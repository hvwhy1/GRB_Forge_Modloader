import json,struct,tempfile,unittest
from pathlib import Path
import conflict_policy as cp,complete_content as cc,batch_engine as be
from weapon_stats_merge import merge
from test_weapon_tuning import Reader,WeaponTuningTests
fixture=WeaponTuningTests.source
del WeaponTuningTests

class ConflictPolicyTests(unittest.TestCase):
 def items(self,root):
  rows=[]
  for mod,data in [('A',b'a'),('B',b'b')]:
   archive=root/(mod+'.zip');archive.write_bytes(data)
   rows.append(dict(mod=mod,archive=str(archive),archiveSha256=be.sha256(archive),member='folder/1_-_Entry.Type',name='1_-_Entry.Type',payload=str(archive)))
  return rows
 def test_no_order_never_picks_an_arbitrary_winner_and_ties_stay_blocked(self):
  with tempfile.TemporaryDirectory() as d:
   rows=self.items(Path(d));self.assertIsNone(cp.choose(rows,{},'DataPC.forge'))
   cfg={'conflict_priorities':{'mod_order':['B','A']}}
   self.assertEqual(cp.choose(rows,cfg,'DataPC.forge')['mod'],'B')
   self.assertIsNone(cp.choose(rows,{'conflict_priorities':{'mod_order':['B']}},'DataPC.forge'))
   rows[0]['mod']='B';self.assertIsNone(cp.choose(rows,cfg,'DataPC.forge'))
 def test_exact_variant_winner_is_hash_bound_and_outweighs_mod_order(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);rows=self.items(root);key=cp.key('DataPC.forge','1_-_Parent.data',rows[0]['name'])
   cfg={'conflict_priorities':{'mod_order':['B','A'],'resource_winners':{key:cp.candidate(rows[0])}}}
   self.assertEqual(cp.inspect_group(rows,cfg,'DataPC.forge','99_-_Parent.data',root)['mod'],'A')
   rows[0]['archiveSha256']='changed'
   with self.assertRaisesRegex(ValueError,'changed archive'):cp.choose(rows,cfg,'DataPC.forge','Parent.data')
   with self.assertRaisesRegex(ValueError,'absent'):cp.inspect_group(rows[1:],cfg,'DataPC.forge','Parent.data',root)
 def test_changed_priority_invalidates_composition_cache(self):
  with tempfile.TemporaryDirectory() as d:
   rows=self.items(Path(d));req=[dict(name=i['name'],payload=i['payload'],priority=None,mod=i['mod'],member=i['member']) for i in rows]
   old=cc.cache_key('DataPC.forge','source',req)
   req[0]['priority']=1;self.assertNotEqual(old,cc.cache_key('DataPC.forge','source',req))
   self.assertEqual(cp.key('DataPC.forge','1_-_Parent.data','9_-_Entry.Type'),cp.key('DataPC.forge','99_-_Parent.data','4_-_Entry.Type'))
 def test_field_donors_combine_damage_and_fire_rate_without_changing_structure(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);base=fixture(self,root)
   damage=root/'damage.GR_WeaponDBEntry';raw=bytearray(base.read_bytes());struct.pack_into('<H',raw,20,150);damage.write_bytes(raw)
   rate=root/'rate.GR_WeaponDBEntry';raw=bytearray(base.read_bytes());struct.pack_into('<H',raw,60,850);rate.write_bytes(raw)
   rows=[dict(mod='Damage',payload=str(damage)),dict(mod='Ballistics',payload=str(rate))]
   cfg={'conflict_priorities':{'weapon_fields':{'damage':'Damage','fire_rate':'Ballistics'}}}
   # UI fire rate also includes its matching UI stat. Supply that supported
   # field in this synthetic reader at a separate offset for readback checks.
   class Fields(Reader):
    def read(self,path):
     out=super().read(path);out['fields']['ui_rateoffire']=dict(out['fields']['rateoffire']);return out
   donors,report=cp.field_donors(rows,cfg,lambda i:Path(i['payload']))
   output=root/'merged.GR_WeaponDBEntry';proof=merge(Fields(),base,donors,output)
   values=Fields().read(output)['fields'];self.assertEqual(values['damage']['value'],150);self.assertEqual(values['rateoffire']['value'],850)
   self.assertTrue(proof['allOtherBytesPreserved']);self.assertEqual(len(report),2)
   allowed=set(range(20,22))|set(range(60,62));self.assertTrue(all(a==b for i,(a,b) in enumerate(zip(base.read_bytes(),output.read_bytes())) if i not in allowed))
 def test_route_priorities_do_not_promote_unresolved_or_held_resources(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);items=self.items(root);rows=[]
   for i in items:
    rows.append(dict(archive=i['mod'],member=i['member'],status='VERIFIED_LOCAL_TARGET',resolved=dict(forge='DataPC.forge',data='1_-_Parent.data',object=i['name'],kind='inner',sourceArchive=i['archive'])))
   rows.append(dict(archive='Held',member='held',status='COMPATIBILITY_HELD'))
   rows.append(dict(archive='Unknown',member='unknown',status='UNRESOLVED_TARGET'))
   self.assertEqual(cp.apply_route_priorities(rows,{'conflict_priorities':{'mod_order':['B','A']}},root),1)
   self.assertEqual([r['status'] for r in rows],['USER_CONFLICT_EXCLUDED','VERIFIED_LOCAL_TARGET','COMPATIBILITY_HELD','UNRESOLVED_TARGET'])
 def test_exact_choice_survives_verified_author_name_alias(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);rows=self.items(root);rows[1]['name']='1_-_Alias.Type'
   target=cp.key('DataPC.forge','Parent.data',rows[0]['name'])
   cfg={'conflict_priorities':{'resource_winners':{target:cp.candidate(rows[1])}}}
   self.assertEqual(cp.choose(rows,cfg,'DataPC.forge','Parent.data')['mod'],'B')
 def test_stale_selection_and_settings_are_rejected_before_backups_or_writes(self):
  from unittest.mock import patch
  import mod_manager as mm,deployment_policy as policy
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);game=root/'game';game.mkdir();(game/'GRB.exe').write_bytes(b'game');forge=game/'DataPC.forge';forge.write_bytes(b'original')
   work=root/'workspace';work.mkdir();cfg=work/'settings.json';mods=work/'mods.json';baseline=work/'baseline.json';manifest=work/'BATCH_RESULT.json'
   be.record_json(cfg,{})
   be.record_json(mods,{'A':{'enabled':True}})
   be.record_json(baseline,dict(installationPaused=False,baselineId='test',gameRoot=str(game),hashes={str(forge):be.sha256(forge)}))
   state=dict(writer='FORGE_LAYERED_BATCH_V20',status='VERIFIED_STAGED',productVersion=policy.VERSION,baselineId='test',selectedArchives=['B'],buildSettings=cp.build_settings({}),plan={'gameRoot':str(game)},outputs={str(forge):{}})
   with patch.object(mm,'WORK',work),patch.object(mm,'CONFIG',cfg),patch.object(mm,'STATE',mods),patch.object(policy,'WORK',work),patch.object(policy,'STATE',baseline):
    be.record_json(manifest,state)
    with self.assertRaisesRegex(ValueError,'Mod selection changed'):be.install_staged(manifest,root/'backups',game_check=lambda:[])
    state['selectedArchives']=['A'];be.record_json(manifest,state);be.record_json(cfg,{'conflict_priorities':{'mod_order':['A']}})
    with self.assertRaisesRegex(ValueError,'settings changed'):be.install_staged(manifest,root/'backups',game_check=lambda:[])
   self.assertEqual(forge.read_bytes(),b'original');self.assertFalse((root/'backups').exists())
   self.assertEqual(cp.build_settings({}),cp.build_settings({'last_transaction':'new receipt'}))
 def test_standalone_weapon_layout_priority_keeps_winner_and_reports_loser(self):
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);base=fixture(self,root);rows=[]
   for n,mod in enumerate(('A','B')):
    path=root/(mod+'.GR_WeaponDBEntry');raw=bytearray(base.read_bytes());raw[-1]=n;path.write_bytes(raw)
    rows.append(dict(mod=mod,archive=str(path),member='1_-_Weapon.GR_WeaponDBEntry',name='1_-_Weapon.GR_WeaponDBEntry',payload=str(path)))
   cfg={'wabo_compatibility_tool':'fixture','conflict_priorities':{'mod_order':['B','A']}}
   with patch('weapon_stats_merge.AuthorReader',return_value=Reader()):
    result=cc.merge_weapons(rows,root,cfg)
   self.assertEqual(len(result),1);self.assertEqual(Path(result[0]['payload']).read_bytes(),Path(rows[1]['payload']).read_bytes())
   self.assertEqual(json.loads((root/'PRIORITY_EXCLUSIONS.json').read_text()),[dict(mod='A',member=rows[0]['member'])])
 def test_declared_db_folder_without_data_suffix_resolves_but_unknown_alias_does_not(self):
  from route_conventions import parse_explicit_parent
  route=parse_explicit_parent('Carbine/DataPC_patch_01/1_-_DBContainerEntry/1_-_SMG_MPX_Tactical.GR_WeaponDBEntry')
  self.assertEqual((route['forge'],route['data'],route['kind']),('DataPC_patch_01.forge','DBContainerEntry_0X104634F921.data','inner'))
  self.assertIsNone(parse_explicit_parent('Carbine/DataPC_patch_01/1_-_UnknownDB/1_-_SMG_MPX_Tactical.GR_WeaponDBEntry'))
 def test_binary_container_groups_do_not_conflate_same_named_distinct_resources(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);items=self.items(root);other=dict(items[0],member='different-parent/1_-_Entry.Type');items.append(other)
   cp.record(root,dict(target=cp.key('DataPC.forge',None,'Entry.Type'),candidates=[cp.candidate(i) for i in items],state='NAME_ONLY'))
   be.record_json(root/'CONTAINER_CONFLICT_GROUPS.json',[dict(id='0000000000000001',type='00000002',name='Entry.Type',candidates=[dict(mod=i['mod'],member=i['member']) for i in items[:2]])])
   cp.container_groups(root,'DataPC.forge',root,items)
   groups=json.loads((root/'CONFLICT_POLICY_REPORT.json').read_text())['groups']
   self.assertEqual(len(groups),1);self.assertEqual(len(groups[0]['candidates']),2);self.assertTrue(groups[0]['binaryIdentityVerified'])
   self.assertIn('#0000000000000001:00000002',groups[0]['target'])
 def test_unknown_field_categories_and_duplicate_mod_order_are_rejected(self):
  with self.assertRaises(ValueError):cp.settings({'conflict_priorities':{'mod_order':['A','A']}})
  with self.assertRaises(ValueError):cp.field_donors([],{'conflict_priorities':{'weapon_fields':{'unknown':'A'}}},lambda _:None)

if __name__=='__main__':unittest.main()
