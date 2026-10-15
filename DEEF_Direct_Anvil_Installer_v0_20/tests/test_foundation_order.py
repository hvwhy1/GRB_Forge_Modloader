import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import layered_pipeline as lp
import mod_manager as mm

class FoundationOrderTests(unittest.TestCase):
    def fixture(self,root):
        game=root/'game';game.mkdir();(game/'GRB.exe').write_bytes(b'game')
        for name in ('DataPC.forge','DataPC_patch_01.forge','DataPC_Resources_patch_01.forge'):(game/name).write_bytes(b'stock')
        def mod(name,members):return {'archive':str(root/name),'enabled':True,'status':'INDEXED','members':members}
        mods={'z-prepatch':mod('Pre patch forge files for modding-1040-1.zip',['DataPC_patch_01.forge','DataPC_Resources_patch_01.forge']),
              'a-unlock':mod('Unlock Everything-548-v2.rar',['DataPC.forge','DataPC_patch_01.forge'])}
        return game,mods

    def test_prepatch_first_regardless_archive_order(self):
        with tempfile.TemporaryDirectory() as d:
            game,mods=self.fixture(Path(d))
            a=lp.foundation_plan(mods,game);b=lp.foundation_plan(dict(reversed(list(mods.items()))),game)
            self.assertEqual(a,b)
            self.assertEqual([r['phase'] for r in a],['PREPATCH','PREPATCH','COMPLETE_FORGE','COMPLETE_FORGE'])
            self.assertEqual([r['mod'] for r in a if Path(r['forge']).name=='DataPC_patch_01.forge'],['z-prepatch','a-unlock'])

    def test_competing_same_phase_forbidden(self):
        with tempfile.TemporaryDirectory() as d:
            game,mods=self.fixture(Path(d));mods['duplicate']=dict(mods['a-unlock'],archive=str(Path(d)/'Other.rar'))
            with self.assertRaisesRegex(ValueError,'Competing'):lp.foundation_plan(mods,game)

    def test_backup_and_combined_alternative_are_reported(self):
        with tempfile.TemporaryDirectory() as d:
            game,mods=self.fixture(Path(d))
            mods['backup']=dict(mods['a-unlock'],archive=str(Path(d)/'Essentials.7z'),members=['Initial Backups/DataPC_patch_01.forge'])
            mods['combined']=dict(mods['a-unlock'],archive=str(Path(d)/'Experimental.7z'),members=['PrePatch-UE-IndiTables-Meta/DataPC_patch_01.forge'])
            with self.assertRaisesRegex(ValueError,'Precombined'):lp.foundation_plan(mods,game)
            exclusions=[];rows=lp.foundation_plan(mods,game,True,exclusions)
            self.assertEqual(len(rows),4);self.assertEqual(len(exclusions),2)

    def test_directory_with_forge_suffix_not_payload(self):
        output='header\n----------\nPath = Put in DataPC.forge\nSize = 0\nAttributes = D\nFolder = +\n\nPath = DataPC.forge\nSize = 100\nAttributes = A\nFolder = -\n\n'
        with patch.object(mm,'sevenzip',return_value='7z'),patch.object(mm.subprocess,'run') as run:
            run.return_value.returncode=0;run.return_value.stdout=output
            self.assertEqual(mm.archive_inventory('test.rar'),['DataPC.forge'])

    def test_whole_forge_cap_does_not_raise_inner_resource_cap(self):
        with patch.object(mm,'MAX_RESOURCE_BYTES',4):
            with self.assertRaises(ValueError):mm._copy_bounded(io.BytesIO(b'12345'),io.BytesIO())
            dest=io.BytesIO();self.assertEqual(mm._copy_bounded(io.BytesIO(b'12345'),dest,max_bytes=10),5)

    def test_hash_chain_rejects_reversed_layers_before_live_write(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game,mods=self.fixture(root);rows=lp.foundation_plan(mods,game)
            source=str(game/'DataPC_patch_01.forge')
            layers=[dict(r,inputSha256='stock',outputSha256='new',anvilVerified=True,indexEntries=1) for r in rows if r['forge']==source]
            document={'writer':lp.WRITER,'status':'VERIFIED_STAGED','installationOrder':list(lp.PHASES),
                'plan':{'gameRoot':str(game),'sourceHashes':{source:'stock'}},'foundationLayers':list(reversed(layers))}
            file=root/'batch.json';file.write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError,'hash chain|order'):lp.inspect_stage(file)

if __name__=='__main__':unittest.main()

class LayeredBuildTests(unittest.TestCase):
    def test_resource_edits_use_unlock_result_and_stale_source_is_rejected(self):
        import copy
        import batch_engine as be
        import deployment_policy as policy
        import existing_stage
        import auto_mapping
        import forge_audit
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game=root/'game';game.mkdir();(game/'GRB.exe').write_bytes(b'game')
            forge=game/'DataPC_patch_01.forge';forge.write_bytes(b'STOCK')
            work=root/'workspace';work.mkdir();kit=root/'toolkit';kit.mkdir();(kit/'AnvilToolkit.dll').write_bytes(b'dll');(kit/'Libs').mkdir()
            mods={}
            payloads={}
            for name,body in [('Pre patch forge files for modding-1040-1.zip',b'PREPATCH'),('Unlock Everything-548-v2.rar',b'UNLOCK')]:
                archive=root/name;archive.write_bytes(body+b'archive');payload=root/(name+'.forge');payload.write_bytes(body)
                mods[name]={'archive':str(archive),'members':[forge.name],'enabled':True,'status':'INDEXED'};payloads[str(archive)]=payload
            resource=root/'Tweaks.zip';resource.write_bytes(b'archive');mods[resource.name]={'archive':str(resource),'members':['edit.bin'],'enabled':True,'status':'INDEXED'}
            be.record_json(work/'settings.json',{'complete_content_pipeline':False}) # This fixture isolates legacy route staging.
            baseline=work/'recovery_state.json';be.record_json(baseline,{'installationPaused':False,'baselineId':'test','gameRoot':str(game),'hashes':{str(forge):be.sha256(forge)}})
            def analyze(selected,view,toolkit,run_dir,**kwargs):
                self.assertEqual((view/forge.name).read_bytes(),b'UNLOCK')
                route={'kind':'inner','forge':str(view/forge.name),'data':'0_-_Container.data','object':'0_-_Object.Type','member':'edit.bin','sourceArchive':selected[resource.name]['archive']}
                mapped=copy.deepcopy(selected);mapped[resource.name]['routes']=[route]
                mapped['Installer gameplay options']={'enabled':False,'members':[],'routes':[]}
                run_dir.mkdir();report=run_dir/'AUTO_CHARACTERIZATION.json';be.record_json(report,{})
                return mapped,{'verifiedCount':1,'blockingUnmappedCount':0},report
            def stage(plan,out,**kwargs):
                self.assertTrue(kwargs['private_only']);out.mkdir();source=Path(plan['routes'][0]['forge']);dest=out/forge.name
                self.assertEqual(source.read_bytes(),b'UNLOCK');dest.write_bytes(source.read_bytes()+b'+EDIT')
                receipt=out/'proof.json';be.record_json(receipt,dict.fromkeys(existing_stage.REQUIRED_RECEIPT_FLAGS,True)|{'requestedEdits':1,'stagedForgeSha256':be.sha256(dest),'sourceSha256':be.sha256(source)})
                entry={'relative':source.name,'staged':str(dest),'sourceSha256':be.sha256(source),'stagedSha256':be.sha256(dest),'streamReceipt':str(receipt)}
                state={'schema':3,'writer':'FORGE_STREAM_BATCH_V27','privateOnly':True,'status':'VERIFIED_STAGED','plan':plan,'outputs':{str(source):entry},'finishedRoutes':plan['routes'],'excludedRoutes':[]}
                be.record_json(out/'BATCH_RESULT.json',state);return state
            with patch.object(mm,'WORK',work),patch.object(mm,'STATE',work/'mods.json'),patch.object(mm,'CONFIG',work/'settings.json'),patch.object(policy,'WORK',work),patch.object(policy,'STATE',baseline),patch.object(mm,'extraction_target',side_effect=lambda archive,*args,**kwargs:next(v for k,v in payloads.items() if Path(k).name==Path(archive).name)),patch.object(auto_mapping,'analyze',side_effect=analyze),patch.object(be,'stage_plan_stream',side_effect=stage),patch.object(forge_audit,'read_index',return_value={'entries':[{'id':'0000000000000100'}]}),patch.object(lp,'prepare_guided_additions',return_value=[]):
                run,report=lp.perform(mods,game,kit,root/'backups',notify=lambda s:None,inspect=lambda *args:{'verified':True,'independentAnvilIndexVerified':True,'nestedDataEntries':['container']})
                manifest=Path(report['batch']);state=json.loads(manifest.read_text())
                self.assertEqual(Path(state['outputs'][str(forge)]['staged']).read_bytes(),b'UNLOCK+EDIT')
                self.assertEqual(forge.read_bytes(),b'STOCK')
                self.assertEqual(lp.inspect_stage(manifest)['foundationSteps'],2)
                policy.require_install(state,manifest)
                transaction=be.install_staged(manifest,root/'backups',game_check=lambda:[])
                self.assertEqual(forge.read_bytes(),b'UNLOCK+EDIT')
                be.restore_install(transaction,game_check=lambda:[])
                self.assertEqual(forge.read_bytes(),b'STOCK')
                # Stale resources packed against PREPATCH cannot erase UNLOCK.
                innerfile=Path(state['resourceManifest']);inner=json.loads(innerfile.read_text());inner['outputs'][str(run/'ComposedGame'/forge.name)]['sourceSha256']=be.sha256(payloads[str(root/'Pre patch forge files for modding-1040-1.zip')])
                inner['plan']['sourceHashes']=dict.fromkeys(inner['outputs'],inner['outputs'][str(run/'ComposedGame'/forge.name)]['sourceSha256']);be.record_json(innerfile,inner)
                with self.assertRaisesRegex(ValueError,'earlier Forge layer'):lp.inspect_stage(manifest)


class ArchiveSnapshotTests(unittest.TestCase):
    def test_snapshot_survives_original_download_deletion(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);archive=root/'mod.zip';archive.write_bytes(b'pinned archive')
            mods={'mod.zip':{'archive':str(archive),'enabled':True,'members':[]}}
            run=root/'run';run.mkdir()
            pinned=lp.snapshot_archives(mods,run)
            archive.unlink()
            self.assertEqual(Path(pinned['mod.zip']['archive']).read_bytes(),b'pinned archive')
            self.assertEqual(mods['mod.zip']['archive'],str(archive))
            self.assertTrue((run/'ARCHIVE_SNAPSHOT.json').is_file())

class ManualGuideTests(unittest.TestCase):
    def test_essentials_enriched_prepatch_follows_raw_prepatch(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game,mods=FoundationOrderTests().fixture(root)
            mods['starter']={'archive':str(root/'Breakpoint Modding Starter Essentials Pack-1492-1.7z'),
                'enabled':True,'status':'INDEXED','members':['Initial Backups/DataPC_patch_01.forge','Initial Backups/DataPC_Resources_patch_01.forge']}
            rows=lp.foundation_plan(mods,game)
            self.assertEqual([r['tier'] for r in rows if r['phase']=='PREPATCH'],[0,0,1,1])
            self.assertEqual([r['mod'] for r in rows if Path(r['forge']).name=='DataPC_patch_01.forge'],['z-prepatch','starter','a-unlock'])

    def test_nightmare_guide_resolves_parent_without_trusting_ordinal(self):
        import manual_install_rules
        forges={'datapc_patch_01.forge':[Path('DataPC_patch_01.forge')],
                'datapc_tgt_worldmap_bootstrap_split_patch_01.forge':[Path('DataPC_TGT_WorldMap_Bootstrap_Split_patch_01.forge')]}
        def inspect(forge,selector):
            return {'nestedDataEntries':['99_-_DBContainerEntry_0X104634F921.data'] if forge.name=='DataPC_patch_01.forge' else ['77_-_TGT_WorldMap.data']}
        db=manual_install_rules.candidates('Nightmare Mode-1102-v2.zip','New Reinforcements/1_-_SC_TGT_Caller.DBSoldierConfig',forges,inspect)
        world=manual_install_rules.candidates('Nightmare Mode-1102-v2.zip','New Reinforcements/1_-_WaveSpawner_Test.WaveSpawner',forges,inspect)
        self.assertEqual(db[0]['data'],'99_-_DBContainerEntry_0X104634F921.data')
        self.assertEqual(world[0]['data'],'77_-_TGT_WorldMap.data')
        self.assertEqual(world[0]['kind'],'inner')
        self.assertEqual(manual_install_rules.candidates('Unrelated.zip','1_-_SC_TGT_Caller.DBSoldierConfig',forges,inspect),[])

class CallerPriorityTests(unittest.TestCase):
    def test_explicit_new_reinforcements_choice_only_excludes_overlapping_traffic_files(self):
        import manual_install_rules as rules
        with patch.object(mm,'load',return_value={'nightmare_caller_priority':'New Reinforcements'}):
            archive='Nightmare Mode-1102-v2.zip'
            self.assertIsNone(rules.excluded_variant(archive,'New Reinforcements/1_-_SC_TGT_Caller.DBSoldierConfig'))
            self.assertIn('User selected',rules.excluded_variant(archive,'Traffic Improvements/1_-_SC_TGT_Caller.DBSoldierConfig'))
            self.assertIsNone(rules.excluded_variant(archive,'Traffic Improvements/1_-_DBTrafficCrew.DBTrafficVehicleCrew'))

class StaleStageGuardTests(unittest.TestCase):
    def test_resource_only_stage_cannot_erase_selected_forge_mods(self):
        import deployment_policy as policy
        import batch_engine as be
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game=root/'game';game.mkdir();source=game/'DataPC.forge';source.write_bytes(b'stock')
            work=root/'workspace';work.mkdir();payload=work/'stage.forge';payload.write_bytes(b'older resource-only output')
            baseline=work/'baseline.json';be.record_json(baseline,{'installationPaused':False,'baselineId':'test','gameRoot':str(game),'hashes':{str(source):be.sha256(source)}})
            be.record_json(work/'mods.json',{'Unlock Everything.rar':{'enabled':True,'members':['DataPC.forge']}})
            manifest=work/'BATCH_RESULT.json';be.record_json(manifest,{'writer':'FORGE_STREAM_BATCH_V27','status':'VERIFIED_STAGED','productVersion':policy.VERSION,'baselineId':'test','plan':{'gameRoot':str(game)},'outputs':{str(source):{'sourceSha256':be.sha256(source),'stagedSha256':be.sha256(payload),'staged':str(payload)}}})
            with patch.object(policy,'STATE',baseline),patch.object(policy,'WORK',work):
                with self.assertRaisesRegex(ValueError,'layered build'):be.install_staged(manifest,root/'backups',game_check=lambda:[])
            self.assertEqual(source.read_bytes(),b'stock');self.assertFalse((root/'backups').exists())
