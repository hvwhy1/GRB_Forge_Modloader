import struct,tempfile,unittest,zlib
from pathlib import Path
import packed_data_audit as audit

class PackedDataAudit(unittest.TestCase):
    def packet(self):
        payload=b'test';section=struct.pack('<QhBIII',audit.MAGIC,3,3,0x80008000,1,len(payload))
        section+=struct.pack('<II',len(payload),zlib.adler32(payload,0))+payload
        return section*2

    def check(self,data):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'private.data';path.write_bytes(data);return audit.audit(path)

    def test_grb_zero_seed_checksum_and_two_sections_are_accepted(self):
        proof=self.check(self.packet());self.assertTrue(proof['verified']);self.assertEqual(len(proof['sections']),2)

    def test_stored_checksum_corruption_is_reported_without_decompression(self):
        data=bytearray(self.packet());data[27]^=1
        proof=self.check(data);self.assertFalse(proof['verified']);self.assertEqual(len(proof['checksumFailures']),1)

    def test_forged_block_length_cannot_read_outside_file(self):
        data=bytearray(self.packet());struct.pack_into('<I',data,23,100000)
        with self.assertRaisesRegex(ValueError,'outside'):self.check(data)

    def test_unexpected_trailing_bytes_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'after two'):self.check(self.packet()+b'junk')
