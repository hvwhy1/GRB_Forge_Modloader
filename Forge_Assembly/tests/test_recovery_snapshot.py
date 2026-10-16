import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import batch_engine as be
import deployment_policy as policy
import recovery_snapshot


class RecoverySnapshotTests(unittest.TestCase):
    def fixture(self,root):
        game=root/'game';game.mkdir();target=game/'Test.forge';target.write_bytes(b'modified')
        origin=root/'original_backup';origin.mkdir();backup=origin/'Test.forge';backup.write_bytes(b'original')
        digest=lambda b:hashlib.sha256(b).hexdigest()
        transaction=origin/'INSTALL_TRANSACTION.json'
        transaction.write_text(json.dumps({'gameRoot':str(game),'files':[{'target':str(target),
            'backup':str(backup),'originalSha256':digest(b'original')}]}))
        manifest=root/'READY_BATCH.json'
        manifest.write_text(json.dumps({'status':'VERIFIED_STAGED','plan':{'gameRoot':str(game)},
            'outputs':{str(target):{'sourceSha256':digest(b'original'),'stagedSha256':digest(b'modified')}}}))
        return game,target,manifest,transaction

    def test_composite_recovery_restores_baseline_after_incremental_trials(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game,target,manifest,transaction=self.fixture(root)
            recovery=recovery_snapshot.create(manifest,transaction,root/'recovery')
            result=be.restore_install(recovery,game_check=lambda:[])
            self.assertEqual(result['status'],'RESTORED')
            self.assertEqual(target.read_bytes(),b'original')

    def test_unknown_live_bytes_cannot_be_registered_as_recoverable_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);_,target,manifest,transaction=self.fixture(root)
            target.write_bytes(b'unknown alteration')
            with self.assertRaisesRegex(ValueError,'Active game bytes'):
                recovery_snapshot.create(manifest,transaction,root/'recovery')
            self.assertFalse((root/'recovery').exists())

    def test_layered_recovery_keeps_runtime_receipt_and_clean_forge_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game,target,manifest,transaction=self.fixture(root)
            tx=json.loads(transaction.read_text());tx['runtimeTransaction']=str(root/'runtime.json');transaction.write_text(json.dumps(tx))
            baseline={'gameRoot':str(game),'hashes':{str(target):be.sha256(Path(tx['files'][0]['backup']))}}
            with patch.object(policy,'require_ready',return_value=baseline):
                receipt=recovery_snapshot.create_layered(manifest,transaction,root/'recovery')
            recovered=json.loads(receipt.read_text())
            self.assertEqual(recovered['runtimeTransaction'],tx['runtimeTransaction'])
            self.assertEqual(recovered['installationTransaction'],str(transaction))
            be.restore_install(receipt,game_check=lambda:[])
            self.assertEqual(target.read_bytes(),b'original')

    def test_compatibility_hold_is_scoped_to_exact_archive_and_member(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);archive=root/'tweak.zip';archive.write_bytes(b'old mod')
            holds=root/'holds.json';holds.write_text(json.dumps({'routes':[{
                'archiveName':archive.name,'archiveSha256':be.sha256(archive),
                'member':'camera/zoom.CameraSelectorNode','reason':'failed startup'}]}))
            with patch.object(policy,'HOLDS',holds):
                self.assertEqual(policy.held_route(archive,'camera/zoom.CameraSelectorNode'),'failed startup')
                self.assertIsNone(policy.held_route(archive,'unrelated.Unit'))
                archive.write_bytes(b'new repaired mod')
                self.assertIsNone(policy.held_route(archive,'camera/zoom.CameraSelectorNode'))


if __name__=='__main__':unittest.main()
