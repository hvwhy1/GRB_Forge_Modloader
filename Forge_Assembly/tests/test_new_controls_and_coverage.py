import io,json,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import config_install as ci
import reshade_install as ri
import mod_coverage as mc
import mod_manager as mm
import test_config_install as fixtures


class NewControlTests(unittest.TestCase):
    def test_custom_toggle_reaches_native_options_and_is_reloaded(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);game,package=fixtures.ConfigurationTests().fixture(root)
            ci.install_files(ci.native_plan(package,game,{'input':'F10'}),game,root/'backup',game_check=lambda:[])
            self.assertIn('ToggleKey=F10',(game/'NativeFirstPersonOptions/Input.ini').read_text())
            self.assertEqual(ci.installed_native_controls(game)['toggle_key'],'F10')
    def test_camera_only_settings_preserve_startup_fov_and_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);game,package=fixtures.ConfigurationTests().fixture(root)
            ci.install_files(ci.native_plan(package,game),game,root/'backup',game_check=lambda:[])
            startup=game/'NativeFirstPersonOptions/Startup.ini';startup.write_text('[FirstPerson]\nEnabledOnLaunch=true\n')
            fov=game/'NativeFirstPersonOptions/FOV.ini';before=fov.read_bytes();runtime=(game/'d3d11.dll').read_bytes()
            rows=ci.native_camera_plan(game,{'forward_offset':'0.325','clip_distance':'0.01'},'k')
            self.assertEqual(len(rows),3);self.assertTrue(all(Path(r['target']).suffix=='.ini' for r in rows))
            receipt=ci.install_files(rows,game,root/'settings-backup',game_check=lambda:[])
            self.assertEqual(ci.installed_native_controls(game),{'forward_offset':'0.325','clip_distance':'0.01','toggle_key':'K'})
            self.assertIn('EnabledOnLaunch=true',startup.read_text());self.assertEqual(fov.read_bytes(),before);self.assertEqual((game/'d3d11.dll').read_bytes(),runtime)
            ci.restore_files(receipt,game_check=lambda:[]);self.assertEqual((game/'d3d11.dll').read_bytes(),runtime)
    def test_toggle_key_validation(self):
        for key in ['Esc','F25','Control+K','0x100','', '../Input.ini']:
            with self.assertRaises(ValueError):ci.validate_toggle_key(key)
        for value,result in [('numpad0','Numpad0'),('f24','F24'),('Home','Home'),('0x60','0x60')]:self.assertEqual(ci.validate_toggle_key(value),result)
    def test_typed_numbers_reach_runtime_option_inis(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);game,package=fixtures.ConfigurationTests().fixture(root)
            receipt=ci.install_files(ci.native_plan(package,game,{'fov':'113.5','adsfov':'68.25'},numeric={'forward_offset':'0.123','clip_distance':'0.006'}),game,root/'backup',game_check=lambda:[])
            for file,value in [('FOV.ini','FOV=113.5'),('ADSFOV.ini','ADSFOV=68.25'),('Camera.ini','CameraForwardOffset=0.123')]:
                self.assertIn(value,(game/'NativeFirstPersonOptions'/file).read_text())
            ci.restore_files(receipt,game_check=lambda:[])
    def test_invalid_numeric_values_fail_before_install(self):
        for values in [{'fov':'nan'},{'adsfov':'inf'},{'fov':'141'},{'adsfov':'10'},{'forward_offset':'-0.1'},{'clip_distance':'0'}]:
            with self.assertRaises(ValueError):ci.validate_native_numbers(values)
    def fixture(self,root):
        game=root/'game';game.mkdir();(game/'GRB.exe').write_bytes(b'fixture')
        setup=root/'ReShade_Setup.exe'
        with zipfile.ZipFile(setup,'w') as z:z.writestr('ReShade32.dll',b'MZ32');z.writestr('ReShade64.dll',b'MZ64')
        return game,setup
    def test_reshade_install_and_restore_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);game,setup=self.fixture(root);preset=root/'preset.ini';preset.write_text('Techniques=Bloom@Bloom.fx\nTechniqueSorting=Unused@Unavailable.fx\n')
            shaders=root/'package/Shaders';shaders.mkdir(parents=True);(shaders/'Bloom.fx').write_text('fixture shader')
            with patch.object(mm,'WORK',root/'work'):
                plan=ri.prepare(setup,game,preset,root/'package',False)
                receipt=ci.install_files(plan,game,root/'backups',game_check=lambda:[])
            self.assertEqual((game/'dxgi.dll').read_bytes(),b'MZ64')
            self.assertIn('PresetPath=.\\DEEF_ReShade_Preset.ini',(game/'ReShade.ini').read_text())
            ci.restore_files(receipt,game_check=lambda:[]);self.assertFalse((game/'dxgi.dll').exists())
    def test_missing_shader_prevents_any_game_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);game,setup=self.fixture(root);preset=root/'preset.ini';preset.write_text('Techniques=Missing@Missing.fx\n')
            with patch.object(mm,'WORK',root/'work'):
                with self.assertRaisesRegex(ValueError,'Missing.fx'):ri.prepare(setup,game,preset,fetch_effects=False)
            self.assertFalse((game/'dxgi.dll').exists())
    def test_shader_traversal_rejected(self):
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:z.writestr('../Shaders/evil.fx',b'bad')
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError,'Unsafe'):ri.extract_shaders(data.getvalue(),Path(directory),'./reshade-shaders/Shaders','./reshade-shaders/Textures')
    def test_foreign_dll_blocks_and_preserves_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);game,setup=self.fixture(root);(game/'dxgi.dll').write_bytes(b'foreign')
            with patch.object(mm,'WORK',root/'work'):
                plan=ri.prepare(setup,game,fetch_effects=False)
                with self.assertRaisesRegex(ValueError,'DLL conflict'):ci.install_files(plan,game,root/'backup',game_check=lambda:[])
            self.assertEqual((game/'dxgi.dll').read_bytes(),b'foreign');self.assertFalse((game/'ReShade.ini').exists())
    def test_native_dxgi_preserves_native_loader_and_restores(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);game,setup=self.fixture(root);package=root/'native';dx=package/'options/renderer/dx11/d3d11.dll';dx.parent.mkdir(parents=True);dx.write_bytes(b'native')
            (game/'d3d11.dll').write_bytes(b'native');(game/'dinput8.dll').write_bytes(b'input');(game/'NativeFirstPerson.ini').write_text('[Compatibility]\nChainLibrary=\n')
            with patch.object(mm,'WORK',root/'work'),patch.object(mm,'load',return_value={'native_package':str(package)}):
                plan=ri.prepare(setup,game,fetch_effects=False)
                receipt=ci.install_files(plan,game,root/'backup',game_check=lambda:[])
            self.assertEqual((game/'d3d11.dll').read_bytes(),b'native');self.assertEqual((game/'dxgi.dll').read_bytes(),b'MZ64')
            self.assertNotIn('ChainLibrary=d3d11_chain.dll',(game/'NativeFirstPerson.ini').read_text())
            ci.restore_files(receipt,game_check=lambda:[]);self.assertFalse((game/'dxgi.dll').exists())
    def test_menu_only_mod_is_incomplete_and_held(self):
        name='516 to KS1-1155-3.zip';rows=[{'archive':name,'member':'UI_ASR_Map.data','status':'VERIFIED_LOCAL_TARGET'},{'archive':name,'member':'WG_KS1.data','status':'UNRESOLVED_TARGET'}]
        report=mc.summarize({'entries':rows},{'finishedRoutes':[{'mod':name,'member':'UI_ASR_Map.data'}]})
        self.assertEqual(report['mods'][0]['state'],'INCOMPLETE');self.assertEqual(report['mods'][0]['menuPayloadsStaged'],1)
        self.assertEqual(mc.hold_incomplete_weapon_packs(rows),[name]);self.assertEqual(rows[0]['status'],'INCOMPLETE_WEAPON_PACK_HELD')
    def test_complete_selected_pack_and_ignored_variant(self):
        name='SPEAR-1125-2.zip';rows=[{'archive':name,'member':'model.data','status':'VERIFIED_LOCAL_TARGET'},{'archive':name,'member':'alternate.data','status':'USER_VARIANT_EXCLUDED'}]
        self.assertFalse(mc.hold_incomplete_weapon_packs(rows))
        report=mc.summarize({'entries':rows},{'finishedRoutes':[{'mod':name,'member':'model.data'}]})
        self.assertEqual(report['mods'][0]['state'],'ALL_SELECTED_PAYLOADS_STAGED')

if __name__=='__main__':unittest.main()
