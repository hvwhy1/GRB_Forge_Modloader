import copy
import json
import tempfile
import unittest
from pathlib import Path

import safe_additions

class AdditionsSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.game=self.root/'Ghost Recon Breakpoint'
        self.mods=self.root/'mods'
        self.game.mkdir();self.mods.mkdir()
        (self.game/'GRB.exe').write_bytes(b'test')
        (self.game/'DataPC_patch_01.forge').write_bytes(b'fake')
        (self.mods/'sample.zip').write_bytes(b'archive')

    def row(self,n,cl='NEW_FORGE_DATA_ENTRY_CANDIDATE',member=None):
        return dict(index=n,classification=cl,sourceArchive='sample.zip',
                    member=member or f'{n}_-_foo{n}.data',
                    parentForge=str(self.game/'DataPC_patch_01.forge'),
                    parentData=f'{n}_-_foo{n}.data',newObject='',kind='data')

    def audit(self,rows):
        return dict(schema='DEEF.new_resource_audit.2',game=str(self.game),candidates=rows)

    def test_complete_new_parent_can_only_be_privately_tested(self):
        x=safe_additions.build(self.audit([self.row(1)]),self.game,self.mods)
        self.assertEqual(x['privateTrialCount'],1)
        self.assertEqual(x['rows'][0]['reason'],'PRIVATE_TRIAL_ONLY')
        self.assertTrue(x['rows'][0]['isNewModAsset'])
        self.assertEqual(x['rows'][0]['assetStatus'],'NEW_MOD_ASSET_CANDIDATE')
        self.assertEqual(x['deployableNewResources'],0)
        self.assertFalse(x['liveInstallationEnabled'])

    def test_parent_missing_never_converts_to_replacement(self):
        r=self.row(2,'NEW_PARENT_DATA_REQUIRED')
        r['kind']='inner';r['member']='child.EntityBuilder';r['newObject']='1_-_child.EntityBuilder'
        x=safe_additions.build(self.audit([r]),self.game,self.mods)
        self.assertEqual(x['privateTrialCount'],0)
        self.assertEqual(x['rows'][0]['reason'],'NO_VERIFIED_PARENT_CONTAINER_OR_INSERTION_METHOD')
        self.assertTrue(x['rows'][0]['isNewModAsset'])

    def test_existing_inner_parent_can_be_private_tested(self):
        r=self.row(3,'NEW_INNER_OBJECT_PARENT_FOUND')
        r['kind']='inner';r['member']='child.EntityBuilder';r['newObject']='3_-_child.EntityBuilder'
        x=safe_additions.build(self.audit([r]),self.game,self.mods)
        self.assertEqual(x['privateTrialQueue'][0]['kind'],'INNER_PRIVATE_TRIAL')

    def test_duplicate_target_is_blocked_even_when_source_archives_differ(self):
        a=self.row(1);b=self.row(9)
        b['parentData']='90_-_foo1.data'
        x=safe_additions.build(self.audit([a,b]),self.game,self.mods)
        self.assertEqual(x['privateTrialCount'],0)
        self.assertTrue(all(z['reason']=='MULTIPLE_MODS_TARGET_SAME_NEW_RESOURCE' for z in x['rows']))

    def test_backup_copy_never_becomes_addition_target(self):
        backup=self.game/'Backups';backup.mkdir()
        (backup/'DataPC_patch_01.forge').write_bytes(b'fake')
        r=self.row(1);r['parentForge']=str(backup/'DataPC_patch_01.forge')
        x=safe_additions.build(self.audit([r]),self.game,self.mods)
        self.assertEqual(x['privateTrialCount'],0)
        self.assertEqual(x['rows'][0]['reason'],'ACTIVE_GAME_ROOT_FORGE_NOT_FOUND')

    def test_unsafe_member_and_absent_archive_are_blocked(self):
        a=self.row(1,member='../evil.data')
        b=self.row(2);b['sourceArchive']='missing.zip'
        x=safe_additions.build(self.audit([a,b]),self.game,self.mods)
        self.assertEqual(x['privateTrialCount'],0)
        self.assertEqual(x['rows'][0]['reason'],'UNSAFE_ARCHIVE_MEMBER')
        self.assertEqual(x['rows'][1]['reason'],'MOD_ARCHIVE_MISSING')

    def test_invalid_game_root_fails_closed(self):
        with self.assertRaises(ValueError):
            safe_additions.build(self.audit([self.row(1)]),self.root,self.mods)

if __name__=='__main__':unittest.main()
