import copy,json,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import mod_manager as mm,batch_engine as be,layered_pipeline as lp,incremental_stage as inc,deployment_policy as policy


class IncrementalTests(unittest.TestCase):
 def test_global_reads_see_the_correct_build_phase_instead_of_final_resource_edits(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);view=root/'view';view.mkdir();target=str(root/'game/DataPC_A.forge');file=view/'DataPC_A.forge';file.write_bytes(b'foundation');current={target:be.sha256(file)};checkpoints={}
   for phase,data in [('beforeOuter',b'foundation'),('afterOuter',b'outer changes'),('preResource',b'inner changes')]:
    file.unlink();file.write_bytes(data);current[target]=be.sha256(file);inc.capture_phase(phase,view,root/'run',current,checkpoints,{})
   file.unlink();file.write_bytes(b'final resource changes');current[target]=be.sha256(file)
   reused={target:{'phaseSources':checkpoints[target]}}
   inc.advance_view('beforeOuter',view,current,reused);self.assertEqual(file.read_bytes(),b'foundation')
   inc.advance_view('afterOuter',view,current,reused,'DataPC_0.forge');self.assertEqual(file.read_bytes(),b'foundation')
   inc.advance_view('afterOuter',view,current,reused,'DataPC_B.forge');self.assertEqual(file.read_bytes(),b'outer changes')
   inc.advance_view('preResource',view,current,reused);self.assertEqual(file.read_bytes(),b'inner changes')
   self.assertEqual(Path(checkpoints[target]['beforeOuter']['file']).read_bytes(),b'foundation')
 def test_global_parent_lookup_depends_on_changes_in_other_forges(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);game=root/'game';game.mkdir();mods={};hashes={}
   for name in ('DataPC_patch_01.forge','DataPC_Resources_patch_01.forge'):
    target=game/name;target.write_bytes(b'baseline');hashes[str(target)]=be.sha256(target)
    archive=root/(name+'.zip');archive.write_bytes(b'archive');mods[archive.name]={'archive':str(archive),'members':[name],'enabled':True,'status':'INDEXED'}
   dependent=str(game/'DataPC_Resources_patch_01.forge');baseline={'hashes':hashes}
   before,_=inc.fingerprints(mods,game,baseline,{},[dependent]);independent,_=inc.fingerprints(mods,game,baseline,{},[])
   Path(mods['DataPC_patch_01.forge.zip']['archive']).write_bytes(b'changed')
   after,_=inc.fingerprints(mods,game,baseline,{},[dependent]);independent_after,_=inc.fingerprints(mods,game,baseline,{},[])
   self.assertNotEqual(before[dependent],after[dependent]);self.assertEqual(independent[dependent],independent_after[dependent])
 def test_add_one_preserves_disabled_mods_and_does_not_enable_other_new_archives(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)
   for name in ('old.zip','disabled.zip','new.zip','other.zip'):
    with zipfile.ZipFile(root/name,'w') as z:z.writestr('readme.txt','hello')
   mods=inc.select_one(root,'new.zip',{'old.zip':{'enabled':True},'disabled.zip':{'enabled':False}})
   self.assertEqual({k for k,v in mods.items() if v['enabled']},{'old.zip','new.zip'})
 def test_unchanged_forges_reuse_flatten_and_invalidate_when_archive_changes(self):
  import auto_mapping,forge_audit,complete_content
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);work=root/'workspace';work.mkdir();game=root/'game';game.mkdir();(game/'GRB.exe').write_bytes(b'exe')
   kit=root/'kit';kit.mkdir();mods={};payloads={};hashes={}
   for name in ('DataPC_patch_01.forge','DataPC_Resources_patch_01.forge'):
    forge=game/name;forge.write_bytes(b'vanilla'+name.encode());hashes[str(forge)]=be.sha256(forge)
    archive=root/(name+'.zip');archive.write_bytes(b'archive'+name.encode())
    payload=root/('payload_'+name);payload.write_bytes(b'mod'+name.encode());payloads[archive.name]=payload
    mods[archive.name]={'archive':str(archive),'members':[name],'enabled':True,'status':'INDEXED'}
   be.record_json(work/'settings.json',{'complete_content_pipeline':True})
   be.record_json(work/'recovery_state.json',{'baselineId':'baseline','installationPaused':False,'gameRoot':str(game),'hashes':hashes})
   def analyze(selected,view,toolkit,run_dir,**kw):
    run_dir.mkdir();file=run_dir/'AUTO_CHARACTERIZATION.json';details={'entries':[],'verifiedCount':0,'blockingUnmappedCount':0};be.record_json(file,details)
    return selected,details,file
   inspector=lambda *args:{'verified':True,'independentAnvilIndexVerified':True,'nestedDataEntries':['one']}
   with patch.object(mm,'WORK',work),patch.object(mm,'CONFIG',work/'settings.json'),patch.object(mm,'STATE',work/'mods.json'),patch.object(policy,'WORK',work),patch.object(policy,'STATE',work/'recovery_state.json'),patch.object(mm,'extraction_target',side_effect=lambda archive,*a,**k:payloads[Path(archive).name]),patch.object(auto_mapping,'analyze',side_effect=analyze),patch.object(forge_audit,'read_index',return_value={'entries':[{'id':'0100'}]}),patch.object(complete_content,'prepare',side_effect=lambda selected,*a,**k:(selected,[])),patch.object(inc,'revision',return_value='test-engine'):
    first,report=lp.perform(mods,game,kit,work/'backups',inspect=inspector,notify=lambda _:None)
    cfg=mm.load(mm.CONFIG,{});cfg['last_stage_manifest']=report['batch'];mm.save(mm.CONFIG,cfg)
    second,report2=lp.perform(mods,game,kit,work/'backups',inspect=lambda *a:self.fail('Unchanged Forge was rebuilt'),notify=lambda _:None)
    self.assertEqual(report2['reusedForges'],2);lp.inspect_stage(report2['batch'])
    cfg['last_stage_manifest']=report2['batch'];mm.save(mm.CONFIG,cfg)
    import existing_stage,recovery_snapshot,workspace_storage as storage
    _,transaction=existing_stage.install_existing(report2['batch'],work/'backups',game_check=lambda:[])
    recovery_snapshot.create_layered(report2['batch'],transaction,work/'backups')
    cfg=mm.load(mm.CONFIG,{})
    # Repeated updates reference original proofs directly, not a growing chain.
    third,report3=lp.perform(mods,game,kit,work/'backups',inspect=inspector,notify=lambda _:None)
    state=mm.load(report3['batch'],{})
    for entry in state['reusedForges'].values():self.assertFalse(mm.load(entry['manifest'],{}).get('reusedForges'))
    cfg['last_stage_manifest']=report3['batch'];mm.save(mm.CONFIG,cfg)
    # A changed source invalidates only its destination.
    changed=next(iter(mods));Path(mods[changed]['archive']).write_bytes(b'changed archive')
    fourth,report4=lp.perform(mods,game,kit,work/'backups',inspect=inspector,notify=lambda _:None)
    self.assertEqual(report4['reusedForges'],1)
    cfg['last_stage_manifest']=report4['batch'];mm.save(mm.CONFIG,cfg)
    # Disable one installed foundation: keep the other complete Forge and
    # explicitly restore the removed mod's destination to its clean baseline.
    removed='DataPC_Resources_patch_01.forge.zip';mods[removed]['enabled']=False
    fifth,report5=lp.perform(mods,game,kit,work/'backups',inspect=inspector,notify=lambda _:None)
    state=mm.load(report5['batch'],{});self.assertEqual(state['baselineRestores'],[str(game/'DataPC_Resources_patch_01.forge')])
    cfg['last_stage_manifest']=report5['batch'];mm.save(mm.CONFIG,cfg)
    _,transaction=existing_stage.install_existing(report5['batch'],work/'backups',game_check=lambda:[])
    recovery_snapshot.create_layered(report5['batch'],transaction,work/'backups')
    self.assertEqual((game/'DataPC_Resources_patch_01.forge').read_bytes(),b'vanillaDataPC_Resources_patch_01.forge')
    cfg=mm.load(mm.CONFIG,{})
    storage.cleanup(work,cfg)
    self.assertEqual(len(list((work/'auto_runs').iterdir())),1)
    lp.inspect_stage(report5['batch'],verify_backups=True)
    # Reuse must reject modified retained output bytes.
    state=mm.load(report5['batch'],{})
    target=next(iter(state['reusedForges']));Path(state['outputs'][target]['staged']).write_bytes(b'corrupt')
    with self.assertRaisesRegex(ValueError,'Retained stage bytes changed'):lp.perform(mods,game,kit,work/'backups',inspect=inspector,notify=lambda _:None)


class LaserTests(unittest.TestCase):
 def test_both_laser_textures_target_patch_instead_of_ambiguous_base_copy(self):
  import complete_content as cc
  with tempfile.TemporaryDirectory() as d:
   archive=Path(d)/'No Visible Laser Light-775-V0.7z';archive.write_bytes(b'archive')
   members=['61631_-_GFX_xMap_Red_DiffuseMap.data','61633_-_GFX_xLazer_Decal_DiffuseMap.data']
   outer,inner=cc.inventory({archive.name:{'archive':str(archive),'enabled':True,'members':members}})
   self.assertEqual(set(outer),{'DataPC_Resources_patch_01.forge'});self.assertEqual(len(next(iter(outer.values()))),2);self.assertFalse(inner)


if __name__=='__main__':unittest.main()
