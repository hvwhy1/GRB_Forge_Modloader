"""Install/restore transaction checks using an isolated temporary game folder."""
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import batch_engine


class InstallFlowTests(unittest.TestCase):
    def install_fixture(self, manifest, backups):
        import deployment_policy as policy
        state=json.loads(manifest.read_text())
        state.update(productVersion=policy.VERSION,baselineId='fixture-baseline')
        manifest.write_text(json.dumps(state))
        baseline=manifest.parent/'RECOVERY_FIXTURE.json'
        baseline.write_text(json.dumps({'installationPaused':False,'baselineId':'fixture-baseline',
            'gameRoot':state['plan']['gameRoot'],
            'hashes':{k:v['sourceSha256'] for k,v in state['outputs'].items()}}))
        with patch.object(policy,'WORK',manifest.parent),patch.object(policy,'STATE',baseline):
            return batch_engine.install_staged(manifest,backups,game_check=lambda:[])

    def test_install_and_restore_need_no_typed_phrase(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            game = root / 'game'
            stage = root / 'stage'
            backups = root / 'backups'
            game.mkdir()
            stage.mkdir()
            (game / 'GRB.exe').write_bytes(b'game marker')
            original = game / 'DataPC_patch_01.forge'
            staged = stage / original.name
            original_bytes = b'verified original bytes'
            staged_bytes = b'verified staged bytes'
            original.write_bytes(original_bytes)
            staged.write_bytes(staged_bytes)
            digest = lambda b: hashlib.sha256(b).hexdigest()
            manifest = stage / 'BATCH_RESULT.json'
            manifest.write_text(json.dumps({
                'status': 'VERIFIED_STAGED',
                'plan': {'gameRoot': str(game)},
                'outputs': {str(original.resolve()): {
                    'staged': str(staged),
                    'sourceSha256': digest(original_bytes),
                    'stagedSha256': digest(staged_bytes),
                }},
            }), encoding='utf-8')

            transaction = self.install_fixture(manifest, backups)
            self.assertEqual(original.read_bytes(), staged_bytes)
            tx = json.loads(Path(transaction).read_text(encoding='utf-8'))
            self.assertEqual(tx['status'], 'INSTALLED')
            self.assertEqual(Path(tx['files'][0]['backup']).read_bytes(), original_bytes)

            batch_engine.restore_install(transaction, game_check=lambda: [])
            self.assertEqual(original.read_bytes(), original_bytes)

    def test_install_accepts_already_staged_forge_and_skips_rewrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            game = root / 'game'
            stage = root / 'stage'
            game.mkdir()
            stage.mkdir()
            (game / 'GRB.exe').write_bytes(b'game marker')
            target = game / 'DataPC_Resources.forge'
            staged = stage / target.name
            staged_bytes = b'already installed verified bytes'
            target.write_bytes(staged_bytes)
            staged.write_bytes(staged_bytes)
            digest = lambda b: hashlib.sha256(b).hexdigest()
            manifest = stage / 'BATCH_RESULT.json'
            manifest.write_text(json.dumps({
                'status': 'VERIFIED_STAGED',
                'plan': {'gameRoot': str(game)},
                'outputs': {str(target.resolve()): {
                    'staged': str(staged),
                    'sourceSha256': digest(b'older source bytes'),
                    'stagedSha256': digest(staged_bytes),
                }},
            }), encoding='utf-8')

            transaction = self.install_fixture(manifest, root / 'backups')
            self.assertEqual(target.read_bytes(), staged_bytes)
            tx = json.loads(Path(transaction).read_text(encoding='utf-8'))
            self.assertEqual(tx['status'], 'INSTALLED')
            self.assertEqual(tx['files'], [])
            self.assertEqual(tx['alreadyStaged'], [str(target.resolve())])


if __name__ == '__main__':
    unittest.main()
