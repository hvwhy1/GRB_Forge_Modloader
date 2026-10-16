import tempfile,unittest,struct
from pathlib import Path
import payload_repairs as repairs

class PayloadRepairs(unittest.TestCase):
    white=bytes.fromhex('003fb8c36eb90100005c19f6cf0110000000576869746520486f7420566973696f6e0000')

    def test_known_white_hot_payload_preserves_body_and_night_vision_identity(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'source';source.write_bytes(self.white)
            output=repairs.repair(source,Path(d)/'repaired','white_hot_identity')
            data=output.read_bytes()
            self.assertEqual(struct.unpack_from('<Q',data,1)[0],0x1CB4862A2A4)
            self.assertEqual(data[9:],self.white[9:]);self.assertEqual(source.read_bytes(),self.white)
            self.assertTrue(output.with_suffix('.REPAIR.json').is_file())

    def test_changed_white_hot_payload_is_not_repaired_by_filename(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'DBBooleanStatDescriptor_CanUseWhiteHot.DBBooleanStatDescriptor'
            modified=bytearray(self.white);modified[-1]=1;source.write_bytes(modified)
            with self.assertRaisesRegex(ValueError,'fingerprint'):repairs.repair(source,Path(d)/'output','white_hot_identity')
            self.assertFalse((Path(d)/'output').exists())

    def test_unknown_bailiff_schema_cannot_be_blindly_patched(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'HDG_UBSTG_Bailiff_Clone.GR_WeaponDBEntry';source.write_bytes(bytes(500))
            with self.assertRaisesRegex(ValueError,'schema'):repairs.repair(source,Path(d)/'output','bailiff_weapon_ui')
