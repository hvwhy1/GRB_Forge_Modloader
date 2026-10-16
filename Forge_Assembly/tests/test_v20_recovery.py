import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import auto_mapping
import auto_additions
import deployment_policy as policy
import batch_engine
from types import SimpleNamespace


class V20RecoveryTests(unittest.TestCase):
    def test_running_game_guard_includes_ubisoft_plus_variants(self):
        result=SimpleNamespace(returncode=0,stdout='"GRB_UPP.exe","123","Console","1","10 K"\n"GRB_UPP_vulkan.exe","456","Console","1","10 K"')
        with patch.object(batch_engine.subprocess,'run',return_value=result):
            self.assertEqual(batch_engine.game_processes_running(),['grb_upp.exe','grb_upp_vulkan.exe'])
    def test_missing_baseline_blocks_install(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(policy,'STATE',Path(folder)/'missing.json'):
            with self.assertRaisesRegex(RuntimeError,'verification'):
                policy.require_install({},Path(folder)/'batch.json')

    def test_old_crash_stage_and_private_additions_cannot_install(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game=root/'game';game.mkdir()
            baseline=root/'baseline.json'
            baseline.write_text(json.dumps({'installationPaused':False,'baselineId':'new',
                'gameRoot':str(game),'hashes':{}}))
            state={'plan':{'gameRoot':str(game)},'outputs':{},'baselineId':'new'}
            with patch.object(policy,'WORK',root),patch.object(policy,'STATE',baseline):
                with self.assertRaisesRegex(ValueError,'older build'):
                    policy.require_install(state,root/'old.json')
                state.update(productVersion=policy.VERSION,additionSummary={'added':1})
                with self.assertRaisesRegex(ValueError,'private'):
                    policy.require_install(state,root/'new.json')
                state['additionSummary']['added']=0
                self.assertEqual(policy.require_install(state,root/'new.json')['baselineId'],'new')

    def fixture(self,root):
        game=root/'game';game.mkdir();(game/'GRB.exe').write_text('marker')
        for name in ('DataPC.forge','DataPC_patch_01.forge'):(game/name).write_bytes(b'private fixture')
        kit=root/'kit';kit.mkdir();(kit/'Libs').mkdir();(kit/'AnvilToolkit.dll').write_bytes(b'fixture')
        archive=root/'sample.zip'
        member='DataPC_patch_01.forge/123_-_WI_TEST.data/0_-_WI_TEST.EntityBuilder'
        with zipfile.ZipFile(archive,'w') as z:z.writestr(member,b'fixture')
        mods={archive.name:{'archive':str(archive),'enabled':True,'status':'INDEXED','members':[member]}}
        return game,kit,mods,archive,member

    def test_missing_patch_parent_routes_to_complete_base_parent(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game,kit,mods,_,_=self.fixture(root)
            def inspect(toolkit,forge,data,work,log):
                return {'verified':True,'writerCapability':'VERIFIED_STREAM_REPLACEMENT_V27',
                    'nestedDataEntries':['123_-_WI_TEST.data'] if forge.name=='DataPC.forge' else [],
                    'nestedObjects':['0_-_WI_TEST.EntityBuilder'] if data!='AUTO' else []}
            with patch.object(auto_mapping,'ROOT',root):
                _,report,_=auto_mapping.analyze(mods,game,kit,run_dir=root/'scan',inspect=inspect,full_game_scan=True)
            self.assertEqual(report['verifiedCount'],1)
            self.assertEqual(Path(report['routes'][0]['forge']).name,'DataPC.forge')
            self.assertEqual(report['entries'][0]['redirectedParent']['reason'],'EXISTING_COMPLETE_PARENT_IN_OTHER_FORGE')

    def test_duplicate_base_parent_is_held_instead_of_guessed(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game,kit,mods,_,_=self.fixture(root)
            def inspect(toolkit,forge,data,work,log):
                return {'verified':True,'writerCapability':'VERIFIED_STREAM_REPLACEMENT_V27',
                    'nestedDataEntries':['123_-_WI_TEST.data','456_-_WI_TEST.data'] if forge.name=='DataPC.forge' else [],
                    'nestedObjects':[]}
            with patch.object(auto_mapping,'ROOT',root):
                _,report,_=auto_mapping.analyze(mods,game,kit,run_dir=root/'scan',inspect=inspect,full_game_scan=True)
            self.assertEqual(report['verifiedCount'],0)
            self.assertIn('EXISTING_BASE_PARENT_AMBIGUOUS',report['entries'][0]['explanation'])

    def test_addition_writer_does_not_synthesize_an_existing_base_parent(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game,kit,_,archive,member=self.fixture(root)
            audit=root/'audit.json'
            audit.write_text(json.dumps({'schema':'DEEF.new_resource_audit.2','game':str(game),'candidates':[
                {'index':1,'sourceArchive':archive.name,'member':member,'kind':'inner',
                 'parentForge':str(game/'DataPC_patch_01.forge'),'parentData':'123_-_WI_TEST.data',
                 'newObject':'0_-_WI_TEST.EntityBuilder','reason':'DATA_PARENT_ABSENT'}]}))
            def forbidden(*args):raise AssertionError('Must not synthesize a partial base-parent override')
            with patch.object(auto_additions,'ROOT',root),patch.object(auto_additions,'_disabled_mods',return_value=[]):
                _,report=auto_additions.stage(audit,game,root,kit,bridge_runner=forbidden,pack_runner=forbidden,
                    inspector=lambda f:['123_-_WI_TEST.data'] if Path(f).name=='DataPC.forge' else [],notify=lambda _:None)
            self.assertEqual(report['verifiedAdditionCount'],0)
            self.assertTrue(any('EXISTING_BASE_PARENT' in r['reason'] for r in report['excluded']))


if __name__=='__main__':unittest.main()
