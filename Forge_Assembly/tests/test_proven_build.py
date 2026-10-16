from pathlib import Path
import json,tempfile,unittest,struct
from unittest.mock import patch
import proven_build as pb,mod_manager as mm,batch_engine as be

class ProvenInstallTests(unittest.TestCase):
    def fixture(self,root,changed=False):
        game=root/'game';game.mkdir();(game/'GRB.exe').write_bytes(b'game-version');payload=root/'payload';payload.mkdir();files=[]
        for name in sorted(pb.FORGES):
            p=payload/name;p.write_bytes(b'proven-'+name.encode());(game/name).write_bytes(b'other-'+name.encode() if changed else p.read_bytes());files.append({'target':name,'source':str(p),'sha256':be.sha256(p),'kind':'forge'})
        p=payload/'NativeFirstPerson.ini';p.write_text('[Compatibility]\nRenderer=DX11\n');(game/p.name).write_bytes(p.read_bytes());files.append({'target':p.name,'source':str(p),'sha256':be.sha256(p),'kind':'config'})
        m=root/'PROVEN_BUILD.json';m.write_text(json.dumps({'schema':'PROVEN_GRB_BUILD_V20','gameplayVerified':True,'gameRoot':str(game),'exeSha256':be.sha256(game/'GRB.exe'),'files':files,'removeKnown':{},'nativeSettings':{}}));return game,m
    def test_reinstall_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game,m=self.fixture(root)
            with patch.object(mm,'WORK',root),patch.object(mm,'CONFIG',root/'settings.json'):
                receipt=pb.install(m,game,root/'backups',notify=lambda s:None,game_check=lambda:[])
            tx=json.loads(receipt.read_text());self.assertEqual(tx['changedForgeCount'],0);self.assertEqual(tx['files'],[])
            self.assertEqual(json.loads(Path(tx['configurationTransaction']).read_text())['files'],[])
    def test_fresh_game_installs_all_forges_and_missing_runtime_then_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game,m=self.fixture(root,True)
            (game/'NativeFirstPerson.ini').unlink()
            with patch.object(mm,'WORK',root),patch.object(mm,'CONFIG',root/'settings.json'):
                receipt=pb.install(m,game,root/'backups',notify=lambda s:None,game_check=lambda:[])
                second=pb.install(m,game,root/'backups',notify=lambda s:None,game_check=lambda:[])
            tx=json.loads(receipt.read_text());self.assertEqual(tx['changedForgeCount'],6)
            for row in json.loads(m.read_text())['files']:self.assertEqual(be.sha256(game/row['target']),row['sha256'])
            self.assertEqual(json.loads(second.read_text())['changedForgeCount'],0)
            self.assertTrue(all(Path(row['backup']).exists() for row in tx['files']))
    def test_bundle_contains_all_sources_and_can_reinstall_without_original_cache(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game,m=self.fixture(root)
            with patch.object(mm,'WORK',root),patch.object(mm,'CONFIG',root/'settings.json'):
                saved=pb.bundle(m,root/'saved',notify=lambda s:None)
                state=json.loads(saved.read_text())
                self.assertTrue(state['selfContainedPayloads'])
                self.assertTrue(all(not Path(r['source']).is_absolute() for r in state['files']))
                for row in json.loads(m.read_text())['files']:Path(row['source']).unlink()
                for file in game.glob('*.forge'):file.write_bytes(b'fresh')
                receipt=pb.install(saved,game,root/'backups',notify=lambda s:None,game_check=lambda:[])
            self.assertEqual(json.loads(receipt.read_text())['changedForgeCount'],6)
            for row in state['files']:self.assertEqual(be.sha256(game/row['target']),row['sha256'])
    def test_config_failure_rolls_back_all_forges(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game,m=self.fixture(root,True);before={p.name:p.read_bytes() for p in game.glob('*.forge')}
            with patch.object(mm,'WORK',root),patch.object(mm,'CONFIG',root/'settings.json'),patch.object(pb.ci,'install_files',side_effect=OSError('injected config failure')):
                with self.assertRaisesRegex(OSError,'injected'):pb.install(m,game,root/'backups',notify=lambda s:None,game_check=lambda:[])
            self.assertEqual(before,{p.name:p.read_bytes() for p in game.glob('*.forge')})
    def test_tampered_source_is_rejected_before_install(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game,m=self.fixture(root);state=json.loads(m.read_text());Path(state['files'][0]['source']).write_bytes(b'tampered')
            with patch.object(mm,'WORK',root):
                with self.assertRaisesRegex(ValueError,'source changed'):pb.install(m,game,root/'backups',notify=lambda s:None,game_check=lambda:[])
            self.assertFalse((root/'backups').exists())

    def test_authorized_camera_adjustment_preserves_parent_and_accepts_bounded_clip(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game,parent=self.fixture(root);state=json.loads(parent.read_text())
            ini=root/'adjusted.ini';ini.write_text('[Compatibility]\nRenderer=DX11\n[FirstPerson]\nCameraClipDistance=0.04\nCameraForwardOffset=0.18\nDisableLocomotionDOF=true\n')
            row=next(r for r in state['files'] if r['target']=='NativeFirstPerson.ini');row.update(source=str(ini),sha256=be.sha256(ini))
            state.update(gameplayVerified=False,iniAdjustmentAuthorized=True,authorizedIniAdjustmentFrom=str(parent))
            child=root/'adjustment.json';child.write_text(json.dumps(state))
            with patch.object(mm,'WORK',root):result=pb.inspect(child,game,notify=lambda s:None)
            self.assertFalse(result['gameplayVerified']);self.assertTrue(json.loads(parent.read_text())['gameplayVerified'])
            ini.write_text('[Compatibility]\nRenderer=Vulkan\n');row['sha256']=be.sha256(ini);child.write_text(json.dumps(state))
            with patch.object(mm,'WORK',root):
                with self.assertRaisesRegex(ValueError,'unsupported INI field'):pb.inspect(child,game,notify=lambda s:None)

    def test_shotgun_authorization_rejects_other_damage_values_and_payload_edits(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game,parent=self.fixture(root);state=json.loads(parent.read_text())
            row=next(r for r in state['files'] if r['target']=='DataPC_patch_01.forge');oldsha=row['sha256']
            output=root/'patched.forge';output.write_bytes(b'patched-shotguns');row.update(source=str(output),sha256=be.sha256(output))
            rows=[];fields=[];originals=[];(root/'Payloads').mkdir()
            for i in range(13):
                name=f'STG_WEAPON_{i}.GR_WeaponDBEntry';original=root/name;payload=root/'Payloads'/name
                raw=bytearray(100);struct.pack_into('<H',raw,30,40);original.write_bytes(raw);struct.pack_into('<H',raw,30,200);payload.write_bytes(raw)
                fields.append({'name':name,'originalSha256':be.sha256(original),'outputSha256':be.sha256(payload),'changes':[{'field':'damage','offset':30,'old':40,'new':200}]})
                rows.append({'name':name,'state':'REPLACED','sha256':be.sha256(payload)})
                originals.append({'name':name.removesuffix('.GR_WeaponDBEntry'),'file':str(original)})
            proof=root/'PROOF.json';fp=root/'FIELDS.json';extract=root/'EXTRACTION.json'
            proof.write_text(json.dumps(dict(verified=True,sourceUnchanged=True,independentAnvilIndexVerified=True,allUnselectedOriginalObjectsPreserved=True,completeNestedReadbackVerified=True,addedEntries=0,replacedEntries=13,sourceSha256=oldsha,outputSha256=be.sha256(output),rows=rows)))
            fp.write_text(json.dumps(fields));extract.write_text(json.dumps(dict(sourceSha256=oldsha,sourceUnchanged=True,rows=originals)))
            state.update(gameplayVerified=False,shotgunAdjustmentAuthorized=True,authorizedShotgunAdjustmentFrom=str(parent),shotgunProof=str(proof),shotgunFieldsProof=str(fp),shotgunExtraction=str(extract))
            child=root/'SHOTGUN.json';child.write_text(json.dumps(state))
            with patch.object(mm,'WORK',root):pb.inspect(child,game,notify=lambda s:None)
            fields[0]['changes'][0]['new']=300;fp.write_text(json.dumps(fields))
            with patch.object(mm,'WORK',root):
                with self.assertRaisesRegex(ValueError,'damage value'):pb.inspect(child,game,notify=lambda s:None)
            fields[0]['changes'][0]['new']=200;fp.write_text(json.dumps(fields))
            payload=root/'Payloads'/fields[0]['name'];raw=bytearray(payload.read_bytes());raw[80]=7;payload.write_bytes(raw)
            fields[0]['outputSha256']=be.sha256(payload);rows[0]['sha256']=be.sha256(payload);fp.write_text(json.dumps(fields));p=json.loads(proof.read_text());p['rows']=rows;proof.write_text(json.dumps(p))
            with patch.object(mm,'WORK',root):
                with self.assertRaisesRegex(ValueError,'unrelated bytes'):pb.inspect(child,game,notify=lambda s:None)
