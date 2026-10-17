import io,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import reshade_install as ri,config_install as ci,mod_manager as mm


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


if __name__=='__main__':unittest.main()
