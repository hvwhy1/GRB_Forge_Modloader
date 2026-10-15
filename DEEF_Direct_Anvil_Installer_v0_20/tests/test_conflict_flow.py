import json,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
import conflict_flow as flow,mod_manager as mm,batch_engine as be

class ConflictFlowTests(unittest.TestCase):
 def fixture(self,root):
  cfg=root/'settings.json';report=root/'CONFLICT_POLICY_REPORT.json'
  row=dict(mod='A',member='variant/file.data',archiveSha256='a'*64)
  be.record_json(report,dict(groups=[dict(target='datapc.forge||#1:2',state='VERIFIED_BINARY_CONFLICT_GROUP',candidates=[row])]))
  mm.save(cfg,{})
  return cfg,report,row
 def test_choice_request_waits_for_response_without_consuming_worker(self):
  request=flow.ChoiceRequest('report','conflict');received=[]
  thread=threading.Thread(target=lambda:received.append(request.wait()));thread.start()
  self.assertTrue(thread.is_alive());request.answer(True);thread.join(1)
  self.assertEqual(received,[True]);self.assertFalse(thread.is_alive())
 def test_only_current_resource_choices_can_resume(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);cfg,report,row=self.fixture(root)
   with patch.object(mm,'CONFIG',cfg):
    def choose(path,message):
     values=mm.load(cfg,{});values['conflict_priorities']={'resource_winners':{'datapc.forge||#1:2':row}};mm.save(cfg,values);return True
    self.assertTrue(flow.resolve(report,'conflict',choose))
    mm.save(cfg,{})
    def unrelated(path,message):
     values=mm.load(cfg,{});values['shotgun_damage_profile']='200';mm.save(cfg,values);return True
    with self.assertRaisesRegex(ValueError,'Other settings changed'):flow.resolve(report,'conflict',unrelated)
 def test_cancel_and_unchanged_choices_cannot_silently_continue(self):
  with tempfile.TemporaryDirectory() as d:
   cfg,report,row=self.fixture(Path(d))
   with patch.object(mm,'CONFIG',cfg):
    with self.assertRaises(flow.StagingCancelled):flow.resolve(report,'conflict',lambda *a:False)
    with self.assertRaisesRegex(ValueError,'No valid choice'):flow.resolve(report,'conflict',lambda *a:True)
 def test_corruption_is_not_a_choice_and_author_mod_list_is_not_a_selection_source(self):
  from auto_setup_gui import choose_default_flags
  self.assertTrue(flow.recoverable('SAME_ARCHIVE_CONTAINER_VARIANTS_NEED_EXACT_CHOICE'))
  self.assertFalse(flow.recoverable('WHOLE_DATA_ID_TYPE_MISMATCH'))
  with patch('auto_mapping.LIST_FILE',Path('missing-author-list.json')):
   mods={'new':{'enabled':True},'disabled':{'enabled':False,'_enabled_by_user':True}}
   self.assertEqual(choose_default_flags(mods),mods)

if __name__=='__main__':unittest.main()
