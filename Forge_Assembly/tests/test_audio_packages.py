import json,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import audio_packages as audio,mod_manager as mm,runtime_stage as runtime,config_install as ci
import complete_content as content,mod_coverage,auto_mapping


class AudioTests(unittest.TestCase):
 def archive(self,root,name,member,data):
  path=root/name
  with zipfile.ZipFile(path,'w') as z:z.writestr(member,data)
  return dict(enabled=True,archive=str(path),members=[member])
 def test_awso_and_hit_sound_banks_stage_install_and_restore_together(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);work=root/'workspace';work.mkdir();game=root/'game';game.mkdir();(game/'GRB.exe').write_bytes(b'game')
   mods={'AWSO-13.zip':self.archive(root,'awso.zip','soundspc_sfx.pck',b'AKPKawso shotgun audio'),
         'HitSounds-1245.zip':self.archive(root,'hits.zip','soundspc_sfx_wea_.pck',b'AKPKquiet hits')}
   target=game/'sounddata/pc/soundspc_sfx.pck';target.parent.mkdir(parents=True);target.write_bytes(b'AKPKvanilla')
   with patch.object(mm,'WORK',work),patch.object(mm,'CONFIG',work/'settings.json'):
    receipt,credits=runtime.prepare(mods,game,work/'auto_runs/current',notify=lambda _:None)
    self.assertEqual({c['mod'] for c in credits},set(mods));self.assertEqual(target.read_bytes(),b'AKPKvanilla')
    transaction=runtime.install(receipt,game,work/'auto_runs/current',root/'backups',game_check=lambda:[])
    self.assertEqual(target.read_bytes(),b'AKPKawso shotgun audio')
    self.assertEqual((game/'sounddata/pc/soundspc_sfx_wea_.pck').read_bytes(),b'AKPKquiet hits')
    live=content.verified_loose(mods,game);self.assertEqual({r['mod'] for r in live},set(mods))
    entries=[dict(archive=k,member=v['members'][0],status='RUNTIME_AUDIO_PACKAGE') for k,v in mods.items()]
    report=mod_coverage.summarize({'entries':entries},{'runtimeContributions':credits})
    self.assertTrue(all(m['state']=='ALL_SELECTED_PAYLOADS_STAGED' for m in report['mods']))
    ci.restore_files(transaction,game_check=lambda:[])
    self.assertEqual(target.read_bytes(),b'AKPKvanilla');self.assertFalse((game/'sounddata/pc/soundspc_sfx_wea_.pck').exists())
 def test_bad_audio_header_is_rejected_during_staging(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);mods={'bad':self.archive(root,'bad.zip','soundspc_sfx.pck',b'not a sound bank')}
   with patch.object(mm,'WORK',root/'workspace'):
    with self.assertRaisesRegex(ValueError,'AKPK'):audio.prepare(mods,root/'game',root/'run',{})
   self.assertFalse((root/'game').exists())
 def test_different_banks_require_priority_and_never_merge_arbitrarily(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);mods={k:self.archive(root,k+'.zip','soundspc_sfx.pck',b'AKPK'+k.encode()) for k in ('one','two')}
   with patch.object(mm,'WORK',root/'workspace'):
    with self.assertRaisesRegex(ValueError,'explicit priority'):audio.prepare(mods,root/'game',root/'run',{})
    plan=audio.prepare(mods,root/'game',root/'run',{'conflict_priorities':{'mod_order':['two','one']}},notify=lambda _:None)
   self.assertEqual(plan[0]['audioContributions'],[{'mod':'two','member':'soundspc_sfx.pck'}])
 def test_supported_audio_is_classified_as_runtime_payload(self):
  self.assertEqual(auto_mapping.candidates_for_member('AWSO.zip','soundspc_sfx.pck',{})[2],'RUNTIME_AUDIO_PACKAGE')
  self.assertIsNone(audio.target_for('unknown.pck',Path('game')))


if __name__=='__main__':unittest.main()
