"""Independent GRB .data block boundaries and Adler-32 audit (no decompression)."""
import struct,zlib,json
from pathlib import Path

MAGIC=1154322941026740787

def audit(path):
    path=Path(path);size=path.stat().st_size;sections=[];failures=[]
    with path.open('rb') as stream:
        def take(n):
            if n<0 or n>size-stream.tell():raise ValueError('Packed block extends outside file')
            raw=stream.read(n)
            if len(raw)!=n:raise ValueError('Truncated packed block')
            return raw
        for section in range(2):
            start=stream.tell()
            if struct.unpack('<Q',take(8))[0]!=MAGIC:raise ValueError('GRB packed section magic mismatch')
            version,algorithm,flags=struct.unpack('<hBI',take(7));block_size=flags&0x7fffffff
            if not flags>>31:raise ValueError('Default-mode section requires a separate audit')
            count=struct.unpack('<I',take(4))[0]
            if version not in (0,1,2,3) or count>100000 or not 0<block_size<=16*1024*1024:raise ValueError('Unsupported packed header/count')
            fmt='<HH' if version==1 else '<II';width=struct.calcsize(fmt)
            table=[struct.unpack(fmt,take(width)) for _ in range(count)]
            uncompressed=compressed=0
            for index,(raw_size,packed_size) in enumerate(table):
                if raw_size>block_size or raw_size==0 or packed_size==0:raise ValueError('Invalid packed block size')
                checksum=struct.unpack('<I',take(4))[0];data=take(packed_size)
                # Anvil's lzo_adler32 starts at zero, unlike zlib's default one.
                calculated=zlib.adler32(data,0)&0xffffffff
                if checksum!=calculated:failures.append({'section':section,'block':index,'stored':f'{checksum:08X}','calculated':f'{calculated:08X}'})
                uncompressed+=raw_size;compressed+=packed_size
            sections.append({'offset':start,'version':version,'algorithm':algorithm,'blockSize':block_size,'blocks':count,'uncompressedBytes':uncompressed,'storedBytes':compressed})
        if stream.tell()!=size:raise ValueError('Unexpected bytes after two packed sections')
    return {'path':str(path.resolve()),'verified':not failures,'readOnly':True,'sections':sections,'checksumFailures':failures,'gameCompatibilityTested':False}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('data');p.add_argument('--report',required=True);a=p.parse_args()
    result=audit(a.data);Path(a.report).write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2));raise SystemExit(0 if result['verified'] else 2)
