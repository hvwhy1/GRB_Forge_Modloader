from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
import config_install as ci
import batch_engine as be

class ConfigurationTests(unittest.TestCase):
    def fixture(self,root):
        game=root/'game';game.mkdir();(game/'GRB.exe').write_bytes(b'marker')
        package=root/'NativePackage';core=package/'core';core.mkdir(parents=True)
        (core/'dinput8.dll').write_bytes(b'native selector');(core/'NativeFirstPersonLauncher.exe').write_bytes(b'launcher')
        (core/'NativeFirstPerson.ini').write_text('[FirstPerson]\nFOV=120\n[Compatibility]\nChainLibrary=\n')
        for role,variant in ci.PRESETS.items():
            folder=package/'options'/role/variant;folder.mkdir(parents=True);(folder/ci.NAMES[role]).write_text('[FirstPerson]\nFOV=120\n')
        (package/'options/renderer/vulkan').mkdir();(package/'options/renderer/vulkan/Renderer.ini').write_text('[Renderer]\nRenderer=Vulkan\n')
        (package/'options/renderer/dx11/d3d11.dll').write_bytes(b'native dx11')
        return game,package
    def test_native_install_and_complete_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game,package=self.fixture(root)
            original=game/'NativeFirstPerson.ini';original.write_bytes(b'old config')
            receipt=ci.install_files(ci.native_plan(package,game),game,root/'backups',game_check=lambda:[])
            self.assertEqual((game/'dinput8.dll').read_bytes(),b'native selector')
            self.assertTrue((game/'NativeFirstPersonOptions/FOV.ini').is_file())
            ci.restore_files(receipt,game_check=lambda:[])
            self.assertEqual(original.read_bytes(),b'old config');self.assertFalse((game/'dinput8.dll').exists())
    def test_dxgi_reshade_stays_separate_and_restorable(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game,package=self.fixture(root)
            (game/'dxgi.dll').write_bytes(b'verified reshade runtime')
            (game/'ReShade.ini').write_text('[GENERAL]\nPresetPath=Nommad.ini\n')
            receipt=ci.install_files(ci.native_plan(package,game),game,root/'backups',game_check=lambda:[])
            self.assertEqual((game/'dxgi.dll').read_bytes(),b'verified reshade runtime')
            self.assertFalse((game/'d3d11_chain.dll').exists())
            self.assertNotIn('ChainLibrary=d3d11_chain.dll',(game/'NativeFirstPerson.ini').read_text())
            self.assertIn('Nommad.ini',(game/'ReShade.ini').read_text())
            ci.restore_files(receipt,game_check=lambda:[])
            self.assertEqual((game/'dxgi.dll').read_bytes(),b'verified reshade runtime')
            self.assertFalse((game/'d3d11.dll').exists())
    def test_two_different_graphics_proxies_are_not_silently_selected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game,package=self.fixture(root)
            (game/'d3d11.dll').write_bytes(b'other graphics proxy');(game/'dxgi.dll').write_bytes(b'reshade')
            import reshade_install
            with patch.object(reshade_install,'addon_runtime',return_value=b'reshade'):
                with self.assertRaisesRegex(ValueError,'Two different'):ci.native_plan(package,game,chain_proxy=True)
            self.assertEqual((game/'d3d11.dll').read_bytes(),b'other graphics proxy');self.assertEqual((game/'dxgi.dll').read_bytes(),b'reshade')
    def test_proxy_collision_and_preserving_chain(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game,package=self.fixture(root);(game/'d3d11.dll').write_bytes(b'reshade proxy')
            with self.assertRaisesRegex(ValueError,'Existing d3d11'):ci.native_plan(package,game)
            receipt=ci.install_files(ci.native_plan(package,game,chain_proxy=True),game,root/'backups',game_check=lambda:[])
            self.assertEqual((game/'d3d11_chain.dll').read_bytes(),b'reshade proxy')
            self.assertIn('ChainLibrary=d3d11_chain.dll',(game/'NativeFirstPerson.ini').read_text())
            ci.restore_files(receipt,game_check=lambda:[]);self.assertEqual((game/'d3d11.dll').read_bytes(),b'reshade proxy')
    def test_master_ini_updates_overriding_presets(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game,package=self.fixture(root)
            rows=ci.ini_plan(package/'core/NativeFirstPerson.ini',game/'NativeFirstPerson.ini','[FirstPerson]\nFOV=110\nToggleKey=Home\n')
            ci.install_files(rows,game,root/'backups',game_check=lambda:[])
            self.assertIn('FOV=110',(game/'NativeFirstPersonOptions/FOV.ini').read_text())
            self.assertIn('ToggleKey=Home',(game/'NativeFirstPersonOptions/Input.ini').read_text())

    def test_graphics_compatibility_preserves_proxy_and_camera_controls(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game,package=self.fixture(root)
            (game/'d3d11.dll').write_bytes(b'foreign wrapper')
            rows=ci.native_plan(package,game,chain_proxy=True,numeric={'forward_offset':'0.23'},toggle_key='F9',graphics_compatibility=True)
            receipt=ci.install_files(rows,game,root/'backups',game_check=lambda:[])
            text=(game/'NativeFirstPerson.ini').read_text()
            self.assertIn('DisableLocomotionDOF=false',text)
            self.assertIn('DisableLocomotionAutoFocus=false',text)
            self.assertIn('ChainLibrary=d3d11_chain.dll',text)
            self.assertIn('CameraForwardOffset=0.23',(game/'NativeFirstPersonOptions/Camera.ini').read_text())
            self.assertIn('ToggleKey=F9',(game/'NativeFirstPersonOptions/Input.ini').read_text())
            ci.restore_files(receipt,game_check=lambda:[])
            self.assertEqual((game/'d3d11.dll').read_bytes(),b'foreign wrapper')

    def test_single_dx11_loader_removes_owned_selector_and_restores(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game,package=self.fixture(root)
            (game/'dinput8.dll').write_bytes((package/'core/dinput8.dll').read_bytes())
            (game/'dxgi.dll').write_bytes(b'existing reshade')
            receipt=ci.install_files(ci.native_plan(package,game,dx11_single_loader=True),game,root/'backups',game_check=lambda:[])
            self.assertFalse((game/'dinput8.dll').exists())
            self.assertEqual((game/'d3d11.dll').read_bytes(),b'native dx11')
            self.assertEqual((game/'dxgi.dll').read_bytes(),b'existing reshade')
            ci.restore_files(receipt,game_check=lambda:[])
            self.assertEqual((game/'dinput8.dll').read_bytes(),b'native selector')

    def test_single_loader_rejects_vulkan_and_foreign_selector(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game,package=self.fixture(root)
            with self.assertRaisesRegex(ValueError,'requires DX11'):ci.native_plan(package,game,{'renderer':'vulkan'},dx11_single_loader=True)
            (game/'dinput8.dll').write_bytes(b'foreign selector')
            with self.assertRaisesRegex(ValueError,'Foreign dinput8'):ci.native_plan(package,game,dx11_single_loader=True)
            self.assertEqual((game/'dinput8.dll').read_bytes(),b'foreign selector')
    def test_finder_and_destination_preserve_option_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game,package=self.fixture(root)
            (root/'workspace').mkdir();(root/'workspace/not_a_config.ini').write_text('ignored')
            found=ci.find_inis([root]);self.assertNotIn(root/'workspace/not_a_config.ini',found)
            self.assertEqual(ci.find_native_packages([root]),[package])
            source=package/'options/fov/120/FOV.ini';self.assertEqual(ci.ini_destination(source,game),game/'NativeFirstPersonOptions/FOV.ini')
    def test_running_game_and_outside_path_block_before_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game,package=self.fixture(root)
            rows=ci.ini_plan(package/'core/NativeFirstPerson.ini',root/'outside.ini','[FirstPerson]\nFOV=110\n')
            with self.assertRaisesRegex(ValueError,'destination'):ci.install_files(rows,game,root/'backups',game_check=lambda:[])
            with self.assertRaisesRegex(RuntimeError,'Close'):ci.install_files([],game,root/'backups',game_check=lambda:['GRB.exe'])
            self.assertFalse((root/'outside.ini').exists());self.assertFalse((root/'backups').exists())
    def test_failure_rolls_back_prior_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game,package=self.fixture(root);a=game/'A.ini';a.write_bytes(b'original')
            rows=[{'target':str(a),'content':'new'}, {'target':str(game/'B.ini'),'content':'second'}]
            import os
            actual=os.replace
            def fail_second(src,dst):
                if Path(dst)==game/'B.ini':raise OSError('simulated swap failure')
                return actual(src,dst)
            with patch.object(ci.os,'replace',side_effect=fail_second):
                with self.assertRaises(OSError):ci.install_files(rows,game,root/'backups',game_check=lambda:[])
            self.assertEqual(a.read_bytes(),b'original');self.assertFalse((game/'B.ini').exists())
    def test_audio_package_streaming_and_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game,package=self.fixture(root)
            source=root/'soundspc_sfx_wea_.pck';source.write_bytes(b'AKPKnew audio')
            target=game/'sounddata/pc'/source.name;target.parent.mkdir(parents=True);target.write_bytes(b'AKPKoriginal')
            with patch.object(ci,'PACKAGE_LIMIT',4):
                receipt=ci.install_files([{'source':str(source),'target':str(target)}],game,root/'backups',game_check=lambda:[])
            self.assertEqual(target.read_bytes(),source.read_bytes())
            ci.restore_files(receipt,game_check=lambda:[]);self.assertEqual(target.read_bytes(),b'AKPKoriginal')
    def test_audio_wrong_destination_and_header_block_before_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game,package=self.fixture(root);source=root/'audio.pck';source.write_bytes(b'AKPKaudio')
            with self.assertRaisesRegex(ValueError,'destination'):
                ci.install_files([{'source':str(source),'target':str(game/'audio.pck')}],game,root/'backups',game_check=lambda:[])
            source.write_bytes(b'not audio')
            with self.assertRaisesRegex(ValueError,'AKPK'):
                ci.install_files([{'source':str(source),'target':str(game/'sounddata/pc/audio.pck')}],game,root/'backups',game_check=lambda:[])
            self.assertFalse((root/'backups').exists())
    def test_renderer_change_removes_only_own_dx11_loader(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game,package=self.fixture(root)
            (game/'d3d11.dll').write_bytes(b'native dx11')
            plan=ci.native_plan(package,game,{'renderer':'vulkan'})
            self.assertTrue(any(r.get('remove') for r in plan))
            (game/'d3d11.dll').write_bytes(b'other proxy')
            self.assertFalse(any(r.get('remove') for r in ci.native_plan(package,game,{'renderer':'vulkan'})))

if __name__=='__main__':unittest.main()
