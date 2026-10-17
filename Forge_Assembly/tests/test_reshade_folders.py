import io,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import reshade_install as ri,config_install as ci,mod_manager as mm
import batch_engine as be
import deployment_policy as policy


class ShaderFolderTests(unittest.TestCase):
 def fixture(self,root):
  game=root/'game';game.mkdir();(game/'GRB.exe').write_bytes(b'exe');work=root/'workspace';work.mkdir()
  setup=root/'setup.exe'
  with zipfile.ZipFile(setup,'w') as archive:
   archive.writestr('ReShade32.dll',b'MZ32');archive.writestr('ReShade64.dll',b'MZ64')
  local=root/'pack/reshade-shaders';(local/'Shaders').mkdir(parents=True);(local/'Textures').mkdir()
  (local/'Shaders/Custom.fx').write_bytes(b'local shader');(local/'Textures/Custom.png').write_bytes(b'local texture')
  return game,work,setup,local

 def test_parent_and_category_folder_selections_install_and_restore_local_files(self):
  for selection in ('','Shaders','Textures'):
   with self.subTest(selection=selection),tempfile.TemporaryDirectory() as d:
    root=Path(d);game,work,setup,local=self.fixture(root)
    with patch.object(mm,'WORK',work),patch.object(mm,'CONFIG',work/'settings.json'):
     rows=ri.prepare(setup,game,shader_folder=local/selection,fetch_effects=False)
     receipt=ci.install_files(rows,game,root/'backups',game_check=lambda:[])
     if selection!='Textures':self.assertEqual((game/'reshade-shaders/Shaders/Custom.fx').read_bytes(),b'local shader')
     if selection!='Shaders':self.assertEqual((game/'reshade-shaders/Textures/Custom.png').read_bytes(),b'local texture')
     ci.restore_files(receipt,game_check=lambda:[])
     self.assertFalse((game/'reshade-shaders/Shaders/Custom.fx').exists());self.assertFalse((game/'reshade-shaders/Textures/Custom.png').exists())

 def test_selected_local_pack_wins_over_downloads_without_duplicate_destinations(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);game,work,setup,local=self.fixture(root);data=io.BytesIO()
   with zipfile.ZipFile(data,'w') as archive:
    archive.writestr('repo/Shaders/custom.fx',b'downloaded shader');archive.writestr('repo/Shaders/Dependency.fx',b'dependency')
   manifest=b'[core]\nRequired=1\nPackageName=Test\nDownloadUrl=https://github.com/test/archive/main.zip\nInstallPath=reshade-shaders/Shaders\nTextureInstallPath=reshade-shaders/Textures\n'
   def fetch(url):return manifest if url==ri.MANIFEST else data.getvalue()
   with patch.object(mm,'WORK',work):rows=ri.prepare(setup,game,shader_folder=local,fetch=fetch)
   shaders=[r for r in rows if Path(r['target']).suffix=='.fx']
   self.assertEqual(len(shaders),2)
   custom=next(r for r in shaders if Path(r['target']).name.casefold()=='custom.fx')
   self.assertEqual(Path(custom['source']).read_bytes(),b'local shader')

 def test_missing_selected_folder_is_reported(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);game,work,setup,local=self.fixture(root)
   with patch.object(mm,'WORK',work),self.assertRaisesRegex(ValueError,'folder does not exist'):
    ri.prepare(setup,game,shader_folder=root/'missing',fetch_effects=False)

 def test_supplied_package_does_not_mix_new_effects_with_old_headers(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);game,work,setup,local=self.fixture(root)
   package=local/'Shaders/CShade';(package/'shared').mkdir(parents=True)
   (package/'cOverlay.fx').write_text('old matching effect');(package/'shared/cShade.fxh').write_text('old matching header')
   extra=game/'reshade-shaders/Shaders/CShade/cBloom.fx';extra.parent.mkdir(parents=True);extra.write_text('incompatible newer effect')
   untouched=extra.with_name('UserExtra.fx');untouched.write_text('untracked')
   receipt=work/'backups/Baseline/CONFIG_TRANSACTION.json'
   be.record_json(receipt,{'status':'INSTALLED','gameRoot':str(game),'files':[{'target':str(extra),'originalSha256':None,'backup':None,'installedSha256':be.sha256(extra)}]})
   be.record_json(work/'settings.json',{'baseline_config_transaction':str(receipt)})
   data=io.BytesIO()
   with zipfile.ZipFile(data,'w') as archive:
    archive.writestr('repo/Shaders/cBloom.fx',b'new effect');archive.writestr('repo/Shaders/shared/cShade.fxh',b'new header')
   manifest=b'[cshade]\nRequired=1\nPackageName=CShade\nDownloadUrl=https://github.com/test/archive/main.zip\nInstallPath=reshade-shaders/Shaders/CShade\nTextureInstallPath=reshade-shaders/Textures/CShade\n'
   with patch.object(mm,'WORK',work),patch.object(mm,'CONFIG',work/'settings.json'),patch.object(policy,'WORK',work):
    rows=ri.prepare(setup,game,shader_folder=local,fetch=lambda url:manifest if url==ri.MANIFEST else data.getvalue())
    extra_rows=[r for r in rows if Path(r['target'])==extra];self.assertEqual(len(extra_rows),1);self.assertTrue(extra_rows[0]['remove'])
    self.assertFalse(any(Path(r['target'])==untouched for r in rows))
    ci.install_files(rows,game,root/'backups',game_check=lambda:[])
   self.assertFalse(extra.exists());self.assertEqual(untouched.read_text(),'untracked')
   self.assertEqual((extra.parent/'shared/cShade.fxh').read_text(),'old matching header')

 def test_recorded_render_target_is_unique_without_changing_uniforms_or_source(self):
  with tempfile.TemporaryDirectory() as d:
   source=Path(d)/'RECORDED.fx';text='texture texColor { Width=10; };\nsampler s { Texture=texColor; };\nuniform float Strength=1;\ntechnique REC { pass { RenderTarget=texColor; } }'
   source.write_text(text);row={'source':str(source),'target':str(source)};ri.shader_compatibility([row])
   self.assertEqual(source.read_text(),text);self.assertNotIn('texColor',row['content'])
   self.assertIn('uniform float Strength=1;',row['content']);self.assertIn('technique REC',row['content'])
   self.assertEqual(row['content'].count('FA_RecordedSceneColor'),3)


if __name__=='__main__':unittest.main()
