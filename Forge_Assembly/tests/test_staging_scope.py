import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import batch_engine as be,mod_manager as mm,layered_pipeline as lp
import recovery_snapshot as recovery,deployment_policy as policy,auto_setup_gui as auto

class StagingScopeTests(unittest.TestCase):
    def fixture(self,root):
        game=root/'game';game.mkdir()
        files={name:game/(name+'.forge') for name in ('A','B')}
        old=root/'old';old.mkdir();rows=[]
        for name,target in files.items():
            target.write_bytes(('installed-'+name).encode())
            backup=old/target.name;backup.write_bytes(('clean-'+name).encode())
            rows.append(dict(target=str(target),backup=str(backup),originalSha256=be.sha256(backup),installedSha256=be.sha256(target)))
        receipt=old/'INSTALL.json';be.record_json(receipt,dict(gameRoot=str(game),files=rows))
        baseline=dict(gameRoot=str(game),hashes={r['target']:r['originalSha256'] for r in rows})
        return game,files,receipt,baseline

    def test_partial_receipt_follows_manifest_history_for_untouched_originals(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game,files,old,baseline=self.fixture(root)
            partial=root/'partial';partial.mkdir();target=files['A'];target.write_bytes(b'new-A')
            backup=partial/target.name;backup.write_bytes(b'clean-A')
            manifest=partial/'BATCH.json';be.record_json(manifest,dict(plan=dict(gameRoot=str(game)),outputs={str(target):dict(previousTransaction=str(old))}))
            tx=partial/'INSTALL.json';be.record_json(tx,dict(gameRoot=str(game),batchManifest=str(manifest),files=[dict(target=str(target),backup=str(backup),originalSha256=be.sha256(backup),installedSha256=be.sha256(target))]))
            with patch.object(policy,'WORK',root),patch.object(mm,'load',return_value=dict(last_transaction=str(tx))):
                sources,previous=lp.baseline_sources(game,baseline)
            self.assertEqual(sources[str(files['B'])],old.parent/files['B'].name)
            self.assertEqual(previous[str(files['B'])]['previousTransaction'],str(old))
            self.assertNotIn('previousFoundationOnly',previous[str(files['B'])])
            (old.parent/files['B'].name).write_bytes(b'corrupt')
            with patch.object(policy,'WORK',root),patch.object(mm,'load',return_value=dict(last_transaction=str(tx))):
                with self.assertRaisesRegex(ValueError,'backup missing/changed'):lp.baseline_sources(game,baseline)

    def test_partial_recovery_preserves_untouched_modified_forge(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game,files,old,baseline=self.fixture(root)
            target=files['A'];manifest=root/'BATCH.json'
            be.record_json(manifest,dict(plan=dict(gameRoot=str(game)),outputs={str(target):dict(sourceSha256=baseline['hashes'][str(target)],stagedSha256=be.sha256(target))}))
            with patch.object(policy,'WORK',root),patch.object(policy,'require_ready',return_value=baseline),patch.object(mm,'load',return_value=dict(last_transaction=str(old))):
                result=recovery.create_layered(manifest,old,root/'backups')
            state=json.loads(result.read_text());self.assertEqual(set(state['installed']),{str(p) for p in files.values()})
            be.restore_install(result,game_check=lambda:[])
            self.assertEqual(files['B'].read_bytes(),b'clean-B')

    def test_diagnostic_is_private_and_rejected_before_game_checks(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);manifest=root/'BATCH.json';be.record_json(manifest,{})
            report=dict(batch=str(manifest))
            with patch.object(auto,'_perform',return_value=(root,report)):
                _,result=auto.perform(build_scope='DIAGNOSTIC_SINGLE_MOD')
            state=json.loads(manifest.read_text());self.assertTrue(state['privateOnly']);self.assertFalse(result['installable'])
            with patch.object(policy,'require_ready') as ready:
                with self.assertRaisesRegex(ValueError,'Diagnostic batches'):policy.require_install(state,manifest)
                ready.assert_not_called()
            with self.assertRaisesRegex(ValueError,'cannot be installed'):auto.perform(build_scope='DIAGNOSTIC_SINGLE_MOD',install=True)

if __name__=='__main__':unittest.main()
