import tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
import batch_engine as be,layered_pipeline as lp,mod_manager as mm,deployment_policy as policy


class DiagnosticBaselineTests(unittest.TestCase):
    def fixture(self,root):
        game=root/'game';game.mkdir();target=game/'DataPC_patch_01.forge';target.write_bytes(b'trial')
        old=root/'install';old.mkdir();clean=old/'original.forge';clean.write_bytes(b'clean')
        baseline={'hashes':{str(target):be.sha256(clean)}}
        primary=old/'INSTALL.json';be.record_json(primary,{'gameRoot':str(game),'files':[{'target':str(target),'backup':str(clean),'originalSha256':be.sha256(clean),'installedSha256':'older-build'}]})
        trial=root/'trial';trial.mkdir();backup=trial/'old.forge';backup.write_bytes(b'older build')
        candidate=trial/target.name;candidate.write_bytes(b'trial');digest=be.sha256(candidate)
        proof=trial/'PROOF.json';be.record_json(proof,dict(verified=True,sourceUnchanged=True,independentAnvilIndexVerified=True,output=str(candidate),outputSha256=digest))
        receipt=trial/'TX.json';be.record_json(receipt,dict(status='INSTALLED',diagnosticOnly=True,gameRoot=str(game),proof=str(proof),installed=[str(target)],files=[dict(target=str(target),backup=str(backup),originalSha256=be.sha256(backup),installedSha256=digest)]))
        return game,target,clean,baseline,primary,receipt,candidate

    def test_stage_uses_clean_backup_and_tracks_current_diagnostic(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game,target,clean,baseline,primary,receipt,candidate=self.fixture(root)
            with patch.object(policy,'WORK',root),patch.object(mm,'load',return_value={'last_transaction':str(primary),'last_isolation_transaction':str(receipt)}):
                sources,previous=lp.baseline_sources(game,baseline)
            self.assertEqual(sources[str(target)],clean)
            self.assertEqual(previous[str(target)]['previousDiagnosticTransaction'],str(receipt))
            self.assertNotIn('previousFoundationOnly',previous[str(target)])

    def test_changed_diagnostic_candidate_cannot_authorize_current_build(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game,target,clean,baseline,primary,receipt,candidate=self.fixture(root)
            candidate.write_bytes(b'tampered')
            with patch.object(policy,'WORK',root):
                self.assertFalse(lp.verified_diagnostic(receipt,game,str(target),be.sha256(target),True))
            with patch.object(policy,'WORK',root),patch.object(mm,'load',return_value={'last_transaction':str(primary),'last_isolation_transaction':str(receipt)}):
                sources,previous=lp.baseline_sources(game,baseline)
            self.assertTrue(previous[str(target)]['previousFoundationOnly'])
            self.assertNotIn('previousDiagnosticTransaction',previous[str(target)])
