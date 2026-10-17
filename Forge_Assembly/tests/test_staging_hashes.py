import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import batch_engine as be,staging_hashes as hashes,performance_trace as perf
import incremental_stage as inc,mod_manager as mm


class StagingHashTests(unittest.TestCase):
 def test_operation_cache_is_bounded_and_eviction_falls_back_to_hashing(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);files=[root/(str(i)+'.forge') for i in range(3)]
   for i,file in enumerate(files):file.write_bytes(str(i).encode())
   with hashes.session(),patch.object(hashes,'LIMIT',2):
    values=[be.sha256(p) for p in files];self.assertEqual(len(hashes.current()),2)
    self.assertEqual(be.sha256(files[0]),values[0]);self.assertEqual(len(hashes.current()),2)
 def test_same_file_and_hardlink_read_once_but_timestamp_restored_edit_is_rehashed(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);file=root/'source.forge';file.write_bytes(b'original');alias=root/'alias.forge'
   try:os.link(file,alias)
   except OSError:self.skipTest('Hard links unavailable')
   @perf.profiled
   def stage():
    perf.bind_run(root)
    with hashes.session():
     first=be.sha256(file);self.assertEqual(first,mm.fingerprint(file));self.assertEqual(first,be.sha256(alias))
     before=file.stat();file.write_bytes(b'modified');os.utime(file,ns=(before.st_atime_ns,before.st_mtime_ns))
     self.assertNotEqual(first,be.sha256(alias))
   stage();metrics=json.loads((root/'PERFORMANCE.json').read_text())['metrics']
   self.assertEqual(metrics['hash.forge']['bytes'],16);self.assertEqual(metrics['hash.reused']['calls'],2)

 def test_new_operation_and_install_inside_a_stage_always_read_fresh_bytes(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);file=root/'source.forge';file.write_bytes(b'bytes')
   @hashes.for_staging
   def operation(install=False):
    if install:self.assertIsNone(hashes.current())
    be.sha256(file);be.sha256(file)
   @perf.profiled
   def stage():
    perf.bind_run(root);operation();operation()
    with hashes.session():operation(install=True)
   stage();metrics=json.loads((root/'PERFORMANCE.json').read_text())['metrics']
   self.assertEqual(metrics['hash.forge']['calls'],4);self.assertEqual(metrics['hash.reused']['calls'],2)
   self.assertIsNone(hashes.current())

 def test_unsupported_metadata_falls_back_to_full_reads(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);file=root/'archive.zip';file.write_bytes(b'bytes')
   @perf.profiled
   def stage():
    perf.bind_run(root)
    with hashes.session(),patch.object(hashes,'version',return_value=None):be.sha256(file);be.sha256(file)
   stage();metrics=json.loads((root/'PERFORMANCE.json').read_text())['metrics']
   self.assertEqual(metrics['hash.zip']['bytes'],10);self.assertNotIn('hash.reused',metrics)

 def test_file_changing_during_hash_is_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   file=Path(d)/'data.forge';file.write_bytes(b'bytes')
   with hashes.session(),patch.object(hashes,'version',side_effect=[('before',),('after',)]):
    with self.assertRaisesRegex(ValueError,'changed while hashing'):be.sha256(file)

 def test_ui_and_runtime_changes_do_not_invalidate_forges_but_builder_changes_do(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'src').mkdir();(root/'data').mkdir()
   for name in ('app_v20.py','reshade_install.py','complete_content.py'):(root/name).write_text('first')
   with patch.object(mm,'ROOT',root):
    first=inc.revision();(root/'app_v20.py').write_text('new panel');(root/'reshade_install.py').write_text('shader repair')
    self.assertEqual(first,inc.revision())
    (root/'complete_content.py').write_text('changed builder');self.assertNotEqual(first,inc.revision())
    second=inc.revision();(root/'new_builder.py').write_text('new unknown component');self.assertNotEqual(second,inc.revision())


if __name__=='__main__':unittest.main()
