import json,tempfile,unittest
from pathlib import Path
import performance_trace as perf,batch_engine as be


class TraceTests(unittest.TestCase):
 def test_hash_is_still_read_and_changed_bytes_never_reuse_a_digest(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);path=root/'file.forge';path.write_bytes(b'before')
   @perf.profiled
   def operation():
    perf.bind_run(root);first=be.sha256(path);path.write_bytes(b'after!');second=be.sha256(path)
    self.assertNotEqual(first,second)
   operation();report=json.loads((root/'PERFORMANCE.json').read_text())
   self.assertTrue(report['completed']);self.assertEqual(report['metrics']['hash.forge']['calls'],2)
   self.assertEqual(report['metrics']['hash.forge']['bytes'],12)
 def test_failed_run_profile_follows_archived_reports_without_hiding_failure(self):
  with tempfile.TemporaryDirectory() as d:
   w=Path(d);run=w/'auto_runs/failed';run.mkdir(parents=True)
   @perf.profiled
   def fail():
    perf.bind_run(run);target=w/'diagnostics/run_history/failed';target.parent.mkdir(parents=True);run.rename(target)
    raise RuntimeError('original stage failure')
   with self.assertRaisesRegex(RuntimeError,'original stage failure'):fail()
   report=json.loads((w/'diagnostics/run_history/failed/PERFORMANCE.json').read_text());self.assertFalse(report['completed'])
 def test_install_metrics_use_separate_filename(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'PERFORMANCE.json').write_text('stage report')
   @perf.profiled(filename='PERFORMANCE_INSTALL.json')
   def install():perf.bind_run(root)
   install();self.assertEqual((root/'PERFORMANCE.json').read_text(),'stage report');self.assertTrue((root/'PERFORMANCE_INSTALL.json').is_file())


if __name__=='__main__':unittest.main()
