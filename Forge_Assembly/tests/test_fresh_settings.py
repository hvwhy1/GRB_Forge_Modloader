import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import mod_manager as mm,complete_content as cc,batch_engine as be,manual_install_rules as rules

class FreshSettingsTests(unittest.TestCase):
 def test_fresh_and_partial_settings_enable_complete_pipeline_and_selected_profile(self):
  with tempfile.TemporaryDirectory() as d:
   cfg=Path(d)/'settings.json'
   with patch.object(mm,'CONFIG',cfg):
    fresh=mm.load(cfg,{})
    self.assertTrue(fresh['complete_content_pipeline']);self.assertEqual(fresh['wabo_difficulty'],'4_Realistic (Hardcore)')
    self.assertEqual(fresh['shotgun_damage_profile'],'150')
    mm.save(cfg,{'game_root':'custom','wabo_difficulty':'2_Normal'})
    upgraded=mm.load(cfg,{})
    self.assertTrue(upgraded['complete_content_pipeline']);self.assertEqual(upgraded['game_root'],'custom');self.assertEqual(upgraded['wabo_difficulty'],'2_Normal')
    self.assertIsNone(rules.excluded_variant('WABO-1585-1.zip','WABO 1.3.1.2/2_Normal/DBContainer/1_-_ASR_4AC.GR_WeaponDBEntry'))
    self.assertIsNotNone(rules.excluded_variant('WABO-1585-1.zip','WABO 1.3.1.2/4_Realistic (Hardcore)/DBContainer/1_-_ASR_4AC.GR_WeaponDBEntry'))
    mm.save(cfg,{'complete_content_pipeline':False})
    self.assertFalse(mm.load(cfg,{})['complete_content_pipeline'])
    unrelated=Path(d)/'mods.json';self.assertEqual(mm.load(unrelated,{}),{})
 def test_missing_old_machine_helper_path_is_discovered_by_supported_hash(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);helper=root/'WABO Compatibility Tool-1585-1-1-2.zip';helper.write_bytes(b'fixture')
   mods={'helper':dict(archive=str(helper),enabled=True)}
   with patch.object(be,'sha256',return_value='3c2e0f6375407609101850e35dc2997bc06d513dcc434c3cf69b40fc2c2c1f7a'):
    self.assertEqual(cc.resolve_weapon_reader({'wabo_compatibility_tool':str(root/'old machine.zip')},mods),str(helper))
   with self.assertRaisesRegex(ValueError,'supported WABO Compatibility Tool'):cc.resolve_weapon_reader({},mods)
   with self.assertRaisesRegex(ValueError,'supported WABO Compatibility Tool'):cc.resolve_weapon_reader({},dict(helper=dict(archive=str(helper),enabled=False)))
 def test_missing_reader_blocks_fresh_build_before_bridge_or_legacy_fallback(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);cfg=root/'settings.json';item=dict(name='1_-_ASR_4AC.GR_WeaponDBEntry')
   with patch.object(mm,'CONFIG',cfg),patch.object(cc,'inventory',return_value=({}, {('DataPC_patch_01.forge','dbcontainerentry_0x104634f921.data'):[item]})),patch.object(be,'build_bridge',side_effect=AssertionError('Should fail before compiling')):
    with self.assertRaisesRegex(ValueError,'No legacy fallback'):cc.prepare({},root/'view',root/'game',root/'toolkit',root,{},lambda _:None)
   self.assertFalse((root/'game').exists())

if __name__=='__main__':unittest.main()
