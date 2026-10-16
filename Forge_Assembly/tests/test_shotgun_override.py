import hashlib,struct,tempfile,types,unittest
from pathlib import Path
from weapon_stats_merge import AuthorReader,edit_damage
from shotgun_override import player_shotgun


def prefix_reader():
    reader=object.__new__(AuthorReader);reader.sha='synthetic-reader';reader.db=types.SimpleNamespace()
    def author_prefix():
        typesearch('damage anchor',0xAABBCCDD,'u32',1)
        statset(4,'damage','u16')
        typesearch('unsupported custom ammo',0xFFFFFFFF,'u32',1)
    reader.reader=author_prefix.__code__
    return reader


class ShotgunTests(unittest.TestCase):
    def test_partial_damage_read_preserves_custom_ammo_and_every_other_byte(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)/'STG_M4.GR_WeaponDBEntry';out=Path(folder)/'patched.GR_WeaponDBEntry'
            raw=bytearray(150);struct.pack_into('<Q',raw,1,123)
            struct.pack_into('<IH',raw,40,0xAABBCCDD,40);base.write_bytes(raw)
            reader=prefix_reader()
            with self.assertRaisesRegex(ValueError,'unsupported custom ammo'):reader.read(base)
            proof=edit_damage(reader,base,200,out)
            self.assertEqual(struct.unpack_from('<H',out.read_bytes(),44)[0],200)
            self.assertEqual(out.read_bytes()[:44],raw[:44]);self.assertEqual(out.read_bytes()[46:],raw[46:])
            self.assertTrue(proof['allOtherBytesPreserved'])
    def test_missing_anchor_and_invalid_requests_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)/'STG_M4.GR_WeaponDBEntry';base.write_bytes(bytes(150));reader=prefix_reader()
            with self.assertRaisesRegex(ValueError,'anchor missing'):reader.read(base,{'damage'})
            for fields in (set(),{'invented'}):
                with self.assertRaises(ValueError):reader.read(base,fields)
    def test_player_selection_does_not_buff_npcs_or_sidearm(self):
        for name in ('STG_M4','Unique_STG_BOSG12','Y1E3_STG_AA12','YEE2_STG_AA12_Custom_Skin'):
            self.assertTrue(player_shotgun(name+'.GR_WeaponDBEntry'))
        for name in ('HDG_UBSTG_Bailiff_Clone','STG_KSG12_NPC','STG_RU12SG_ASU_IA','STG_BenelliM4_PROG_TEST','Unique_STG_KSG12_Silverback','ASR_4AC'):
            self.assertFalse(player_shotgun(name+'.GR_WeaponDBEntry'))
