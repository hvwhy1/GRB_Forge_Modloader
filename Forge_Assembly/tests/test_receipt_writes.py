import errno,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import batch_engine as be,config_install as ci,friendly_errors


def locked(code=5):
    exc=PermissionError('simulated Windows file lock');exc.winerror=code;return exc


class ReceiptWriteTests(unittest.TestCase):
    def test_transient_windows_lock_retries_atomic_publication(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'receipt.json';path.write_text('{"old":true}');replace=be.os.replace;attempts=[]
            def transient(source,target):
                attempts.append(source)
                if len(attempts)<3:
                    self.assertEqual(json.loads(path.read_text()),{'old':True})
                    raise locked()
                return replace(source,target)
            with patch.object(be.os,'replace',side_effect=transient),patch.object(be.time,'sleep') as sleep:
                be.record_json(path,{'new':True})
            self.assertEqual(json.loads(path.read_text()),{'new':True});self.assertEqual(sleep.call_count,2)
            self.assertFalse(list(Path(d).glob('*.pending')))

    def test_persistent_lock_keeps_old_receipt_and_reports_exact_file(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'receipt.json';path.write_bytes(b'{"old":true}')
            with patch.object(be.os,'replace',side_effect=locked(32)),patch.object(be.time,'sleep') as sleep:
                with self.assertRaisesRegex(PermissionError,'RECEIPT_WRITE_BLOCKED') as caught:be.record_json(path,{'new':True})
            self.assertEqual(sleep.call_count,8);self.assertEqual(path.read_bytes(),b'{"old":true}')
            self.assertFalse(list(Path(d).glob('*.pending')))
            detail=friendly_errors.explain(caught.exception,Path(d))
            self.assertIn(str(path),detail[1])

    def test_disk_error_is_not_treated_as_a_transient_lock(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(be.os,'replace',side_effect=OSError(errno.ENOSPC,'disk full')),patch.object(be.time,'sleep') as sleep:
                with self.assertRaises(OSError):be.record_json(Path(d)/'receipt.json',{})
            sleep.assert_not_called()

    def test_many_shader_files_have_complete_recovery_before_first_game_write(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);game=root/'game';game.mkdir();(game/'GRB.exe').write_bytes(b'exe');backups=root/'backups'
            plan=[]
            for index in range(40):
                target=game/'reshade-shaders/Shaders'/('effect'+str(index)+'.fx');target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(b'old shader');plan.append({'target':str(target),'content':'new shader'})
            replace=be.os.replace;states=[];blocked=False
            def transient(source,target):
                nonlocal blocked
                target=Path(target)
                if target.name=='CONFIG_TRANSACTION.json':
                    state=json.loads(Path(source).read_text());states.append(state['status'])
                    if state['status']=='INSTALLING' and not blocked:blocked=True;raise locked()
                elif target.suffix=='.fx':
                    receipt=next(backups.glob('*/CONFIG_TRANSACTION.json'));doc=json.loads(receipt.read_text())
                    self.assertEqual(doc['status'],'INSTALLING');self.assertEqual(len(doc['files']),40)
                    for row in doc['files']:self.assertEqual(Path(row['backup']).read_bytes(),b'old shader')
                return replace(source,target)
            with patch.object(be.os,'replace',side_effect=transient),patch.object(be.time,'sleep'):
                receipt=ci.install_files(plan,game,backups,game_check=lambda:[])
            self.assertTrue(blocked);self.assertEqual(states,['PREPARING','INSTALLING','INSTALLING','INSTALLED'])
            self.assertTrue(all(Path(row['target']).read_bytes()==b'new shader' for row in plan))
            ci.restore_files(receipt,game_check=lambda:[])
            self.assertTrue(all(Path(row['target']).read_bytes()==b'old shader' for row in plan))


if __name__=='__main__':unittest.main()
