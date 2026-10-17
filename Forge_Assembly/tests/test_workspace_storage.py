import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import workspace_storage as ws

class StorageTests(unittest.TestCase):
 def test_cleanup_preserves_required_outputs_reports_archives_and_rollback(self):
  with tempfile.TemporaryDirectory() as d:
   w=Path(d)/'workspace';run=w/'auto_runs/current';run.mkdir(parents=True)
   output=run/'Output/game.forge';archive=run/'Archives/mod.zip';scratch=run/'Extracted/resource.Mesh';backup=w/'backups/Originals/game.forge';saved=w/'saved_setup/Working/game.forge'
   for p in (output,archive,scratch,backup,saved):p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'preserve' if p!=scratch else b'disposable')
   proof=run/'PROOF.json';proof.write_text('{"verified":true}')
   manifest=run/'BATCH_RESULT.json';manifest.write_text(json.dumps(dict(status='VERIFIED_STAGED',outputs={'game':{'staged':str(output)}},foundationLayers=[dict(archive=str(archive))])))
   with patch('existing_stage.inspect_existing_stage') as inspect:
    result=ws.cleanup(w,{'last_stage_manifest':str(manifest)})
   self.assertFalse(scratch.exists());self.assertEqual(inspect.call_count,2)
   for p in (output,archive,backup,saved):self.assertEqual(p.read_bytes(),b'preserve')
   self.assertTrue(proof.exists());self.assertEqual(result['removedFiles'],1)
 def test_invalid_retained_stage_stops_before_deletion(self):
  with tempfile.TemporaryDirectory() as d:
   w=Path(d);run=w/'auto_runs/run';run.mkdir(parents=True);payload=run/'bad.data';payload.write_bytes(b'keep')
   manifest=run/'BATCH_RESULT.json';manifest.write_text('{"status":"FAILED"}')
   with self.assertRaises(ws.StorageError):ws.cleanup(w,{'last_stage_manifest':str(manifest)})
   self.assertTrue(payload.exists())
 def test_outside_root_and_unmanaged_backups_are_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   w=Path(d)/'workspace';w.mkdir();outside=Path(d)/'game';outside.mkdir();(outside/'file.forge').write_bytes(b'live')
   backup=w/'backups';backup.mkdir()
   for p in (outside,backup,w):
    with self.assertRaises(ws.StorageError):ws.prune(w,[p],set())
   self.assertEqual((outside/'file.forge').read_bytes(),b'live')
 def test_hardlink_removal_does_not_modify_source_and_usage_deduplicates(self):
  with tempfile.TemporaryDirectory() as d:
   w=Path(d);run=w/'auto_runs/run';run.mkdir(parents=True);source=w/'protected.forge';source.write_bytes(b'original')
   os.link(source,run/'copy.forge')
   self.assertEqual(ws.usage(w)['uniqueBytes'],8)
   ws.prune(w,[w/'auto_runs'],set());self.assertEqual(source.read_bytes(),b'original')
 def test_usage_counts_distinct_files_even_on_windows(self):
  with tempfile.TemporaryDirectory() as d:
   w=Path(d);(w/'a').write_bytes(b'123');(w/'b').write_bytes(b'45678')
   self.assertEqual(ws.usage(w),{'uniqueBytes':8,'uniqueFiles':2})
 def test_symlink_is_not_followed(self):
  with tempfile.TemporaryDirectory() as d:
   base=Path(d);w=base/'workspace';run=w/'auto_runs';run.mkdir(parents=True);outside=base/'game';outside.mkdir();(outside/'file.data').write_bytes(b'live')
   try:os.symlink(outside,run/'link',target_is_directory=True)
   except OSError:self.skipTest('Symlink creation is unavailable')
   ws.prune(w,[run],set());self.assertEqual((outside/'file.data').read_bytes(),b'live')
 def test_dry_run_does_not_remove_payload(self):
  with tempfile.TemporaryDirectory() as d:
   w=Path(d);run=w/'auto_runs/run';run.mkdir(parents=True);p=run/'temp.data';p.write_bytes(b'x')
   result=ws.cleanup(w,{},dry_run=True);self.assertEqual(result['removedFiles'],1);self.assertTrue(p.exists())

class GuardTests(unittest.TestCase):
 def test_payload_provenance_is_preserved(self):
  with tempfile.TemporaryDirectory() as d:
   w=Path(d);run=w/'auto_runs/current';run.mkdir(parents=True)
   archive=run/'vanilla.zip';archive.write_bytes(b'archive')
   m=run/'BATCH_RESULT.json';m.write_text(json.dumps({'status':'VERIFIED_STAGED','presentPayloads':[{'archive':str(archive)}]}))
   with patch('existing_stage.inspect_existing_stage'):ws.cleanup(w,{'last_stage_manifest':str(m)})
   self.assertTrue(archive.exists())
 def test_long_paths_are_removed_but_protected_long_paths_survive(self):
  with tempfile.TemporaryDirectory() as d:
   w=Path(d);run=w/'auto_runs';long=run/('a'*100)/('b'*100)/('c'*100)
   os.makedirs(ws.native(long));file=long/'scratch.data';keep=long/'keep.forge'
   for p in (file,keep):
    with open(ws.native(p),'wb') as f:f.write(b'payload')
   self.assertEqual(ws.usage(w)['uniqueBytes'],14)
   ws.prune(w,[run],{ws.path_key(keep)})
   self.assertFalse(os.path.exists(ws.native(file)));self.assertTrue(os.path.exists(ws.native(keep)))
   os.unlink(ws.native(keep))
   for folder in (long,long.parent,long.parent.parent):os.rmdir(ws.native(folder))
 def test_budget_stops_owned_child(self):
  import storage_guard as guard,sys,time
  with tempfile.TemporaryDirectory() as d:
   start=time.monotonic()
   budget=guard.Budget(d,0,1,free=lambda:100*ws.GIB-(100 if time.monotonic()-start>.4 else 0))
   token=guard._active.set(budget)
   try:
    with self.assertRaises(ws.StorageError):guard.run([sys.executable,'-c','import time;time.sleep(30)'])
   finally:guard._active.reset(token)
   self.assertLess(time.monotonic()-start,5)
 def test_install_guard_suspended(self):
  import storage_guard as guard
  budget=guard.Budget('.',0,1,free=lambda:0);token=guard._active.set(budget)
  try:
   @guard.suspended
   def operation():guard.check();return 'rollback permitted'
   self.assertEqual(operation(),'rollback permitted')
   with self.assertRaises(ws.StorageError):guard.check()
  finally:guard._active.reset(token)
 def test_backup_retirement_keeps_referenced_and_recent_sets(self):
  import time
  with tempfile.TemporaryDirectory() as d:
   w=Path(d);receipts=[]
   for stamp in ('20000101_000000','20000102_000000','20000103_000000','20000104_000000'):
    folder=w/'backups'/stamp;folder.mkdir(parents=True);p=folder/'INSTALL_TRANSACTION.json'
    p.write_text(json.dumps({'status':'INSTALLED','files':[{'target':'game.forge'}]}));os.utime(p,(1,1));receipts.append(p)
   retired=ws.backup_roots_to_retire(w,{'last_transaction':str(receipts[0])})
   self.assertEqual(retired,[receipts[1].parent])
 def test_backup_dedup_requires_matching_actual_bytes(self):
  import hashlib
  with tempfile.TemporaryDirectory() as d:
   w=Path(d);paths=[]
   for name in ('one','two'):
    folder=w/'backups'/name;folder.mkdir(parents=True);p=folder/'game.forge';p.write_bytes(b'original');paths.append(p)
    (folder/'INSTALL_TRANSACTION.json').write_text(json.dumps({'files':[{'backup':str(p),'originalSha256':hashlib.sha256(b'original').hexdigest()}]}))
   self.assertEqual(ws.deduplicate_backups(w),1)
   self.assertEqual(paths[0].stat().st_ino,paths[1].stat().st_ino)
   for p in paths:self.assertEqual(p.read_bytes(),b'original')
 def test_invalid_budgets(self):
  for value in (float('nan'),float('inf'),0,501):
   with self.assertRaises(ws.StorageError):ws.limit_bytes({'workspace_limit_gib':value})

class LifecycleTests(unittest.TestCase):
 def test_cross_process_lease(self):
  import subprocess,sys
  with tempfile.TemporaryDirectory() as d:
   with ws.lease(d):
    code="import workspace_storage as w,sys;\nwith w.lease(sys.argv[1]): print('unexpected')"
    p=subprocess.run([sys.executable,'-c',code,d],capture_output=True,text=True)
   self.assertNotEqual(p.returncode,0);self.assertIn('Another installer operation',p.stderr)
 def test_junction_is_skipped(self):
  import subprocess
  if os.name!='nt':self.skipTest('Windows junction test')
  with tempfile.TemporaryDirectory() as d:
   w=Path(d)/'workspace';run=w/'auto_runs';run.mkdir(parents=True)
   outside=Path(d)/'game';outside.mkdir();(outside/'live.data').write_bytes(b'live')
   link=run/'junction'
   p=subprocess.run(['cmd','/c','mklink','/J',str(link),str(outside)],capture_output=True)
   if p.returncode:self.skipTest('Junction creation unavailable')
   try:
    ws.prune(w,[run],set());self.assertEqual((outside/'live.data').read_bytes(),b'live')
   finally:os.rmdir(link)
 def test_step_compaction_keeps_final_link_and_proof(self):
  import storage_guard as guard
  with tempfile.TemporaryDirectory() as d:
   w=Path(d);step=w/'auto_runs/current/step';step.mkdir(parents=True)
   output=step/'output.forge';output.write_bytes(b'verified');final=w/'auto_runs/current/final.forge';os.link(output,final)
   proof=step/'PROOF.json';proof.write_text('{}')
   token=guard._active.set(guard.Budget(w,0,200*ws.GIB,free=lambda:100*ws.GIB))
   try:guard.compact(step)
   finally:guard._active.reset(token)
   self.assertFalse(output.exists());self.assertTrue(proof.exists());self.assertEqual(final.read_bytes(),b'verified')
 def test_failed_staging_cleans_scratch_and_keeps_report(self):
  import auto_setup_gui as auto,contextlib
  with tempfile.TemporaryDirectory() as d:
   w=Path(d);run=w/'auto_runs/failure';run.mkdir(parents=True)
   def fail(*args,**kwargs):
    (run/'resource.data').write_bytes(b'temp');(run/'PROOF.json').write_text('{}');raise ValueError('packing failed')
   with patch.object(auto.mm,'WORK',w),patch.object(auto.mm,'CONFIG',w/'settings.json'),patch('storage_guard.session',return_value=contextlib.nullcontext()),patch.object(auto,'_perform_unmanaged',side_effect=fail):
    with self.assertRaisesRegex(ValueError,'packing failed'):auto._perform(notify=lambda _:None)
   self.assertFalse((run/'resource.data').exists());self.assertTrue(ws.history_path(w,run/'PROOF.json').exists())

class SingleStageTests(unittest.TestCase):
 def stage(self,w,name):
  run=w/'auto_runs'/name;run.mkdir(parents=True)
  output=run/'output.forge';output.write_bytes(name.encode())
  manifest=run/'BATCH_RESULT.json'
  manifest.write_text(json.dumps({'status':'VERIFIED_STAGED','outputs':{'game':{'staged':str(output)}}}))
  return manifest,output
 def test_latest_stage_replaces_confirmed_stage_and_keeps_receipts(self):
  with tempfile.TemporaryDirectory() as d:
   w=Path(d);old,old_output=self.stage(w,'old');current,output=self.stage(w,'current')
   with patch('existing_stage.inspect_existing_stage'):
    result=ws.cleanup(w,{'last_stage_manifest':str(current),'last_confirmed_archive_manifest':str(old)})
   self.assertTrue(output.exists());self.assertFalse(old_output.exists());self.assertFalse(old.exists());self.assertTrue(ws.history_path(w,old).exists())
   self.assertEqual(result['retainedStages'],[str(current)])
   self.assertEqual([p.name for p in (w/'auto_runs').iterdir()],['current'])
 def test_new_stage_keeps_required_resource_dependencies_only(self):
  with tempfile.TemporaryDirectory() as d:
   w=Path(d);old,old_output=self.stage(w,'old');new,output=self.stage(w,'new');resource,resource_output=self.stage(w,'resource')
   source=w/'auto_runs/new/ComposedGame';source.mkdir();(source/'GRB.exe').write_bytes(b'exe')
   resource_doc=ws.read(resource);resource_doc['plan']={'gameRoot':str(source)};resource.write_text(json.dumps(resource_doc))
   doc=ws.read(new);doc['resourceManifest']=str(resource);new.write_text(json.dumps(doc))
   with patch('existing_stage.inspect_existing_stage'):
    result=ws.cleanup(w,{'last_stage_manifest':str(old)},extra_stages=[str(new)])
   self.assertFalse(old_output.exists());self.assertTrue(output.exists());self.assertTrue(resource_output.exists())
   self.assertEqual(result['retainedStages'],[str(new),str(resource)])
 def test_invalid_replacement_leaves_previous_stage_untouched(self):
  with tempfile.TemporaryDirectory() as d:
   w=Path(d);old,old_output=self.stage(w,'old');new,output=self.stage(w,'new')
   with patch('existing_stage.inspect_existing_stage',side_effect=ValueError('Invalid new stage')):
    with self.assertRaisesRegex(ValueError,'Invalid new stage'):ws.cleanup(w,{'last_stage_manifest':str(old)},extra_stages=[str(new)])
   self.assertTrue(old_output.exists());self.assertTrue(output.exists())
 def test_diagnostic_build_keeps_current_stage_and_only_diagnostic_reports(self):
  import auto_setup_gui as auto,contextlib
  with tempfile.TemporaryDirectory() as d:
   w=Path(d);current,output=self.stage(w,'current');diagnostic,diagnostic_output=self.stage(w,'diagnostic')
   cfg=w/'settings.json';cfg.write_text(json.dumps({'last_stage_manifest':str(current)}))
   with patch.object(auto.mm,'WORK',w),patch.object(auto.mm,'CONFIG',cfg),patch('storage_guard.session',return_value=contextlib.nullcontext()),patch.object(auto,'_perform_unmanaged',return_value=(diagnostic.parent,{'batch':str(diagnostic)})),patch('existing_stage.inspect_existing_stage'):
    auto._perform(_retain_stage=False,notify=lambda _:None)
   self.assertTrue(output.exists());self.assertFalse(diagnostic_output.exists());self.assertFalse(diagnostic.exists());self.assertTrue(ws.history_path(w,diagnostic).exists())
   self.assertEqual(ws.read(cfg)['last_stage_manifest'],str(current))
 def test_successful_build_saves_new_selection_before_retiring_previous(self):
  import auto_setup_gui as auto,contextlib
  with tempfile.TemporaryDirectory() as d:
   w=Path(d);old,old_output=self.stage(w,'old');new,output=self.stage(w,'new')
   cfg=w/'settings.json';cfg.write_text(json.dumps({'last_stage_manifest':str(old)}))
   with patch.object(auto.mm,'WORK',w),patch.object(auto.mm,'CONFIG',cfg),patch('storage_guard.session',return_value=contextlib.nullcontext()),patch.object(auto,'_perform_unmanaged',return_value=(new.parent,{'batch':str(new)})),patch('existing_stage.inspect_existing_stage'):
    auto._perform(notify=lambda _:None)
   self.assertFalse(old_output.exists());self.assertTrue(output.exists())
   self.assertEqual(ws.read(cfg)['last_stage_manifest'],str(new))
 def test_interrupted_run_is_cleaned_and_reports_moved_out_of_stage_list(self):
  with tempfile.TemporaryDirectory() as d:
   w=Path(d);current,output=self.stage(w,'current');run=w/'auto_runs/interrupted';run.mkdir()
   (run/'AUTO_SETUP_RESULT.json').write_text(json.dumps({'batch':None,'state':'PREPARING_FOUNDATIONS'}))
   (run/'mod.zip').write_bytes(b'abandoned archive snapshot')
   with patch('existing_stage.inspect_existing_stage'):
    result=ws.cleanup(w,{'last_stage_manifest':str(current)})
   self.assertTrue(output.exists());self.assertFalse(run.exists())
   archived=w/'diagnostics/run_history/interrupted/AUTO_SETUP_RESULT.json'
   self.assertTrue(archived.exists());self.assertEqual(result['archivedAbandonedRuns'],[str(archived.parent)])
 def test_recovery_follows_links_through_retired_run_reports(self):
  import layered_pipeline as lp,deployment_policy as policy
  with tempfile.TemporaryDirectory() as d:
   w=Path(d);current,output=self.stage(w,'current');old,old_output=self.stage(w,'old')
   game=w/'game';game.mkdir()
   original=w/'backups/previous/INSTALL_TRANSACTION.json';original.parent.mkdir(parents=True)
   backup=original.parent/'original.forge';backup.write_bytes(b'clean')
   original.write_text(json.dumps({'gameRoot':str(game),'files':[{'backup':str(backup),'target':str(game/'data.forge')}]}))
   old_doc=ws.read(old);old_doc.update(plan={'gameRoot':str(game)},outputs={'data':{'previousTransaction':str(original)}});old.write_text(json.dumps(old_doc))
   tx=w/'backups/latest/INSTALL_TRANSACTION.json';tx.parent.mkdir(parents=True)
   tx.write_text(json.dumps({'gameRoot':str(game),'batchManifest':str(old)}))
   with patch('existing_stage.inspect_existing_stage'):ws.cleanup(w,{'last_stage_manifest':str(current),'last_transaction':str(tx)})
   self.assertFalse(old.parent.exists());self.assertTrue(ws.history_path(w,old).is_file())
   with patch.object(policy,'WORK',w):history=lp.receipt_history(game,[str(tx)])
   self.assertIn(original,[path for path,doc in history]);self.assertTrue(backup.exists())

if __name__=='__main__':unittest.main()
