"""ReShade DX11 staging from the official setup payload, with preset dependencies."""
import configparser
import io
from pathlib import Path,PurePosixPath
import re
import urllib.request
import zipfile
import uuid
import subprocess
import struct
import config_install as ci
import mod_manager as mm
import batch_engine as be

MANIFEST='https://raw.githubusercontent.com/crosire/reshade-shaders/list/EffectPackages.ini'


def download(url):
    if not url.startswith(('https://github.com/','https://raw.githubusercontent.com/','https://codeload.github.com/')):raise ValueError('Unexpected shader download host')
    if url.startswith('https://github.com/') and '/archive/' in url:
        repo,ref=url[len('https://github.com/'):].split('/archive/',1)
        url='https://codeload.github.com/'+repo+'/zip/'+ref.removesuffix('.zip')
    request=urllib.request.Request(url,headers={'User-Agent':'DEEF-Mod-Studio'})
    with urllib.request.urlopen(request,timeout=60) as response:
        data=response.read(256*1024**2+1)
    if len(data)>256*1024**2:raise ValueError('Shader download exceeds 256 MiB')
    return data


def resolve_preset(path):
    path=Path(path)
    if path.suffix.lower() in mm.ARCHIVE_EXT:
        choices=[m for m in mm.archive_inventory(path) if m.lower().endswith('.ini')]
        if len(choices)!=1:raise ValueError('Preset archive contains several INIs; extract and choose the intended preset')
        return mm.extraction_target(path,choices[0])
    if not path.is_file() or path.suffix.lower()!='.ini':raise ValueError('Choose a preset INI or its archive')
    return path


def active_effects(text):
    line=next((line.split('=',1)[1] for line in text.splitlines() if line.startswith('Techniques=')),'')
    return {t.split('@',1)[1].strip() for t in line.split(',') if '@' in t}


def extract_shaders(data,folder,install_path,texture_path):
    rows=[]
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for item in archive.infolist():
            parts=PurePosixPath(item.filename).parts
            if '..' in parts or PurePosixPath(item.filename).is_absolute():raise ValueError('Unsafe shader archive path')
            if item.is_dir():continue
            category=next((i for i,p in enumerate(parts) if p.casefold() in ('shaders','textures')),None)
            if category is None:continue
            rel=Path(*parts[category+1:])
            if not rel.parts or item.file_size>64*1024**2:continue
            base=install_path if parts[category].casefold()=='shaders' else texture_path
            destination=folder/Path(base.replace('\\','/').removeprefix('./'))/rel
            if not be.within(folder,destination):raise ValueError('Shader path escaped staging workspace')
            destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(archive.read(item))
            rows.append(destination)
    return rows


def addon_runtime(path):
    """Validate the supplied full-addon runtime's x64 PE and actual exports."""
    data=Path(path).read_bytes()
    def u32(pos):return struct.unpack_from('<I',data,pos)[0]
    try:
        pe=u32(60);optional=pe+24
        if data[:2]!=b'MZ' or data[pe:pe+4]!=b'PE\0\0' or struct.unpack_from('<H',data,pe+4)[0]!=0x8664 or struct.unpack_from('<H',data,optional)[0]!=0x20b:raise ValueError('Expected an x64 runtime')
        count,size=struct.unpack_from('<H',data,pe+6)[0],struct.unpack_from('<H',data,pe+20)[0]
        sections=[struct.unpack_from('<IIII',data,optional+size+40*i+8) for i in range(count)]
        def offset(rva):
            for virtual_size,virtual_address,raw_size,raw_pointer in sections:
                if virtual_address<=rva<virtual_address+max(virtual_size,raw_size):return raw_pointer+rva-virtual_address
            raise ValueError('Invalid export address')
        export=offset(u32(optional+112));names=u32(export+24)
        if names>10000:raise ValueError('Invalid export count')
        table=offset(u32(export+32));exports=set()
        for i in range(names):
            start=offset(u32(table+4*i));end=data.index(b'\0',start,start+512)
            exports.add(data[start:end].decode('ascii'))
        if not {'D3D11CreateDevice','D3D11CreateDeviceAndSwapChain','ReShadeRegisterAddon'}<=exports:raise ValueError('Runtime lacks DX11 chaining or full add-on exports')
    except (struct.error,IndexError,UnicodeError) as error:raise ValueError('Invalid full-addon ReShade DLL') from error
    return data


def supplied_package_cleanup(rows,game,families):
    """Retire only receipt-owned extras inside explicitly supplied packages."""
    if not families:return []
    from layered_pipeline import receipt_history
    cfg=mm.load(mm.CONFIG,{})
    seeds=[cfg.get('baseline_config_transaction'),cfg.get('last_config_transaction')]
    shader_root=Path(game).resolve()/'reshade-shaders/Shaders'
    planned={Path(r['target']).resolve() for r in rows};owned={}
    for receipt,doc in receipt_history(game,seeds):
        if receipt.name!='CONFIG_TRANSACTION.json' or doc.get('status')!='INSTALLED':continue
        for row in doc.get('files',[]):
            target=Path(row['target']).resolve()
            try:parts=target.relative_to(shader_root).parts
            except ValueError:continue
            if target not in planned and len(parts)>1 and parts[0].casefold() in families:
                owned.setdefault(target,set()).add(row.get('installedSha256'))
    result=[]
    for target,known in owned.items():
        if not target.is_file():continue
        live=be.sha256(target)
        if live not in known:raise ValueError('Managed shader changed outside the installer; package cleanup stopped: '+str(target))
        # Explicitly selecting a package replaces its effect set. Preserve any
        # earlier copies in recovery, rather than restoring incompatible extras
        # back into the active search path.
        result.append({'target':str(target),'remove':True,'expectedSha256':live})
    return result


def shader_compatibility(rows):
    # RECORDED and PPFX_Bloom both declare global texColor, with different
    # semantics. ReShade shares that name across effects. Give only RECORDED's
    # private render target a unique identifier; preserve techniques/uniforms.
    for row in rows:
        target=Path(row['target'])
        if target.name.casefold()!='recorded.fx' or 'source' not in row:continue
        text=ci.read_ini(row['source'])[0]
        if re.search(r'\btexture\s+texColor\s*\{',text) and re.search(r'\bRenderTarget\s*=\s*texColor\b',text):
            row['content']=re.sub(r'\btexColor\b','FA_RecordedSceneColor',text);row['encoding']='utf-8'


def prepare(setup,game,preset='',shader_folder='',fetch_effects=True,notify=lambda s:None,fetch=download,runtime_override=''):
    game=Path(game).resolve();setup=Path(setup).resolve();folder=mm.WORK/'reshade_stages'/uuid.uuid4().hex[:12];folder.mkdir(parents=True)
    if not setup.is_file():raise ValueError('Choose the official ReShade setup executable')
    with zipfile.ZipFile(setup) as archive:
        if not {'ReShade32.dll','ReShade64.dll'}<=set(archive.namelist()):raise ValueError('Setup has no official ReShade runtime payload')
        runtime=folder/'ReShade64.dll';runtime.write_bytes(archive.read('ReShade64.dll'))
    if runtime.read_bytes()[:2]!=b'MZ':raise ValueError('Invalid ReShade runtime DLL')
    if runtime_override:runtime.write_bytes(addon_runtime(runtime_override))
    native=game/'NativeFirstPerson.ini';target=game/'dxgi.dll';rows=[]
    if native.is_file() and (game/'d3d11.dll').is_file():
        cfg=mm.load(mm.CONFIG,{});package=Path(cfg.get('native_package',''))
        own=package/'options/renderer/dx11/d3d11.dll'
        if not own.is_file() or be.sha256(own)!=be.sha256(game/'d3d11.dll'):raise ValueError('Existing native graphics loader could not be verified')
        # Keep ReShade on DXGI, matching the user's working FPV setup.
    rows.append({'source':str(runtime),'target':str(target)})
    if runtime_override:
        # The supplied HUD fix requires its module and settings as well as the
        # full-addon runtime. Match the selected archive by exact DLL bytes.
        digest=be.sha256(runtime)
        for key,mod in mm.load(mm.STATE,{}).items():
            if not isinstance(mod,dict) or not mod.get('enabled',True) or not Path(mod['archive']).name.lower().startswith('grbno-hud-no-effectsfix'):continue
            archive=Path(mod['archive']);fingerprint=be.sha256(archive)
            supplied=mm.extraction_target(archive,'dxgi.dll',archive_hash=fingerprint)
            if be.sha256(supplied)!=digest:continue
            for name in ('ShaderToggler.addon64','ShaderToggler.ini'):
                rows.append({'source':str(mm.extraction_target(archive,name,archive_hash=fingerprint)),'target':str(game/name)})
    preset_file=resolve_preset(preset) if preset else None
    preset_text=ci.read_ini(preset_file)[0] if preset_file else ''
    needed=active_effects(preset_text);staged={};local_rows={};families=set()
    if shader_folder:
        local=Path(shader_folder).resolve()
        if not local.is_dir():raise ValueError('Shader / texture folder does not exist: '+str(local))
        for file in ci.walk([local]):
            parts=file.relative_to(local).parts
            if local.name.casefold() in ('shaders','textures'):parts=(local.name,)+parts
            index=next((i for i,p in enumerate(parts) if p.casefold() in ('shaders','textures')),None)
            if index is None:continue
            category='Shaders' if parts[index].casefold()=='shaders' else 'Textures'
            if category=='Shaders' and len(parts)>index+2:families.add(parts[index+1].casefold())
            rel=Path('reshade-shaders')/category/Path(*parts[index+1:]);local_rows[rel.as_posix().casefold()]={'source':str(file),'target':str(game/rel)}
    if fetch_effects:
        manifest=configparser.ConfigParser(interpolation=None);manifest.read_string(fetch(MANIFEST).decode('utf-8-sig'))
        selected=[section for section in manifest.sections() if manifest.get(section,'Required',fallback='0')=='1' or needed & set(manifest.get(section,'EffectFiles',fallback='').split(','))]
        for section in selected:
            notify('Downloading '+manifest.get(section,'PackageName')+' shader package')
            data=fetch(manifest.get(section,'DownloadUrl'))
            for file in extract_shaders(data,folder,manifest.get(section,'InstallPath'),manifest.get(section,'TextureInstallPath')):
                rel=file.relative_to(folder)
                if len(rel.parts)>3 and rel.parts[1].casefold()=='shaders' and rel.parts[2].casefold() in families:continue
                staged[rel.as_posix().casefold()]={'source':str(file),'target':str(game/rel)}
    # Download dependencies without overwriting an explicitly supplied local pack.
    staged.update(local_rows)
    available={Path(r['target']).name for r in staged.values()}
    if (game/'reshade-shaders').is_dir():available.update(p.name for p in (game/'reshade-shaders').rglob('*.fx'))
    missing=needed-available
    if missing:raise ValueError('Preset requires unavailable effects: '+', '.join(sorted(missing))+'; select its shader folder')
    rows.extend(staged.values())
    cleanup=supplied_package_cleanup(rows,game,families)
    removed={Path(r['target']).name for r in cleanup if r.get('remove')}
    supplied={Path(r['target']).name for r in rows}
    missing=needed & (removed-supplied)
    if missing:raise ValueError('Supplied shader package is missing active effects: '+', '.join(sorted(missing)))
    rows.extend(cleanup)
    shader_compatibility(rows)
    if preset_file:
        preset_target=game/'DEEF_ReShade_Preset.ini';rows.append({'source':str(preset_file),'target':str(preset_target)})
    else:preset_target=game/'DEEF_ReShade_Preset.ini';rows.append({'target':str(preset_target),'content':'Techniques=\nTechniqueSorting=\n'})
    config=game/'ReShade.ini';text=ci.read_ini(config)[0] if config.is_file() else ''
    text=ci.patch_ini_values(text,'GENERAL',{'EffectSearchPaths':'.\\reshade-shaders\\Shaders\\**','TextureSearchPaths':'.\\reshade-shaders\\Textures\\**','PresetPath':'.\\'+preset_target.name})
    rows.append({'target':str(config),'content':text,'encoding':'utf-8'})
    if target.name=='d3d11_chain.dll':rows.append({'target':str(game/'d3d11_chain.ini'),'content':text,'encoding':'utf-8'})
    be.record_json(folder/'PLAN.json',{'setup':str(setup),'runtimeOverride':str(runtime_override),'runtimeSha256':be.sha256(runtime),'target':str(target),'activeEffects':sorted(needed),'suppliedShaderPackages':sorted(families),'files':rows})
    return rows


def install(setup,game,backups,preset='',shader_folder='',fetch_effects=True,notify=lambda s:None,runtime_override=''):
    if be.game_processes_running():raise RuntimeError('Close Breakpoint before installing ReShade')
    return ci.install_files(prepare(setup,game,preset,shader_folder,fetch_effects,notify,runtime_override=runtime_override),game,backups)


def open_vulkan_setup(setup,game):
    if be.game_processes_running():raise RuntimeError('Close Breakpoint before running ReShade setup')
    # Vulkan setup registers a system/user Vulkan layer. Use the official UI.
    return subprocess.Popen([str(setup),str(Path(game)/'GRB_vulkan.exe'),'--api','vulkan']).pid
