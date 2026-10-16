import json,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import mod_manager as mm,mod_settings as catalog,friendly_errors,build_package


class ModSettingsTests(unittest.TestCase):
 def archive(self,root,name,members):
  path=root/name
  with zipfile.ZipFile(path,'w') as z:
   for member in members:z.writestr(member,b'fixture')
  return path
 def test_folder_presence_enabled_state_and_supported_variants(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);cfg={'archive_dir':str(root)}
   self.archive(root,'Nightmare Mode-1102-1.zip',['New Reinforcements/A.data','Traffic Improvements/A.data'])
   self.archive(root,'WABO-1585-1.zip',['WABO/4_Realistic (Hardcore)/1.GR_WeaponDBEntry'])
   thermal=self.archive(root,'Pathfinder-1159-1.zip',['A.data'])
   state=mm.scan_archives(root)
   def keys():return {r[0]:r[2] for r in catalog.options(catalog.present_mods(cfg,state))}
   values=keys();self.assertIn('nightmare_caller_priority',values)
   self.assertEqual(values['wabo_difficulty'],['4_Realistic (Hardcore)'])
   self.assertEqual(values['thermal_priority'],['Standard night vision and thermal','Pathfinder white thermal vision'])
   self.assertNotIn('weapon_damage_priority',values);self.assertNotIn('bcm_handguard',values)
   thermal.unlink();self.assertNotIn('thermal_priority',keys())
   state['Nightmare Mode-1102-1.zip']['enabled']=False
   self.assertNotIn('nightmare_caller_priority',keys())
 def test_no_gameplay_controls_for_helper_or_cover_only(self):
  mods={'WABO Compatibility Tool-1585-1.zip':{'members':['Tool.exe']},'Disabled cover system-537-1.rar':{'members':['Cover.Type']}}
  self.assertEqual(catalog.options(mods),[])
 def test_learned_variants_survive_later_builds_but_not_removed_members_or_mods(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);mods=root/'mods';mods.mkdir();path=self.archive(mods,'Test.zip',['Default/A.data','Optional/A.data'])
   cfg={'archive_dir':str(mods)};state=mm.scan_archives(mods)
   candidates=[dict(mod='Test.zip',member=m,archiveSha256='a'*64) for m in state['Test.zip']['members']]
   run=root/'auto_runs/old';run.mkdir(parents=True)
   (run/'CONFLICT_POLICY_REPORT.json').write_text(json.dumps({'groups':[dict(target='forge||#1:2',state='VERIFIED_COMPOSED',candidates=candidates)]}))
   later=root/'auto_runs/new';later.mkdir();(later/'CONFLICT_POLICY_REPORT.json').write_text('{"groups": []}')
   self.assertEqual(len(catalog.learned_groups(catalog.present_mods(cfg,state),root)),1)
   state['Test.zip']['enabled']=False
   self.assertEqual(catalog.learned_groups(catalog.present_mods(cfg,state),root),[])
   state['Test.zip']['enabled']=True;state['Test.zip']['members'].pop()
   self.assertEqual(catalog.learned_groups(catalog.present_mods(cfg,state),root),[])
   path.unlink();self.assertEqual(catalog.learned_groups(catalog.present_mods(cfg,state),root),[])
 def test_changed_archive_inventory_is_not_reused(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);path=self.archive(root,'Test.zip',['Old/file.data']);state=mm.scan_archives(root)
   path.write_bytes(b'new archive with a different size')
   self.assertEqual(catalog.present_mods({'archive_dir':str(root)},state)['Test.zip']['members'],[])


class FriendlyErrorTests(unittest.TestCase):
 def test_bridge_error_identifies_mod_and_saves_full_details(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);stage=root/'auto_runs/run/CompleteContents/5';stage.mkdir(parents=True)
   proof=stage/'PROOF.json';stack='System.IO.IOException: WHOLE_DATA_ID_NAME_MISMATCH: BCM.Mesh\n at WholeDataMerge.Validate()'
   proof.write_text(json.dumps({'verified':False,'error':stack}))
   (stage/'REQUEST.json').write_text(json.dumps([dict(mod='BCM Koblin.zip',member='A.data')]))
   raw='Complete content composition failed:\n'+str(proof)+'\n'+stack
   title,message,report=friendly_errors.explain(raw,root)
   self.assertIn('compatibility',title);self.assertIn('BCM Koblin.zip',message);self.assertIn('BCM.Mesh',message)
   self.assertNotIn('System.IO',message);self.assertNotIn(' at WholeDataMerge',message)
   self.assertEqual(json.loads(report.read_text())['technicalDetails'],raw)
 def test_unknown_error_preserved_and_conflict_explained(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);raw='Unexpected edge condition\nfull stack'
   title,message,report=friendly_errors.explain(raw,root)
   self.assertNotIn('full stack',message);self.assertEqual(json.loads(report.read_text())['technicalDetails'],raw)
   title,message,_=friendly_errors.explain('SAME_ARCHIVE_CONTAINER_VARIANTS_NEED_EXACT_CHOICE',root)
   self.assertIn('Choose',title);self.assertIn('Conflicts & new assets',message)


class PortablePackageTests(unittest.TestCase):
 def test_package_omits_personal_preferences_and_toolkit_path(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)/'App';root.mkdir();(root/'START_FORGE_ASSEMBLY.cmd').write_text('fixture')
   (root/'mod_settings.py').write_text('# fixture')
   (root/'workspace').mkdir();(root/'data').mkdir()
   for path in ('workspace/settings.json','workspace/additional_options.json','data/local_toolkit.json'):(root/path).write_text('{"personal": true}')
   with patch.object(build_package,'ROOT',root):archive=build_package.package()
   with zipfile.ZipFile(archive) as z:
    names=z.namelist();self.assertIn('Forge_Assembly/mod_settings.py',names)
    self.assertFalse(any(n.endswith(('settings.json','additional_options.json','local_toolkit.json')) for n in names))


if __name__=='__main__':unittest.main()
