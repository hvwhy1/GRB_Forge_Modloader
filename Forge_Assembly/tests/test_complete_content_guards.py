import struct,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import auto_mapping as am
import complete_content as cc
import reshade_install as ri


class CompleteContentGuards(unittest.TestCase):
    def test_helper_executable_never_becomes_a_game_resource(self):
        name='WABO Compatibility Tool-1585-1-1-2.zip'
        mods={name:{'archive':name,'members':['WABO Compatibility Tool.exe'],'enabled':True}}
        with patch.object(am,'initial_characterize',return_value={}),patch.object(cc.be,'sha256',side_effect=AssertionError('Helper must not enter asset staging')):
            self.assertEqual(cc.inventory(mods),({},{}))
        self.assertEqual(am.candidates_for_member(name,'WABO Compatibility Tool.exe',{})[2],'IGNORED_SUPPORT_FILE')

    def runtime(self,exports):
        data=bytearray(2048);data[:2]=b'MZ';struct.pack_into('<I',data,60,128);data[128:132]=b'PE\0\0'
        struct.pack_into('<HH',data,132,0x8664,1);struct.pack_into('<H',data,148,240);struct.pack_into('<H',data,152,0x20b)
        struct.pack_into('<I',data,264,4096);struct.pack_into('<IIII',data,400,1536,4096,1536,512)
        struct.pack_into('<I',data,536,len(exports));struct.pack_into('<I',data,544,4136)
        pos=600
        for i,name in enumerate(exports):
            struct.pack_into('<I',data,552+4*i,4096+pos-512);raw=name.encode()+b'\0';data[pos:pos+len(raw)]=raw;pos+=len(raw)
        return data

    def test_addon_runtime_requires_actual_x64_dx11_and_addon_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'runtime.dll'
            exports=['D3D11CreateDevice','D3D11CreateDeviceAndSwapChain','ReShadeRegisterAddon']
            file.write_bytes(self.runtime(exports));self.assertEqual(ri.addon_runtime(file),file.read_bytes())
            file.write_bytes(self.runtime(exports[:2]))
            with self.assertRaisesRegex(ValueError,'exports'):ri.addon_runtime(file)
            data=self.runtime(exports);struct.pack_into('<H',data,132,0x14c);file.write_bytes(data)
            with self.assertRaisesRegex(ValueError,'x64'):ri.addon_runtime(file)
