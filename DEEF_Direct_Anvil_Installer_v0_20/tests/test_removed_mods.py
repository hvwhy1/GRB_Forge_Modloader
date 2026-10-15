"""Removing a mod must restore untouched destinations without touching saves."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import layered_pipeline as lp,mod_manager as mm,batch_engine as be,deployment_policy as policy

class RemovedModTests(unittest.TestCase):
    def test_removed_unlock_forge_is_restored_and_rollback_preserves_saves(self):
        import auto_mapping,forge_audit
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game=root/'game';game.mkdir();(game/'GRB.exe').write_bytes(b'game')
            main=game/'DataPC_patch_01.forge';main.write_bytes(b'STOCK PATCH')
            removed=game/'DataPC.forge';removed.write_bytes(b'UNLOCKED')
            save=root/'savegames'/'character.save';save.parent.mkdir();save.write_bytes(b'ORIGINAL CHARACTER')
            work=root/'workspace';work.mkdir();kit=root/'kit';kit.mkdir()
            old=work/'backups'/'old';old.mkdir(parents=True)
            original=old/'DataPC.forge';original.write_bytes(b'STOCK DATA')
            tx=old/'INSTALL_TRANSACTION.json'
            be.record_json(tx,{'status':'INSTALLED','gameRoot':str(game),'files':[{'target':str(removed),'backup':str(original),'originalSha256':be.sha256(original),'installedSha256':be.sha256(removed)}]})
            baseline=work/'baseline.json'
            be.record_json(baseline,{'installationPaused':False,'baselineId':'test','gameRoot':str(game),'hashes':{str(main):be.sha256(main),str(removed):be.sha256(original)}})
            cfg=work/'settings.json';be.record_json(cfg,{'last_transaction':str(tx)})
            archive=root/'Pre patch forge files for modding-1040-1.zip';archive.write_bytes(b'archive')
            payload=root/'prepatch.forge';payload.write_bytes(b'PREPATCH')
            mods={archive.name:dict(archive=str(archive),members=[main.name],enabled=True,status='INDEXED')}
            def analyze(selected,view,toolkit,run_dir,**kwargs):
                out=run_dir
                out.mkdir();report=out/'AUTO_CHARACTERIZATION.json';be.record_json(report,{})
                mapped=copy.deepcopy(selected)
                for m in mapped.values():m['routes']=[]
                return mapped,{'verifiedCount':0,'blockingUnmappedCount':0,'entries':[]},report
            with patch.object(mm,'WORK',work),patch.object(mm,'STATE',work/'mods.json'),patch.object(mm,'CONFIG',cfg),patch.object(policy,'WORK',work),patch.object(policy,'STATE',baseline),patch.object(mm,'extraction_target',return_value=payload),patch.object(auto_mapping,'analyze',side_effect=analyze),patch.object(forge_audit,'read_index',return_value={'entries':[{'id':'0000000000000100'}]}),patch.object(lp,'prepare_guided_additions',return_value=[]):
                run,report=lp.perform(mods,game,kit,root/'backups',notify=lambda _:None,inspect=lambda *a:{'verified':True,'independentAnvilIndexVerified':True,'nestedDataEntries':['parent']})
                manifest=Path(report['batch']);state=json.loads(manifest.read_text())
                self.assertEqual(state['baselineRestores'],[str(removed)])
                self.assertEqual(removed.read_bytes(),b'UNLOCKED')
                self.assertEqual(lp.inspect_stage(manifest,verify_backups=True)['baselineRestores'],1)
                installed=be.install_staged(manifest,root/'backups',game_check=lambda:[])
                self.assertEqual(removed.read_bytes(),b'STOCK DATA')
                self.assertEqual(main.read_bytes(),b'PREPATCH')
                repeat=be.install_staged(manifest,root/'backups',game_check=lambda:[])
                self.assertEqual(json.loads(repeat.read_text())['files'],[])
                self.assertEqual(save.read_bytes(),b'ORIGINAL CHARACTER')
                be.restore_install(installed,game_check=lambda:[])
                self.assertEqual(removed.read_bytes(),b'UNLOCKED')
                self.assertEqual(main.read_bytes(),b'STOCK PATCH')
                self.assertEqual(save.read_bytes(),b'ORIGINAL CHARACTER')
                state['outputs'][str(removed)]['stagedSha256']=be.sha256(payload)
                be.record_json(manifest,state)
                with self.assertRaisesRegex(ValueError,'Removed-mod restoration'):
                    lp.inspect_stage(manifest)

    def test_precombined_unlock_is_not_selected_without_explicit_unlock_archive(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game=root/'game';game.mkdir();(game/'DataPC_patch_01.forge').write_bytes(b'stock')
            mods={'combined':dict(archive=str(root/'Combined.zip'),enabled=True,status='INDEXED',members=['PrePatch-UE-IndiTables-Meta/DataPC_patch_01.forge'])}
            exclusions=[]
            self.assertEqual(lp.foundation_plan(mods,game,exclusions=exclusions),[])
            self.assertEqual(exclusions[0]['reason'],'UNLOCK_EVERYTHING_NOT_SELECTED')

if __name__=='__main__':unittest.main()
