import hashlib,os,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import archive_cache as cache,mod_manager as mm


class ArchiveCacheTests(unittest.TestCase):
 def archive(self,root,name='mod.zip'):
  path=root/name
  with zipfile.ZipFile(path,'w') as z:
   for i in range(4):z.writestr(str(i)+'.data',('payload'+str(i)).encode())
  return path,hashlib.sha256(path.read_bytes()).hexdigest()
 def test_one_reader_multiple_members_and_corrupt_payload_is_reextracted(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);archive,digest=self.archive(root)
   with patch.object(mm,'WORK',root/'workspace'),cache.session() as readers:
    outputs=[mm.extraction_target(archive,str(i)+'.data',digest) for i in range(4)]
    self.assertEqual(len(readers.entries),1);reader=next(iter(readers.entries.values()))['reader']
    outputs[0].write_bytes(b'corruption')
    self.assertEqual(mm.extraction_target(archive,'0.data',digest).read_bytes(),b'payload0')
   self.assertIsNone(reader.fp)
 def test_archive_edit_with_restored_timestamp_still_fails_full_verification(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);archive,digest=self.archive(root);before=archive.stat()
   with self.assertRaisesRegex(ValueError,'ZIP archive changed'):
    with cache.session():
     with cache.open_zip(archive,digest) as reader:self.assertEqual(reader.read('0.data'),b'payload0')
     data=bytearray(archive.read_bytes());data[-1]^=1;archive.write_bytes(data)
     os.utime(archive,ns=(before.st_atime_ns,before.st_mtime_ns))
 def test_reader_limit_closes_and_verifies_evicted_archive(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);a,ha=self.archive(root);b,hb=self.archive(root,'second.zip')
   with cache.session() as readers:
    readers.limit=1
    with cache.open_zip(a,ha) as first:first.read('0.data')
    with cache.open_zip(b,hb) as second:second.read('1.data')
    self.assertEqual(len(readers.entries),1);self.assertIsNone(first.fp)
   self.assertIsNone(second.fp)
 def test_original_failure_is_preserved_and_handles_close(self):
  with tempfile.TemporaryDirectory() as d:
   archive,digest=self.archive(Path(d))
   with self.assertRaisesRegex(RuntimeError,'packing failed'):
    with cache.session():
     with cache.open_zip(archive,digest) as reader:reader.read('0.data')
     raise RuntimeError('packing failed')
   self.assertIsNone(reader.fp)


if __name__=='__main__':unittest.main()
