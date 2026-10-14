"""Field-level weapon merges using the supplied author's compatibility reader.

Only the inspected, fingerprinted reader runs, with memory-only scan callbacks.
No converter UI, writer, imports, network code, or initialization is executed.
"""
import dis,hashlib,marshal,struct,types,zlib,zipfile
from pathlib import Path
import math

FORMATS={'u8':'<B','u16':'<H','u32':'<I','u64':'<Q','float':'<f'}
FIELDS={'rateoffire','projectilebreakingangle','zero','range','damage','penetration1','penetration2','velocity2',
    'ui_range','ui_rateoffire','ui_weight','ui_noise','timetoaim','burdenfactor','sway','recoilpattern','soundfile',
    'tacticalreloadthreshold','walkspeedmultiplier','mobility','adssensitivity','soundrange','ammotype','spreadpattern','reloadtime'}


class AuthorReader:
    def __init__(self,archive):
        if hashlib.sha256(Path(archive).read_bytes()).hexdigest()!='3c2e0f6375407609101850e35dc2997bc06d513dcc434c3cf69b40fc2c2c1f7a':raise ValueError('Unrecognized compatibility archive')
        with zipfile.ZipFile(archive) as z:
            name=next(n for n in z.namelist() if n.endswith('WABO Compatibility Tool 1.1.2.exe'))
            blob=z.read(name)
        # The author-published 1.1.2 payload shown on Nexus's file page.
        self.sha=hashlib.sha256(blob).hexdigest()
        if self.sha!='a33f1a4433c43ccbe2ac0e9cfb49730c02ef87c058b23a1425c1f28851ae6794':raise ValueError('Unrecognized compatibility tool binary; inspect before enabling its reader')
        cookie=blob.rfind(b'MEI\014\013\012\013\016')
        if cookie<0:raise ValueError('Compatibility tool archive missing')
        _,length,toc,size,version,_=struct.unpack('!8sIIII64s',blob[cookie:cookie+88])
        if version!=314:raise ValueError('Expected author Python 3.14 reader')
        start=cookie+88-length;cursor=start+toc;end=cursor+size;pyz=None
        while cursor<end:
            n,pos,packed,unpacked,compressed,typ=struct.unpack('!IIIIBc',blob[cursor:cursor+18]);label=blob[cursor+18:cursor+n].split(b'\0',1)[0].decode();cursor+=n
            if label=='PYZ.pyz':pyz=blob[start+pos:start+pos+packed];pyz=zlib.decompress(pyz) if compressed else pyz
        if pyz is None:raise ValueError('Reader modules missing')
        modules=dict(marshal.loads(pyz[struct.unpack('!I',pyz[8:12])[0]:]))
        def code(name):
            _,pos,size=modules[name];return marshal.loads(zlib.decompress(pyz[pos:pos+size]))
        table=code('db')
        if any(i.opname.startswith(('CALL','IMPORT')) or i.opname in ('LOAD_ATTR','STORE_ATTR','LOAD_GLOBAL') for i in dis.get_instructions(table)):
            raise ValueError('Author table is not literal data')
        values={'__builtins__':{}};exec(table,values)
        self.db=types.SimpleNamespace(**{k:v for k,v in values.items() if k!='__builtins__'})
        self.reader=next(c for c in code('scripts.WeaponDB').co_consts if isinstance(c,types.CodeType) and c.co_name=='weapondbread')
        allowed={'filedisplay','func','offset','statset','typesearch','fileu64','filename','dicttypesearch','db','ammotype'}
        if not set(self.reader.co_names)<=allowed:raise ValueError('Author reader requires uninspected operations')

    def read(self,path,required_fields=None):
        path=Path(path);data=path.read_bytes()
        if len(data)<100 or path.suffix.lower()!='.gr_weapondbentry':raise ValueError('Unsupported weapon definition')
        ctx=types.SimpleNamespace(offset=14,fileu64=int.from_bytes(data[1:9],'little'),filename=path.name)
        fields={};anchors=[]
        required=FIELDS if required_fields is None else set(required_fields)
        if not required or not required<=FIELDS:raise ValueError('Unsupported requested weapon fields')
        class Complete(Exception):pass
        def read(offset,typ):
            fmt=FORMATS[typ];size=struct.calcsize(fmt)
            if offset<13 or offset+size>len(data):raise ValueError('Weapon stat outside payload')
            value=struct.unpack_from(fmt,data,offset)[0]
            if isinstance(value,float) and not math.isfinite(value):raise ValueError('Nonfinite weapon stat')
            return value
        def statset(delta,name,typ):
            if name not in FIELDS:raise ValueError('Unknown author stat '+name)
            ctx.offset+=delta
            fields[name]={'offset':ctx.offset,'type':typ,'value':read(ctx.offset,typ)}
            if required_fields is not None and required<=fields.keys():raise Complete()
        def typesearch(name,targets,typ,times):
            targets=targets if isinstance(targets,tuple) else (targets,)
            upper=min(ctx.offset+1000,len(data)-struct.calcsize(FORMATS[typ])+1)
            matches=[i for i in range(ctx.offset,upper) if read(i,typ) in targets]
            if len(matches)<times:raise ValueError('Author reader anchor missing: '+name)
            ctx.offset=matches[times-1];anchors.append({'name':name,'offset':ctx.offset,'type':typ})
        def dicttypesearch(name,typ,table,subdict):
            targets=[v[subdict] for v in table.values() if isinstance(v,dict) and subdict in v]
            typesearch(name,tuple(targets),typ,1)
        globals_={'__builtins__':{},'filedisplay':lambda *a:None,'func':ctx,'db':self.db,'statset':statset,'typesearch':typesearch,'dicttypesearch':dicttypesearch}
        try:types.FunctionType(self.reader,globals_)()
        except Complete:pass
        if not required<=fields.keys():raise ValueError('Incomplete author stat coverage')
        if any(name in fields and not 1<=fields[name]['value']<=10000 for name in ('damage','rateoffire')):raise ValueError('Implausible damage/rate of fire')
        return {'id':ctx.fileu64,'fields':fields,'anchors':anchors,'sha256':hashlib.sha256(data).hexdigest()}


def merge(reader,base,donors,output):
    """donors: [(path, selected_field_names)]. Preserve every other base byte."""
    before=Path(base).read_bytes();result=bytearray(before);layout=reader.read(base);changes=[];ranges=set()
    for donor,names in donors:
        other=reader.read(donor)
        if other['id']!=layout['id']:raise ValueError('Weapon donor identity differs')
        for name in names:
            if name not in FIELDS:raise ValueError('Unsupported merged stat')
            dst=layout['fields'][name];src=other['fields'][name]
            if dst['type']!=src['type']:raise ValueError('Stat types differ')
            offset=dst['offset'];raw=struct.pack(FORMATS[dst['type']],src['value'])
            result[offset:offset+len(raw)]=raw;ranges.update(range(offset,offset+len(raw)))
            changes.append({'field':name,'offset':offset,'old':dst['value'],'new':src['value'],'donor':str(donor)})
    if any(before[i]!=result[i] and i not in ranges for i in range(len(before))):raise ValueError('Unrelated weapon byte changed')
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);output.write_bytes(result)
    after=reader.read(output)
    expected={name:entry['value'] for name,entry in layout['fields'].items()}
    for change in changes:expected[change['field']]=change['new']
    if any(after['fields'][name]['value']!=value for name,value in expected.items()):
        output.unlink();raise ValueError('Merged stat readback differs')
    return {'verified':True,'allOtherBytesPreserved':True,'originalSha256':layout['sha256'],'outputSha256':after['sha256'],'changes':changes,'readerSha256':reader.sha}


class DamageReader:
    """Read only the damage prefix; never interpret custom ammo/UI layouts."""
    def __init__(self,reader):self.reader=reader;self.sha=reader.sha
    def read(self,path):return self.reader.read(path,required_fields={'damage'})


def merge_damage(reader,base,donor,output):
    # Stop the fingerprinted author reader after damage. Later custom ammo/UI
    # references are irrelevant to a damage-only edit and remain untouched.
    return merge(DamageReader(reader),base,[(donor,{'damage'})],output)


def edit_damage(reader,base,value,output):
    return edit_fields(DamageReader(reader),base,{'damage':value},output)


def edit_fields(reader, base, values, output):
    """Apply typed stat values; reject changes outside independently read fields."""
    before=Path(base).read_bytes();layout=reader.read(base);result=bytearray(before)
    changes=[];ranges=set()
    for name,value in values.items():
        if name not in FIELDS:raise ValueError('Unsupported edited stat: '+name)
        field=layout['fields'][name]
        if not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError('Invalid stat value')
        if name=='sway' and not 0<=value<=10:raise ValueError('Invalid sway')
        if name=='damage' and not 1<=value<=10000:raise ValueError('Invalid damage')
        if name=='spreadpattern' and value not in reader.db.spreadpattern.values():raise ValueError('Unknown spread reference')
        offset=field['offset'];raw=struct.pack(FORMATS[field['type']],value)
        result[offset:offset+len(raw)]=raw;ranges.update(range(offset,offset+len(raw)))
        changes.append({'field':name,'offset':offset,'old':field['value'],'new':struct.unpack(FORMATS[field['type']],raw)[0]})
    if any(before[i]!=result[i] and i not in ranges for i in range(len(before))):raise ValueError('Unrelated weapon byte changed')
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);output.write_bytes(result)
    try:
        after=reader.read(output)
        expected={name:field['value'] for name,field in layout['fields'].items()}
        expected.update({c['field']:c['new'] for c in changes})
        if after['id']!=layout['id'] or any(after['fields'][name]['value']!=value for name,value in expected.items()):raise ValueError('Edited weapon readback differs')
    except Exception:
        output.unlink(missing_ok=True);raise
    return {'verified':True,'allOtherBytesPreserved':True,'originalSha256':layout['sha256'],'outputSha256':after['sha256'],'changes':changes,'readerSha256':reader.sha}
