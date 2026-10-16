from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
import runtime_stage as rs,mod_manager as mm,config_install as ci,batch_engine as be,existing_stage
from test_config_install import ConfigurationTests
native_fixture=ConfigurationTests.fixture
del ConfigurationTests


class RuntimeStageTests(unittest.TestCase):
    fixture=native_fixture
    def test_generated_native_stage_freezes_settings_and_installs_without_selector(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game,package=self.fixture(root);run=root/'stage';run.mkdir()
            (package/'options/bodyfollow/disabled/BodyFollow.ini').write_text('[FirstPerson]\nFullBodyFollow=true\n')
            cfg={'native_package':str(package),'native_dx11_single_loader':True,'native_numeric':{'forward_offset':'0.18','clip_distance':'0.04'},'native_toggle_key':'F9'}
            with patch.object(mm,'CONFIG',root/'settings.json'):
                mm.save(mm.CONFIG,cfg);receipt,_=rs.prepare({},game,run,notify=lambda s:None)
            state=rs.inspect(receipt,game,run)
            self.assertFalse(any(r['relative']=='dinput8.dll' for r in state['files']))
            text=(run/'Runtime/Payloads/NativeFirstPerson.ini').read_text()
            self.assertIn('FullBodyFollow=true',text);self.assertIn('CameraForwardOffset=0.18',text)
            self.assertFalse((game/'d3d11.dll').exists())
            rs.install(receipt,game,run,root/'backups',game_check=lambda:[])
            self.assertFalse((game/'dinput8.dll').exists());self.assertTrue((game/'d3d11.dll').exists())
    def test_modified_runtime_is_rejected_before_install(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game,package=self.fixture(root);run=root/'stage';run.mkdir()
            with patch.object(mm,'CONFIG',root/'settings.json'):
                mm.save(mm.CONFIG,{'native_package':str(package),'native_dx11_single_loader':True})
                receipt,_=rs.prepare({},game,run,notify=lambda s:None)
            (run/'Runtime/Payloads/NativeFirstPerson.ini').write_text('tampered')
            with self.assertRaisesRegex(ValueError,'source changed'):rs.install(receipt,game,run,root/'backups',game_check=lambda:[])
            self.assertFalse((root/'backups').exists())
    def test_runtime_failure_rolls_back_actual_forge_swap(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game=root/'game';game.mkdir();(game/'GRB.exe').write_bytes(b'game')
            target=game/'DataPC_patch_01.forge';target.write_bytes(b'original')
            backup=root/'original.forge';backup.write_bytes(b'original');target.write_bytes(b'staged')
            receipt=root/'TX.json';be.record_json(receipt,{'status':'INSTALLED','gameRoot':str(game),'files':[{'target':str(target),'backup':str(backup),'originalSha256':be.sha256(backup),'installedSha256':be.sha256(target)}],'installed':[str(target)]})
            manifest=root/'BATCH.json';manifest.write_text(json.dumps({'runtimeManifest':'runtime.json'}))
            summary={'manifest':str(manifest),'gameRoot':str(game)}
            with patch.object(existing_stage,'inspect_existing_stage',return_value=summary),patch.object(be,'install_staged',return_value=receipt),patch.object(rs,'install',side_effect=OSError('runtime failed')):
                with self.assertRaisesRegex(OSError,'runtime failed'):existing_stage.install_existing(manifest,root/'backups',game_check=lambda:[])
            self.assertEqual(target.read_bytes(),b'original')
