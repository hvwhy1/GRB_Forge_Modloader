import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import baseline_gameplay as baseline,batch_engine as be,deployment_policy as policy


class BaselineTests(unittest.TestCase):
 def test_single_extraction_still_rejects_a_missing_required_gameplay_object(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);source=root/'source/DataPC_patch_01.forge';source.parent.mkdir();source.write_bytes(b'private source')
   game=root/'game';game.mkdir();target=game/source.name;digest=be.sha256(source)
   cfg={'preserve_game_sensitivity':True,'preserve_player_automatic_fire':False,'npc_trigger_priority':'Mod settings'}
   calls=[]
   def extract(command,**kwargs):
    calls.append(command);output=Path(command[-1]);output.mkdir(parents=True)
    donor=output/'input.bin';donor.write_bytes(b'input')
    name=baseline.INPUTS[0]
    (output/'READ_ONLY_EXTRACTION.json').write_text(json.dumps({'sourceUnchanged':True,'sourceSha256':digest,
      'rows':[{'name':name,'file':str(donor)}],'available':[{'name':name,'extension':'InputFilter'}]}))
    return SimpleNamespace(returncode=0)
   with patch.object(policy,'require_ready',return_value={'hashes':{str(target):digest}}),patch.object(baseline.storage_guard,'run',side_effect=extract):
    with self.assertRaisesRegex(ValueError,'Required vanilla gameplay resource missing'):baseline.prepare([],{str(target):source},root/'toolkit',root/'run',cfg,lambda _:None)
   self.assertEqual(len(calls),1);self.assertEqual(calls[0][2],'cached-extract-short-existing')
   self.assertEqual(source.read_bytes(),b'private source')


if __name__=='__main__':unittest.main()
