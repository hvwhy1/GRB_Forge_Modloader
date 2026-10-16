"""Exact, documented corrections to two known payloads, with byte proofs."""
import struct
from pathlib import Path
import batch_engine as be

WHITE_HOT_SOURCE='bc1152c29d10b6c23891bd87da7c998355febac1c9b40cab3cb188fdefeed6bf'
BAILIFF_SOURCE='d874681bfc7d867d9a53cc875dde68a26b8d22e642da5d2ed191587a27d008ad'

def repair(source,output,kind):
    source,output=Path(source),Path(output)
    raw=source.read_bytes();data=bytearray(raw)
    if kind=='white_hot_identity':
        expected=WHITE_HOT_SOURCE;offset=1;old=0x1B96EC3B83F;new=0x1CB4862A2A4
        if len(raw)!=36 or raw[0]!=0 or struct.unpack_from('<I',raw,9)[0]!=3489012060 or raw[18:34]!=b'White Hot Vision':raise ValueError('White-hot descriptor schema changed')
        reason='Author names and documents CanUseWhiteHot, but supplied root ID names CanUseNightVision; preserve the named descriptor identity.'
    elif kind=='bailiff_weapon_ui':
        expected=BAILIFF_SOURCE;offset=481;old=0x1D969D83A59;new=0x1CB5B41F5E0
        if len(raw)<489 or raw[0]!=0 or struct.unpack_from('<QI',raw,1)!=(0x1D969D75448,1393216219):raise ValueError('Bailiff clone schema changed')
        reason='WABO repurposes the clone UI target as AmmoTypeUI; redirect the existing weapon UI reference to the unchanged Bailiff WeaponUI object.'
    else:raise ValueError('Unknown payload repair')
    if be.sha256(source)!=expected or struct.unpack_from('<Q',raw,offset)[0]!=old:raise ValueError('Known payload fingerprint/reference changed')
    struct.pack_into('<Q',data,offset,new)
    if raw[:offset]!=data[:offset] or raw[offset+8:]!=data[offset+8:]:raise AssertionError('Unrelated payload bytes changed')
    output.parent.mkdir(parents=True,exist_ok=True);output.write_bytes(data)
    proof={'verified':True,'kind':kind,'source':str(source.resolve()),'sourceSha256':expected,'output':str(output.resolve()),'outputSha256':be.sha256(output),'offset':offset,'oldId':f'{old:016X}','newId':f'{new:016X}','allOtherBytesPreserved':True,'reason':reason,'gameplayVerified':False}
    be.record_json(output.with_suffix(output.suffix+'.REPAIR.json'),proof)
    return output
