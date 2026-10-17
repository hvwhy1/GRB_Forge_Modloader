import json,struct,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import mod_manager as mm,batch_engine as be,manual_install_rules as rules
from gameplay_options import wabo_variant
from shotgun_override import apply
from test_shotgun_override import prefix_reader
from test_config_install import ConfigurationTests
import runtime_stage as runtime
native_fixture=ConfigurationTests.fixture
del ConfigurationTests

class GameplayOptionsTests(unittest.TestCase):
    def test_explicit_folder_aliases_keep_container_identity(self):
        from route_conventions import parse_explicit_parent
        root=parse_explicit_parent('Mod/DataPCPatch01/Black Handguard/1_-_WG_Koblin.data')
        self.assertEqual((root['forge'],root['data'],root['kind']),('DataPC_patch_01.forge','1_-_WG_Koblin.data','data'))
        inner=parse_explicit_parent('Mod/DataPCPatch01/DBContainer/1_-_Unique_Koblin.GR_WeaponDBEntry')
        self.assertEqual(inner['data'],'DBContainerEntry_0X104634F921.data')
        self.assertEqual(inner['kind'],'inner')
        self.assertEqual(parse_explicit_parent('Mod/ExtraPatch01/1_-_WG_Koblin.data')['forge'],'DataPC_extra_patch_01.forge')
        self.assertIsNone(parse_explicit_parent('Mod/UnknownPatch01/1_-_WG_Koblin.data'))
    def test_disabled_mod_choice_survives_default_selection(self):
        from auto_setup_gui import choose_default_flags
        key='BCM Koblin-1807-Complete-1774862891.7z'
        selected=choose_default_flags({key:{'enabled':False,'_enabled_by_user':True}})
        self.assertFalse(selected[key]['enabled'])

    def test_vanilla_ammo_and_suppressors_do_not_change_hardcore_health_or_ballistics(self):
        cfg=dict(wabo_difficulty='4_Realistic (Hardcore)',ammo_capacity_profile='Vanilla',suppressor_profile='Vanilla-like')
        base='WABO/4_Realistic (Hardcore)/DBContainer/'
        self.assertIsNotNone(wabo_variant(base+'1_-_DBAmmoType_5_56.DBAmmoType',cfg))
        self.assertIsNotNone(wabo_variant(base+'1_-_Muzzle_SIL_ASR_556.MuzzleAttachmentDBEntry',cfg))
        self.assertIsNone(wabo_variant(base+'1_-_ASR_4AC.GR_WeaponDBEntry',cfg))
        self.assertIsNotNone(wabo_variant(base+'1_-_Muzzle_STD_SMG_9mm_NODATA_UMPCQC.MuzzleAttachmentDBEntry',cfg))
        self.assertIsNone(wabo_variant('WABO/5_Custom/Optional/Suppressors/1 Easy (Vanilla)/1_-_Muzzle_STD_SMG_9mm_NODATA_UMPCQC.MuzzleAttachmentDBEntry',cfg))
        self.assertIsNone(wabo_variant(base+'1_-_DBNpcHealth_Default.DBNpcHealth',cfg))
        self.assertIsNone(wabo_variant('WABO/5_Custom/Optional/Ammo Presets/Easy (Vanilla)/1_-_DBAmmoType_5_56.DBAmmoType',cfg))
        self.assertIsNone(wabo_variant('WABO/5_Custom/Optional/Ammo Multipliers/Vanilla_x1,25/DBContainer/1_-_DBPerkLIH_AmmoMax1.DBPerk',cfg))
        self.assertIsNone(wabo_variant('WABO/5_Custom/Optional/Suppressors/1 Easy (Vanilla)/1_-_Muzzle_SIL_ASR_556.MuzzleAttachmentDBEntry',cfg))
        self.assertIsNotNone(wabo_variant('WABO/5_Custom/Optional/Suppressors/4 Realistic/1_-_Muzzle_SIL_ASR_556.MuzzleAttachmentDBEntry',cfg))
    def test_difficulty_presets_stay_selected_without_overrides(self):
        cfg=dict(wabo_difficulty='4_Realistic (Hardcore)')
        self.assertIsNone(wabo_variant('WABO/4_Realistic (Hardcore)/DBContainer/1_-_DBAmmoType_5_56.DBAmmoType',cfg))
        self.assertIsNotNone(wabo_variant('WABO/1_Easy/DBContainer/1_-_DBAmmoType_5_56.DBAmmoType',cfg))
    def test_normal_suppressors_include_mandatory_core_and_preserve_hardcore_weapons(self):
        cfg=dict(wabo_difficulty='4_Realistic (Hardcore)',suppressor_profile='Normal')
        custom='WABO/5_Custom/Optional/Suppressors/'
        for name in ('1_-_Muzzle_STD_ASR_556.MuzzleAttachmentDBEntry','1_-_DBWeaponAttachmentUIConfig_Silencer_ASR.DBWeaponAttachmentUIConfig'):
            self.assertIsNone(wabo_variant(custom+'0 Core/'+name,cfg))
            self.assertIsNotNone(wabo_variant('WABO/4_Realistic (Hardcore)/'+name,cfg))
        self.assertIsNone(wabo_variant(custom+'2 Normal/1_-_Muzzle_SIL_ASR_556.MuzzleAttachmentDBEntry',cfg))
        self.assertIsNotNone(wabo_variant(custom+'4 Realistic/1_-_Muzzle_SIL_ASR_556.MuzzleAttachmentDBEntry',cfg))
        self.assertIsNone(wabo_variant('WABO/4_Realistic (Hardcore)/1_-_ASR_4AC.GR_WeaponDBEntry',cfg))
        self.assertIsNone(wabo_variant('WABO/4_Realistic (Hardcore)/1_-_DBNpcHealth_Default.DBNpcHealth',cfg))
    def test_each_suppressor_preset_selects_exactly_its_authored_branch(self):
        from gameplay_options import SUPPRESSOR_PRESETS
        for selected,folder in SUPPRESSOR_PRESETS.items():
            cfg=dict(wabo_difficulty='4_Realistic (Hardcore)',suppressor_profile=selected)
            for other in SUPPRESSOR_PRESETS.values():
                reason=wabo_variant('WABO/5_Custom/Optional/Suppressors/'+other+'/1_-_Muzzle_SIL_SMG_9mm.MuzzleAttachmentDBEntry',cfg)
                self.assertEqual(reason is None,folder==other)
    def test_incomplete_suppressor_preset_cannot_silently_remove_existing_muzzles(self):
        from gameplay_options import validate_suppressor_package
        cfg={'suppressor_profile':'Normal'}
        with self.assertRaisesRegex(ValueError,'requires both'):validate_suppressor_package(['WABO/5_Custom/Optional/Suppressors/2 Normal/Muzzle.Type'],cfg)
        validate_suppressor_package(['WABO/5_Custom/Optional/Suppressors/0 Core/Muzzle.Type','WABO/5_Custom/Optional/Suppressors/2 Normal/Muzzle.Type'],cfg)
    def test_held_bootstrap_wheel_cannot_leave_half_installed_syringe(self):
        member='Item wheel swap/Syringe and Binoculars/Put in DBContainerEntry_0X104634F921.data/8_-_DBItemSetting_HealingItemEssence_HealingSyringes.DBToolSetting'
        with patch.object(mm,'load',return_value={'item_wheel_variant':'Syringe and Binoculars'}),patch('deployment_policy.held_route',return_value='startup crash'):
            self.assertIn('Coupled item-wheel',rules.excluded_variant('Various Tweaks-537-1.rar',member))
    def test_npc_option_does_not_replace_shared_player_trigger(self):
        with patch.object(mm,'load',return_value={'preserve_player_automatic_fire':True}):
            reason=rules.excluded_variant('Various Tweaks-537-1.rar','Single trigger for NPC/Put in DBContainerEntry.data/55_-_TriggerAttachment_FullAuto_Burst3.TriggerAttachmentDBEntry')
        self.assertIn('automatic fire',reason)
    def test_vehicle_profile_excludes_coupled_npc_aac_changes(self):
        with patch.object(mm,'load',return_value={'npc_trigger_priority':'Vanilla automatic fire'}):
            self.assertIsNotNone(rules.excluded_variant('Various Tweaks-537-1.rar','Single trigger for NPC/Put in DBContainerEntry.data/17_-_DBWeaponSmith_AttachmentPerWeaponSetting_AAC.DBWeaponSmith_AttachmentPerWeaponSetting'))
    def test_author_reader_retains_weapon_name_with_short_private_filename(self):
        import types
        from weapon_stats_merge import AuthorReader
        def author_prefix():
            if 'HDG' in func.filename:typesearch('pistol damage',0xAABBCCDD,'u32',1)
            else:typesearch('wrong weapon class',0xFFFFFFFF,'u32',1)
            statset(4,'damage','u16')
        reader=object.__new__(AuthorReader);reader.sha='test';reader.db=types.SimpleNamespace();reader.reader=author_prefix.__code__
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'000000000000007B.GR_WeaponDBEntry';raw=bytearray(150);struct.pack_into('<Q',raw,1,123);struct.pack_into('<IH',raw,40,0xAABBCCDD,35);path.write_bytes(raw)
            with self.assertRaises(ValueError):reader.read(path,{'damage'})
            reader.canonical_names={123:'HDG_P45T.GR_WeaponDBEntry'}
            self.assertEqual(reader.read(path,{'damage'})['fields']['damage']['value'],35)
    def test_150_shotgun_overlay_preserves_custom_ammo_and_model_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);base=root/'STG_M4.GR_WeaponDBEntry';out=root/'patched.GR_WeaponDBEntry'
            data=bytearray(range(150));struct.pack_into('<Q',data,1,123);struct.pack_into('<IH',data,40,0xAABBCCDD,40);base.write_bytes(data)
            apply(prefix_reader(),base,out,150)
            self.assertEqual(struct.unpack_from('<H',out.read_bytes(),44)[0],150)
            self.assertEqual(out.read_bytes()[:44],data[:44]);self.assertEqual(out.read_bytes()[46:],data[46:])

class RuntimeGameplayTests(unittest.TestCase):
    fixture=native_fixture
    def test_explicit_ads_fov_survives_sensitivity_preservation_and_direct_install(self):
        import config_install as ci
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game,package=self.fixture(root);run=root/'stage';run.mkdir()
            choices={'adsfov':'95'}
            plan=ci.native_plan(package,game,choices,dx11_single_loader=True)
            for name in ('NativeFirstPerson.ini','ADSFOV.ini'):
                self.assertIn('ADSFOV=95',next(r['content'] for r in plan if Path(r['target']).name==name))
            cfg={'native_package':str(package),'native_preset_choices':choices,'native_dx11_single_loader':True,'preserve_game_sensitivity':True}
            with patch.object(mm,'CONFIG',root/'settings.json'):
                mm.save(mm.CONFIG,cfg);runtime.prepare({},game,run,notify=lambda _:None)
            for path in (run/'Runtime/Payloads/NativeFirstPerson.ini',run/'Runtime/Payloads/NativeFirstPersonOptions/ADSFOV.ini'):
                self.assertIn('ADSFOV=95',path.read_text())
    def test_game_optic_fov_is_preserved_in_master_and_helper(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game,package=self.fixture(root);run=root/'stage';run.mkdir()
            cfg={'native_package':str(package),'native_dx11_single_loader':True,'preserve_game_sensitivity':True}
            with patch.object(mm,'CONFIG',root/'settings.json'):
                mm.save(mm.CONFIG,cfg);receipt,_=runtime.prepare({},game,run,notify=lambda _:None)
            for path in (run/'Runtime/Payloads/NativeFirstPerson.ini',run/'Runtime/Payloads/NativeFirstPersonOptions/ADSFOV.ini'):
                self.assertIn('ADSFOV=0',path.read_text())
    def test_home_toggle_avoids_reshade_overlay_and_disabled_addon_is_staged_for_removal(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game,package=self.fixture(root);run=root/'stage';run.mkdir()
            addon=game/'ShaderToggler.addon64';addon.write_bytes(b'addon')
            ini=root/'ReShade.ini';ini.write_text('[INPUT]\nKeyOverlay=36,0,0,0\n')
            cfg={'native_package':str(package),'native_dx11_single_loader':True,'native_toggle_key':'Home','reshade_setup':'fixture','reshade_shader_folder':'fixture'}
            with patch.object(mm,'CONFIG',root/'settings.json'),patch('reshade_install.prepare',return_value=[dict(target=str(game/'ReShade.ini'),content=ini.read_text(),encoding='utf-8')]):
                mm.save(mm.CONFIG,cfg);receipt,_=runtime.prepare({},game,run,notify=lambda _:None)
            state=runtime.inspect(receipt,game,run)
            self.assertIn('KeyOverlay=36,0,1,0',(run/'Runtime/Payloads/ReShade.ini').read_text())
            self.assertTrue(next(r for r in state['files'] if r['target']==str(addon))['remove'])
            self.assertEqual(addon.read_bytes(),b'addon')
    def test_absent_disabled_addon_does_not_break_fresh_install(self):
        import config_install as ci
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game=root/'game';game.mkdir();(game/'GRB.exe').write_bytes(b'game')
            receipt=ci.install_files([dict(target=str(game/'ShaderToggler.addon64'),remove=True,expectedSha256='previous-addon-hash')],game,root/'backups',game_check=lambda:[])
            self.assertEqual(json.loads(receipt.read_text())['files'],[])
            target=game/'ShaderToggler.addon64';target.write_bytes(b'changed addon')
            with self.assertRaisesRegex(ValueError,'refusing removal'):
                ci.install_files([dict(target=str(target),remove=True,expectedSha256='previous-addon-hash')],game,root/'backups',game_check=lambda:[])
            self.assertEqual(target.read_bytes(),b'changed addon')
    def test_audio_is_staged_even_when_already_installed_and_later_changed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game=root/'game';game.mkdir();run=root/'stage';run.mkdir();archive=root/'audio.7z';archive.write_bytes(b'archive')
            payload=root/'soundspc_sfx_wea_.pck';payload.write_bytes(b'AKPKmod sound')
            target=game/'sounddata/pc'/payload.name;target.parent.mkdir(parents=True);target.write_bytes(payload.read_bytes())
            mods={'Sound-1245-1.7z':dict(enabled=True,archive=str(archive),members=[payload.name])}
            with patch.object(mm,'CONFIG',root/'settings.json'),patch.object(mm,'extraction_target',return_value=payload):
                receipt,contributions=runtime.prepare(mods,game,run,notify=lambda _:None)
            (game/'GRB.exe').write_bytes(b'game')
            target.write_bytes(b'vanilla sound')
            state=runtime.inspect(receipt,game,run)
            self.assertEqual(len(contributions),1);self.assertEqual(state['files'][0]['sha256'],be.sha256(payload))
            self.assertEqual(target.read_bytes(),b'vanilla sound')
            receipt=runtime.install(receipt,game,run,root/'backups',game_check=lambda:[])
            self.assertEqual(target.read_bytes(),payload.read_bytes())
            import config_install as ci
            ci.restore_files(receipt,game_check=lambda:[])
            self.assertEqual(target.read_bytes(),b'vanilla sound')

if __name__=='__main__':unittest.main()
