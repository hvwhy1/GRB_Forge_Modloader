import hashlib,struct,tempfile,unittest,types
from pathlib import Path
from weapon_stats_merge import edit_fields

class Reader:
    sha='test-reader'
    db=types.SimpleNamespace(spreadpattern={'LMG':111,'SNR_HTI':222})
    def read(self,path):
        data=Path(path).read_bytes()
        fields={name:{'offset':offset,'type':typ,'value':struct.unpack_from(fmt,data,offset)[0]} for name,offset,typ,fmt in [('damage',20,'u16','<H'),('spreadpattern',30,'u64','<Q'),('sway',40,'float','<f'),('rateoffire',60,'u16','<H')]}
        return {'id':123,'sha256':hashlib.sha256(data).hexdigest(),'fields':fields}

class WeaponTuningTests(unittest.TestCase):
    def source(self,root):
        data=bytearray(range(100));struct.pack_into('<H',data,20,25);struct.pack_into('<Q',data,30,111);struct.pack_into('<f',data,40,1.0)
        source=root/'base.GR_WeaponDBEntry';source.write_bytes(data);return source
    def test_precision_preserves_damage_recoil_and_unrelated_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);base=self.source(root);out=root/'precision.GR_WeaponDBEntry'
            proof=edit_fields(Reader(),base,{'spreadpattern':222,'sway':0.2},out)
            allowed=set(range(30,38))|set(range(40,44))
            self.assertTrue(all(a==b for i,(a,b) in enumerate(zip(base.read_bytes(),out.read_bytes())) if i not in allowed))
            self.assertEqual(Reader().read(out)['fields']['damage']['value'],25)
            self.assertEqual(Reader().read(out)['fields']['spreadpattern']['value'],222)
            self.assertTrue(proof['allOtherBytesPreserved'])
    def test_invalid_reference_and_nonfinite_stat_rejected_before_output(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);base=self.source(root);out=root/'bad.GR_WeaponDBEntry'
            for values in ({'spreadpattern':333},{'sway':float('nan')},{'damage':0},{'invented':1}):
                with self.assertRaises(ValueError):edit_fields(Reader(),base,values,out)
                self.assertFalse(out.exists())
