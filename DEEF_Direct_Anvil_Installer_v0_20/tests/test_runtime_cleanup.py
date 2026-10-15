"""Private runtime removal, history and selected-input regression tests."""
import json,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import batch_engine as be,mod_manager as mm,deployment_policy as policy
import runtime_stage as rs,config_install as ci

class RuntimeCleanupTests(unittest.TestCase):
    def fixture(self,root):
        game=root/'game';game.mkdir();(game/'GRB.exe').write_bytes(b'game')
        work=root/'workspace';work.mkdir();cfg=work/'settings.json'
        return game,work,cfg
    def receipt(self,work,game,name,rows,previous=None):
        folder=work/name;folder.mkdir();records=[]
        for target,old,new in rows:
            backup=folder/'Originals'/target.relative_to(game)
            if old is not None:backup.parent.mkdir(parents=True,exist_ok=True);backup.write_bytes(old)
            import hashlib
            digest=lambda b:hashlib.sha256(b).hexdigest() if b is not None else None
            records.append(dict(target=str(target),backup=str(backup) if old is not None else None,originalSha256=digest(old),installedSha256=digest(new)))
        receipt=folder/'CONFIG_TRANSACTION.json';doc=dict(status='INSTALLED',gameRoot=str(game),files=records)
        if previous:doc['previousConfigTransaction']=str(previous)
        be.record_json(receipt,doc);return receipt
    def test_cleanup_restores_original_sound_and_removes_only_managed_added_files(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game,work,cfg=self.fixture(root)
            sound=game/'sounddata/pc/soundspc_sfx_wea_.pck';sound.parent.mkdir(parents=True);sound.write_bytes(b'AKPKmod')
            addon=game/'old-loader.dll';addon.write_bytes(b'managed loader')
            unknown=game/'unmanaged.dll';unknown.write_bytes(b'unmanaged')
            saves=root/'savegames';saves.mkdir();(saves/'1.save').write_bytes(b'character')
            tx=self.receipt(work,game,'first',[(sound,b'AKPKvanilla',b'AKPKmod'),(addon,None,b'managed loader')])
            be.record_json(cfg,dict(last_config_transaction=str(tx)))
            with patch.object(mm,'CONFIG',cfg),patch.object(mm,'WORK',work),patch.object(policy,'WORK',work):
                run=work/'stage';run.mkdir();receipt,_=rs.prepare({},game,run,notify=lambda _:None)
                state=rs.inspect(receipt,game,run)
                self.assertEqual({r['target'] for r in state['files']},{str(sound),str(addon)})
                self.assertEqual(sound.read_bytes(),b'AKPKmod');self.assertTrue(addon.exists())
                installed=rs.install(receipt,game,run,work/'backups',game_check=lambda:[])
                self.assertEqual(sound.read_bytes(),b'AKPKvanilla');self.assertFalse(addon.exists())
                self.assertEqual(unknown.read_bytes(),b'unmanaged');self.assertEqual((saves/'1.save').read_bytes(),b'character')
                rs.install(receipt,game,run,work/'backups',game_check=lambda:[])
                ci.restore_files(installed,game_check=lambda:[])
                self.assertEqual(sound.read_bytes(),b'AKPKmod');self.assertEqual(addon.read_bytes(),b'managed loader')
    def test_multiple_versions_follow_explicit_history_to_original(self):
        import runtime_history as rh
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game,work,cfg=self.fixture(root);target=game/'NativeFirstPerson.ini';target.write_bytes(b'v2')
            first=self.receipt(work,game,'first',[(target,None,b'v1')])
            second=self.receipt(work,game,'second',[(target,b'v1',b'v2')],first)
            with patch.object(policy,'WORK',work):
                plan=rh.cleanup([],game,dict(last_config_transaction=str(second)))
                self.assertTrue(plan[0]['remove']);self.assertEqual(len(plan[0]['cleanupHistory']),2)
                self.assertEqual(rh.cleanup([dict(target=str(target))],game,dict(last_config_transaction=str(second))),[])
                target.write_bytes(b'user edit')
                with self.assertRaisesRegex(ValueError,'changed outside'):rh.cleanup([],game,dict(last_config_transaction=str(second)))
    def test_runtime_cleanup_rejects_missing_backup_and_change_after_staging(self):
        import runtime_history as rh
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game,work,cfg=self.fixture(root);target=game/'options.ini';target.write_bytes(b'modded')
            tx=self.receipt(work,game,'first',[(target,b'vanilla',b'modded')]);be.record_json(cfg,dict(last_config_transaction=str(tx)))
            with patch.object(mm,'CONFIG',cfg),patch.object(mm,'WORK',work),patch.object(policy,'WORK',work):
                run=work/'stage';run.mkdir();receipt,_=rs.prepare({},game,run,notify=lambda _:None)
                target.write_bytes(b'user edit')
                with self.assertRaisesRegex(ValueError,'refusing cleanup'):rs.install(receipt,game,run,work/'backups',game_check=lambda:[])
                self.assertEqual(target.read_bytes(),b'user edit')
                target.write_bytes(b'modded');(tx.parent/'Originals/options.ini').write_bytes(b'tampered')
                with self.assertRaisesRegex(ValueError,'backup missing or changed'):rh.cleanup([],game,dict(last_config_transaction=str(tx)))
    def test_save_profile_and_outside_paths_cannot_enter_runtime_stage(self):
        from runtime_history import safe_target
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game,work,cfg=self.fixture(root)
            for path in (game/'savegames/1.save',game/'profiles/profile.ini',game/'GamerProfile.xml',game/'GRB.exe',root/'outside.ini'):
                self.assertFalse(safe_target(game,path))
            self.assertTrue(safe_target(game,game/'NativeFirstPersonOptions/Input.ini'))
    def test_deleted_archive_and_changed_archive_cannot_reuse_old_payload(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);mods=root/'mods';mods.mkdir();work=root/'workspace';work.mkdir();archive=mods/'mod.zip'
            with zipfile.ZipFile(archive,'w') as z:z.writestr('file.bin',b'old')
            with patch.object(mm,'WORK',work):
                old=mm.extraction_target(archive,'file.bin');self.assertEqual(old.read_bytes(),b'old')
                selected=mm.scan_archives(mods)
                with zipfile.ZipFile(archive,'w') as z:z.writestr('file.bin',b'new')
                new=mm.extraction_target(archive,'file.bin');self.assertEqual(new.read_bytes(),b'new');self.assertNotEqual(old,new)
                archive.unlink();self.assertEqual(mm.scan_archives(mods,selected),{})
                self.assertTrue(old.exists()) # Cached bytes remain inert, never selected by scanning.

if __name__=='__main__':unittest.main()
