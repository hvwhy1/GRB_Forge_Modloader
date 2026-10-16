import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import directory_repair_install as dr,batch_engine as be

class DirectoryInstallTests(unittest.TestCase):
    def fixture(self,root):
        game=root/'game';game.mkdir();(game/'GRB.exe').write_bytes(b'marker');proofs=[]
        for i,name in enumerate(sorted(dr.NAMES)[:2]):
            source=game/name;source.write_bytes(b'original'+bytes([i]));folder=root/str(i);folder.mkdir();out=folder/name;out.write_bytes(b'repaired'+bytes([i]));proof=folder/'PROOF.json'
            proof.write_text(json.dumps({'verified':True,'sourceUnchanged':True,'independentAnvilIndexVerified':True,'allPayloadAndEntryBytesPreserved':True,'populatedGlobalIndexesConsecutive':True,'sourceForge':str(source),'sourceSha256':be.sha256(source),'output':str(out),'outputSha256':be.sha256(out)}));proofs.append(proof)
        return game,proofs
    def test_mid_install_failure_restores_every_original(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game,proofs=self.fixture(root);backup=root/'backups';backup.mkdir();calls=0
            def check():
                nonlocal calls
                calls+=1
                if calls==3:raise OSError('Injected second-swap preflight failure')
                return []
            before={p.name:p.read_bytes() for p in game.glob('*.forge')}
            with patch.object(dr.policy,'require_ready',return_value={}):
                with self.assertRaisesRegex(OSError,'Injected'):dr.install(proofs,game,backup,game_check=check)
            self.assertEqual(before,{p.name:p.read_bytes() for p in game.glob('*.forge')})
            tx=json.loads(next(backup.rglob('INSTALL_TRANSACTION.json')).read_text());self.assertEqual(tx['status'],'RESTORED')
    def test_changed_live_input_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game,proofs=self.fixture(root);backup=root/'backups';backup.mkdir();p=json.loads(proofs[0].read_text());Path(p['sourceForge']).write_bytes(b'changed')
            with patch.object(dr.policy,'require_ready',return_value={}):
                with self.assertRaisesRegex(ValueError,'changed'):dr.install(proofs,game,backup,game_check=lambda:[])
            self.assertEqual(Path(p['sourceForge']).read_bytes(),b'changed');self.assertEqual(list(backup.iterdir()),[])
