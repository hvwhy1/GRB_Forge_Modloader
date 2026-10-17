import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import backup_slots as slots,batch_engine as be,mod_manager as mm,deployment_policy as policy,layered_pipeline as lp,runtime_history as rh


class BackupSlotTests(unittest.TestCase):
 def test_three_installs_keep_only_baseline_and_previous_full_build_and_runtime(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);work=root/'workspace';work.mkdir();game=root/'game';game.mkdir();(game/'GRB.exe').write_bytes(b'exe')
   forge=game/'DataPC_patch_01.forge';forge.write_bytes(b'vanilla');clean=be.sha256(forge);loader=game/'native.ini';base=work/'backups'
   be.record_json(work/'recovery_state.json',{'baselineId':'base','installationPaused':False,'gameRoot':str(game),'hashes':{str(forge):clean}})
   be.record_json(work/'settings.json',{})
   with patch.object(mm,'WORK',work),patch.object(mm,'CONFIG',work/'settings.json'),patch.object(policy,'WORK',work),patch.object(policy,'STATE',work/'recovery_state.json'):
    for index in range(3):
     folder=base/('install_'+str(index));folder.mkdir(parents=True)
     original=folder/forge.name;original.write_bytes(forge.read_bytes());before=be.sha256(original)
     runtime_before=folder/'native.ini';previous=loader.read_bytes() if loader.exists() else None
     if previous is not None:runtime_before.write_bytes(previous)
     forge.write_bytes(('build'+str(index)).encode());loader.write_bytes(('config'+str(index)).encode())
     row={'target':str(forge),'backup':str(original),'originalSha256':before,'installedSha256':be.sha256(forge)}
     config=folder/'CONFIG_TRANSACTION.json';be.record_json(config,{'status':'INSTALLED','gameRoot':str(game),'files':[{'target':str(loader),'backup':str(runtime_before) if previous else None,'originalSha256':be.sha256(runtime_before) if previous else None,'installedSha256':be.sha256(loader)}]})
     actual=folder/'INSTALL_TRANSACTION.json';be.record_json(actual,{'status':'INSTALLED','gameRoot':str(game),'files':[row],'runtimeTransaction':str(config)})
     recovery=base/('recovery_'+str(index));recovery.mkdir();vanilla=recovery/forge.name;vanilla.write_bytes(b'vanilla')
     baseline=recovery/'INSTALL_TRANSACTION.json';be.record_json(baseline,{'status':'INSTALLED','gameRoot':str(game),'files':[dict(row,backup=str(vanilla),originalSha256=clean)]})
     result=slots.rotate(None,actual,base,baseline)
     self.assertEqual({p.name for p in base.iterdir() if p.is_dir()},{'Baseline','LastWorking'})
     self.assertEqual((base/'Baseline/Originals'/forge.name).read_bytes(),b'vanilla')
     self.assertEqual((base/'LastWorking/Originals'/forge.name).read_bytes(),b'vanilla' if index==0 else ('build'+str(index-1)).encode())
     self.assertEqual(be.sha256(lp.baseline_sources(game,policy.require_ready(game))[0][str(forge)]),clean)
     plan=rh.cleanup([],game,mm.load(mm.CONFIG,{}));self.assertEqual(len(plan),1);self.assertTrue(plan[0]['remove'])
    # An idempotent install must not replace the previous build with itself.
    empty=base/'idempotent';empty.mkdir();receipt=empty/'INSTALL_TRANSACTION.json'
    be.record_json(receipt,{'status':'INSTALLED','gameRoot':str(game),'files':[]})
    slots.rotate(None,receipt,base,base/'Baseline/INSTALL_TRANSACTION.json')
    self.assertEqual((base/'LastWorking/Originals'/forge.name).read_bytes(),b'build1')
    # Actual prior bytes are recoverable, not merely a receipt pointing at them.
    # A known clean game after verification is also a valid restore starting point.
    forge.write_bytes(b'vanilla');loader.unlink()
    be.restore_install(base/'LastWorking/INSTALL_TRANSACTION.json',game_check=lambda:[])
    self.assertEqual(forge.read_bytes(),b'build1')
    import config_install as ci
    ci.restore_files(base/'LastWorking/CONFIG_TRANSACTION.json',game_check=lambda:[])
    self.assertEqual(loader.read_bytes(),b'config1')
    self.assertFalse(lp.baseline_sources(game,policy.require_ready(game))[1][str(forge)].get('previousFoundationOnly'))
    self.assertTrue(rh.cleanup([],game,mm.load(mm.CONFIG,{}))[0]['remove'])
 def test_standalone_configuration_installs_also_keep_only_two_slots(self):
  import config_install as ci
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);work=root/'workspace';work.mkdir();game=root/'game';game.mkdir();(game/'GRB.exe').write_bytes(b'exe');base=work/'backups';target=game/'native.ini'
   be.record_json(work/'recovery_state.json',{'baselineId':'base','installationPaused':False,'gameRoot':str(game),'hashes':{}})
   with patch.object(mm,'WORK',work),patch.object(mm,'CONFIG',work/'settings.json'),patch.object(policy,'WORK',work),patch.object(policy,'STATE',work/'recovery_state.json'):
    for index in range(3):
     ci.install_files([{'target':str(target),'content':'config'+str(index)}],game,base,game_check=lambda:[])
     self.assertEqual({p.name for p in base.iterdir() if p.is_dir()},{'Baseline','LastWorking'})
    ci.install_files([{'target':str(target),'content':'config2'}],game,base,game_check=lambda:[])
    self.assertEqual((base/'LastWorking/Originals/native.ini').read_bytes(),b'config1')
    self.assertTrue(rh.cleanup([],game,mm.load(mm.CONFIG,{}))[0]['remove'])
 def test_corrupt_baseline_aborts_without_removing_existing_backups(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);work=root/'workspace';work.mkdir();game=root/'game';game.mkdir();forge=game/'DataPC.forge';forge.write_bytes(b'installed');base=work/'backups';old=base/'old';old.mkdir(parents=True)
   backup=old/'original.forge';backup.write_bytes(b'corrupt');receipt=old/'INSTALL_TRANSACTION.json'
   be.record_json(receipt,{'status':'INSTALLED','gameRoot':str(game),'files':[{'target':str(forge),'backup':str(backup),'originalSha256':'0'*64,'installedSha256':be.sha256(forge)}]})
   be.record_json(work/'recovery_state.json',{'baselineId':'base','installationPaused':False,'gameRoot':str(game),'hashes':{str(forge):'0'*64}})
   with patch.object(mm,'WORK',work),patch.object(mm,'CONFIG',work/'settings.json'),patch.object(policy,'STATE',work/'recovery_state.json'):
    with self.assertRaisesRegex(ValueError,'missing or changed'):slots.rotate(None,receipt,base,receipt)
   self.assertTrue(backup.is_file());self.assertTrue(receipt.is_file());self.assertFalse((base/'Baseline').exists())
 def test_fpv_only_install_preserves_forge_recovery_without_reading_forges_or_stage(self):
  import config_install as ci
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);work=root/'workspace';work.mkdir();game=root/'game';game.mkdir();(game/'GRB.exe').write_bytes(b'exe')
   base=work/'backups';forge=game/'DataPC.forge';forge.write_bytes(b'current build');target=game/'NativeFirstPerson.ini';target.write_bytes(b'old FPV')
   digest=be.sha256(forge);clean=None
   for name,data in [('Baseline',b'vanilla'),('LastWorking',b'previous build')]:
    original=base/name/'Originals'/forge.name;original.parent.mkdir(parents=True);original.write_bytes(data)
    if name=='Baseline':clean=be.sha256(original)
    be.record_json(base/name/'INSTALL_TRANSACTION.json',{'status':'INSTALLED','gameRoot':str(game),'files':[{'target':str(forge),'backup':str(original),'originalSha256':be.sha256(original),'installedSha256':digest}]})
   be.record_json(work/'recovery_state.json',{'baselineId':'base','installationPaused':False,'gameRoot':str(game),'hashes':{str(forge):clean}})
   stage=work/'BATCH_RESULT.json';stage.write_text('{}')
   be.record_json(work/'settings.json',{'last_stage_manifest':str(stage)})
   real_hash=be.sha256
   def runtime_hash(path):
    self.assertNotEqual(Path(path).suffix.lower(),'.forge','FPV-only install read a Forge')
    return real_hash(path)
   with patch.object(mm,'WORK',work),patch.object(mm,'CONFIG',work/'settings.json'),patch.object(policy,'STATE',work/'recovery_state.json'),patch.object(be,'sha256',side_effect=runtime_hash),patch.object(lp,'inspect_stage',side_effect=AssertionError('FPV-only install inspected the complete stage')):
    for text in ('new FPV','updated FPV'):
     receipt=ci.install_files([{'target':str(target),'content':text}],game,base,game_check=lambda:[])
    self.assertEqual(target.read_bytes(),b'updated FPV')
    ci.restore_files(receipt,game_check=lambda:[])
    self.assertEqual(target.read_bytes(),b'new FPV')
    ci.restore_files(base/'Baseline/CONFIG_TRANSACTION.json',game_check=lambda:[])
    self.assertEqual(target.read_bytes(),b'old FPV')
   self.assertEqual(forge.read_bytes(),b'current build')
   self.assertEqual((base/'Baseline/Originals'/forge.name).read_bytes(),b'vanilla')
   self.assertEqual((base/'LastWorking/Originals'/forge.name).read_bytes(),b'previous build')
   self.assertEqual({p.name for p in base.iterdir() if p.is_dir()},{'Baseline','LastWorking'})
 def test_existing_history_with_repeated_configuration_versions_has_one_verified_origin(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);work=root/'workspace';work.mkdir();game=root/'game';game.mkdir();base=work/'backups';target=game/'NativeFirstPerson.ini';target.write_bytes(b'original');previous=None
   for date,data in [('20260101_010000',b'other'),('20260102_010000',b'original'),('20260103_010000',b'current')]:
    folder=base/('CONFIG_'+date);folder.mkdir(parents=True);original=folder/target.name;original.write_bytes(target.read_bytes());target.write_bytes(data)
    receipt=folder/'CONFIG_TRANSACTION.json';doc={'status':'INSTALLED','gameRoot':str(game),'files':[{'target':str(target),'backup':str(original),'originalSha256':be.sha256(original),'installedSha256':be.sha256(target)}]}
    if previous:doc['previousConfigTransaction']=str(previous)
    be.record_json(receipt,doc);previous=receipt
   anchor=base/'initial';anchor.mkdir();receipt=anchor/'INSTALL_TRANSACTION.json';be.record_json(receipt,{'status':'INSTALLED','gameRoot':str(game),'files':[]})
   be.record_json(work/'settings.json',{'last_config_transaction':str(previous)})
   be.record_json(work/'recovery_state.json',{'baselineId':'baseline','installationPaused':False,'gameRoot':str(game),'hashes':{}})
   with patch.object(mm,'WORK',work),patch.object(mm,'CONFIG',work/'settings.json'),patch.object(policy,'WORK',work),patch.object(policy,'STATE',work/'recovery_state.json'):
    slots.rotate(None,None,base,receipt)
   self.assertEqual((base/'Baseline/Originals'/target.name).read_bytes(),b'original')
   self.assertEqual((base/'LastWorking/Originals'/target.name).read_bytes(),b'current')


if __name__=='__main__':unittest.main()
