"""Config discovery and transactional deployment of selected INIs/native packages."""
import configparser
import os
from pathlib import Path
import time
import uuid
import shutil
import batch_engine as be

MAX_INI=2*1024**2
PACKAGE_LIMIT=64*1024**2
AUDIO_LIMIT=2*1024**3
SKIP={'workspace','extracted','backups','originals','.git','node_modules','libs','bin','obj','__pycache__'}
PRESETS={'renderer':'dx11','startup':'manual','fov':'120','adsfov':'vanilla','interactions':'vanilla','camera':'standard','bodyfollow':'disabled','input':'numpad0'}
NAMES={'renderer':'Renderer.ini','startup':'Startup.ini','fov':'FOV.ini','adsfov':'ADSFOV.ini','interactions':'Interactions.ini','camera':'Camera.ini','bodyfollow':'BodyFollow.ini','input':'Input.ini'}


def walk(roots,max_depth=7):
    seen=set();pending=list(reversed([(Path(r),0) for r in roots if str(r)]));count=0
    while pending and count<50000:
        folder,depth=pending.pop()
        try:
            key=str(folder.resolve()).casefold()
            if key in seen or folder.is_symlink() or folder.is_junction():continue
            seen.add(key)
            with os.scandir(folder) as entries:
                for item in entries:
                    count+=1
                    if item.is_symlink():continue
                    path=Path(item.path)
                    if item.is_file(follow_symlinks=False):yield path
                    elif depth<max_depth and item.is_dir(follow_symlinks=False) and item.name.casefold() not in SKIP:
                        pending.append((path,depth+1))
        except (OSError,ValueError):continue


def find_inis(roots):
    return sorted({p.resolve() for p in walk(roots) if p.suffix.casefold()=='.ini' and p.stat().st_size<=MAX_INI},key=lambda p:str(p).casefold())


def find_native_packages(roots):
    return sorted({p.parent.parent.resolve() for p in walk(roots) if p.name.casefold()=='nativefirstperson.ini' and p.parent.name.casefold()=='core' and (p.parent/'dinput8.dll').is_file()},key=str)


def read_ini(path):
    data=Path(path).read_bytes()
    if len(data)>MAX_INI:raise ValueError('INI exceeds 2 MiB')
    encoding='utf-16' if data.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig' if data.startswith(b'\xef\xbb\xbf') else 'utf-8'
    try:return data.decode(encoding),encoding
    except UnicodeDecodeError:return data.decode('cp1252'),'cp1252'


def ini_destination(source,game):
    source=Path(source);game=Path(game).resolve()
    if be.within(game,source):return source.resolve()
    if source.name in NAMES.values() and any(p.casefold() in ('options','nativefirstpersonoptions') for p in source.parts):
        return game/'NativeFirstPersonOptions'/source.name
    return game/source.name


def native_plan(package,game,choices=None,chain_proxy=False,numeric=None,toggle_key=None,graphics_compatibility=False,dx11_single_loader=False):
    package=Path(package).resolve();game=Path(game).resolve();choices=dict(PRESETS,**(choices or {}))
    numeric=dict(numeric or {})
    numeric.setdefault('fov',choices['fov'])
    numeric.setdefault('adsfov','0' if choices['adsfov']=='vanilla' else choices['adsfov'])
    numbers=validate_native_numbers(numeric)
    choices['fov']='120';choices['adsfov']='vanilla'
    key=validate_toggle_key(toggle_key if toggle_key is not None else choices['input'])
    choices['input']='numpad0'
    core=package/'core'
    if not all((core/name).is_file() for name in ('NativeFirstPerson.ini','dinput8.dll','NativeFirstPersonLauncher.exe')):raise ValueError('Native package is incomplete: core must include the master INI, dinput8.dll and launcher')
    plan=[]
    for file in sorted(core.rglob('*')):
        if file.is_file():plan.append({'source':str(file),'target':str(game/file.relative_to(core))})
    for role,variant in choices.items():
        if role not in NAMES or variant!=Path(variant).name or '..' in variant:raise ValueError('Invalid native preset choice')
        file=package/'options'/role/variant/NAMES[role]
        if not file.is_file():raise ValueError('Preset unavailable: '+str(file))
        plan.append({'source':str(file),'target':str(game/'NativeFirstPersonOptions'/file.name)})
    edits={'FOV.ini':{'FOV':numbers['fov']},'ADSFOV.ini':{'ADSFOV':numbers['adsfov']}}
    camera={}
    if 'forward_offset' in numbers:camera['CameraForwardOffset']=numbers['forward_offset']
    if 'clip_distance' in numbers:camera['CameraClipDistance']=numbers['clip_distance']
    if camera:edits['Camera.ini']=camera
    if key is not None:edits['Input.ini']={'ToggleKey':key}
    for row in plan:
        name=Path(row['target']).name
        if name in edits:
            text,encoding=read_ini(row['source']);row.update(content=patch_ini_values(text,'FirstPerson',edits[name]),encoding=encoding)
    renderer=choices['renderer'];dx=package/'options/renderer/dx11/d3d11.dll'
    if dx11_single_loader:
        if renderer!='dx11':raise ValueError('The proven single-loader FPV setup requires DX11')
        plan=[row for row in plan if Path(row['target']).name!='dinput8.dll']
        selector=game/'dinput8.dll'
        if selector.exists():
            expected=be.sha256(core/'dinput8.dll')
            if be.sha256(selector)!=expected:raise ValueError('Foreign dinput8.dll must be resolved before using the single-loader setup')
            plan.append({'target':str(selector),'remove':True,'expectedSha256':expected})
    if renderer=='dx11':
        if not dx.is_file():raise ValueError('DX11 loader missing from package')
        existing=game/'d3d11.dll'
        proxy=existing if existing.is_file() and be.sha256(existing)!=be.sha256(dx) else None
        existing_dxgi=game/'dxgi.dll'
        if existing_dxgi.is_file() and proxy:
            raise ValueError('Two different graphics proxies are present; resolve the foreign d3d11.dll before installing FPV.')
        # Preserve standalone DXGI exactly as the working setup does. Only a
        # wrapper occupying d3d11.dll requires the native ChainLibrary mechanism.
        if proxy:
            if not chain_proxy:raise ValueError('Existing d3d11.dll differs. Enable proxy chaining to preserve it, or choose another renderer.')
            plan.append({'source':str(proxy),'target':str(game/'d3d11_chain.dll'),'allowProxyReplace':True})
            main=next(r for r in plan if Path(r['target']).name=='NativeFirstPerson.ini')
            text,encoding=read_ini(main['source'])
            main['content']=patch_ini_values(text,'Compatibility',{'ChainLibrary':'d3d11_chain.dll'});main['encoding']=encoding
            if (game/'ReShade.ini').is_file():plan.append({'source':str(game/'ReShade.ini'),'target':str(game/'d3d11_chain.ini')})
        plan.append({'source':str(dx),'target':str(game/'d3d11.dll'),'allowProxyReplace':bool(chain_proxy)})
    elif renderer=='vulkan':
        existing=game/'d3d11.dll'
        if existing.is_file() and dx.is_file() and be.sha256(existing)==be.sha256(dx):
            plan.append({'target':str(existing),'remove':True,'expectedSha256':be.sha256(dx)})
    else:raise ValueError('Unknown renderer')
    if graphics_compatibility:
        main=next(r for r in plan if Path(r['target']).name=='NativeFirstPerson.ini')
        text,encoding=(main['content'],main.get('encoding','utf-8')) if 'content' in main else read_ini(main['source'])
        main.update(content=patch_ini_values(text,'FirstPerson',{'DisableLocomotionDOF':'false','DisableLocomotionAutoFocus':'false'}),encoding=encoding)
    return plan


def ini_plan(source,target,text,encoding='utf-8'):
    if Path(target).suffix.casefold()!='.ini':raise ValueError('INI destination must end in .ini')
    if len(text.encode(encoding))>MAX_INI:raise ValueError('Edited INI exceeds 2 MiB')
    rows=[{'source':str(source),'target':str(target),'content':text,'encoding':encoding}]
    # The runtime's preset files override the master INI. Update matching keys
    # when installing the master so its edits actually take effect.
    if Path(target).name.casefold()=='nativefirstperson.ini':
        cfg=configparser.ConfigParser(interpolation=None,strict=False);cfg.optionxform=str;cfg.read_string(text)
        fields={'Startup':('FirstPerson',['EnabledOnLaunch']),'FOV':('FirstPerson',['FOV']),
            'ADSFOV':('FirstPerson',['ADSFOV']),'Camera':('FirstPerson',['CameraForwardOffset','CameraClipDistance']),
            'Interactions':('FirstPerson',['SpecialInteractions']),'BodyFollow':('FirstPerson',['FullBodyFollow']),
            'Input':('FirstPerson',['ToggleKey'])}
        for name,(section,keys) in fields.items():
            values=[k+'='+cfg.get(section,k) for k in keys if cfg.has_option(section,k)]
            if values:rows.append({'target':str(Path(target).parent/'NativeFirstPersonOptions'/(name+'.ini')),'content':'['+section+']\n'+'\n'.join(values)+'\n','encoding':'utf-8'})
    return rows


def install_files(plan,game,backups,game_check=be.game_processes_running):
    game=Path(game).resolve();backups=Path(backups).resolve()
    if not (game/'GRB.exe').is_file():raise ValueError('Game folder must contain GRB.exe')
    if be.within(game,backups) or be.within(backups,game):raise ValueError('Backups must be outside the game')
    if game_check():raise RuntimeError('Close Breakpoint before installing configuration files')
    targets=set();prepared=[]
    for item in plan:
        target=Path(item['target']).resolve()
        if not be.within(game,target) or target==game or target.suffix.casefold() in ('.forge','.data'):raise ValueError('Invalid configuration destination')
        if str(target).casefold() in targets:raise ValueError('Duplicate configuration destination')
        targets.add(str(target).casefold())
        old=be.sha256(target) if target.is_file() else None
        stream_source=None
        if 'source' in item and 'content' not in item:
            source=Path(item['source']).resolve();size=source.stat().st_size
            if target.suffix.casefold()=='.pck':
                if target.parent!=game/'sounddata'/'pc' or source.name!=target.name or size>AUDIO_LIMIT:raise ValueError('Invalid audio package destination or size')
                with source.open('rb') as f:
                    if f.read(4)!=b'AKPK':raise ValueError('Audio package must have a Wwise AKPK header')
                stream_source=source
            elif size>PACKAGE_LIMIT:raise ValueError('Configuration/package file exceeds 64 MiB')
        data=None if item.get('remove') else stream_source if stream_source else item['content'].encode(item.get('encoding','utf-8')) if 'content' in item else Path(item['source']).read_bytes()
        import hashlib
        new=be.sha256(data) if isinstance(data,Path) else hashlib.sha256(data).hexdigest() if data is not None else None
        if item.get('remove') and old!=item.get('expectedSha256'):raise ValueError('Loader changed; refusing removal')
        if target.suffix.casefold()=='.dll' and old and old!=new and not item.get('remove') and not item.get('allowProxyReplace'):
            raise ValueError('Existing DLL conflict: '+target.name)
        prepared.append((target,old,new,data))
    folder=backups/('CONFIG_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]);folder.mkdir(parents=True)
    tx={'status':'PREPARING','gameRoot':str(game),'files':[]}
    receipt=folder/'CONFIG_TRANSACTION.json';swapped=[]
    try:
        for target,old,new,data in prepared:
            if old==new:continue
            backup=folder/'Originals'/target.relative_to(game)
            if old:be._copy_verify(target,backup,old)
            record={'target':str(target),'backup':str(backup) if old else None,'originalSha256':old,'installedSha256':new}
            tx['files'].append(record);be.record_json(receipt,tx)
        tx['status']='INSTALLING';be.record_json(receipt,tx)
        for record in tx['files']:
            if game_check():raise RuntimeError('Game opened during configuration installation')
            target=Path(record['target']);current=be.sha256(target) if target.is_file() else None
            if current!=record['originalSha256']:raise ValueError('Destination changed during installation')
            data=next(d for p,o,n,d in prepared if p==target)
            target.parent.mkdir(parents=True,exist_ok=True)
            if data is None:target.unlink()
            else:
                pending=target.with_name(target.name+'.deef-config-'+uuid.uuid4().hex[:6])
                try:
                    with pending.open('xb') as f:
                        if isinstance(data,Path):
                            with data.open('rb') as source:shutil.copyfileobj(source,f,8*1024**2)
                        else:f.write(data)
                        f.flush();os.fsync(f.fileno())
                    if be.sha256(pending)!=record['installedSha256']:raise IOError('Pending config hash mismatch')
                    os.replace(pending,target)
                finally:pending.unlink(missing_ok=True)
            swapped.append(record)
        tx['status']='INSTALLED';be.record_json(receipt,tx);return receipt
    except Exception as exc:
        errors=[]
        for record in reversed(swapped):
            try:
                target=Path(record['target'])
                if record['backup']:
                    tmp=target.with_name(target.name+'.deef-config-rollback-'+uuid.uuid4().hex[:6]);be._copy_verify(Path(record['backup']),tmp,record['originalSha256']);os.replace(tmp,target)
                else:target.unlink(missing_ok=True)
            except Exception as error:errors.append(str(error))
        tx.update(status='ROLLBACK_FAILED' if errors else 'ROLLED_BACK',error=str(exc),rollbackErrors=errors);be.record_json(receipt,tx);raise


def restore_files(receipt,game_check=be.game_processes_running):
    import json
    receipt=Path(receipt).resolve();tx=json.loads(receipt.read_text(encoding='utf-8'));game=Path(tx['gameRoot']).resolve()
    if tx.get('status')!='INSTALLED':raise ValueError('Config transaction is not installed')
    if game_check():raise RuntimeError('Close Breakpoint before restoring configurations')
    for row in tx['files']:
        target=Path(row['target']).resolve()
        if not be.within(game,target) or target.suffix.casefold() in ('.forge','.data'):raise ValueError('Invalid config recovery target')
        if (be.sha256(target) if target.is_file() else None)!=row['installedSha256']:raise ValueError('Installed configuration changed; refusing to overwrite edits')
        if row['backup']:
            backup=Path(row['backup']).resolve()
            if not be.within(receipt.parent,backup) or be.sha256(backup)!=row['originalSha256']:raise ValueError('Config backup missing or changed')
    for row in reversed(tx['files']):
        target=Path(row['target'])
        if row['backup']:
            tmp=target.with_name(target.name+'.deef-config-restore-'+uuid.uuid4().hex[:6])
            try:be._copy_verify(Path(row['backup']),tmp,row['originalSha256']);os.replace(tmp,target)
            finally:tmp.unlink(missing_ok=True)
        else:target.unlink(missing_ok=True)
    tx['status']='RESTORED';be.record_json(receipt,tx);return receipt


def validate_native_numbers(values):
    import math
    limits={'fov':(70,140),'adsfov':(0,140),'forward_offset':(0,0.45),'clip_distance':(0.001,0.1)}
    result={}
    for key,value in values.items():
        if key not in limits:raise ValueError('Unsupported numerical setting: '+key)
        try:number=float(value)
        except (ValueError,TypeError):raise ValueError(key+' must be a number')
        lower,upper=limits[key]
        if not math.isfinite(number) or not lower<=number<=upper or (key=='adsfov' and 0<number<20):raise ValueError(key+' is outside its supported range')
        result[key]=format(number,'.12g')
    return result


def validate_toggle_key(value):
    import re
    value=str(value).strip();lower=value.casefold()
    named=['Numpad0','Numpad1','Numpad2','Numpad3','Numpad4','Numpad5','Numpad6','Numpad7','Numpad8','Numpad9',
           'Insert','Delete','Home','End','PageUp','PageDown','Space','Pause','ScrollLock','NumpadPlus','NumpadMinus','NumpadDecimal']
    match=next((n for n in named if n.casefold()==lower),None)
    if match:return match
    if re.fullmatch(r'[A-Za-z0-9]',value):return value.upper()
    if re.fullmatch(r'F(?:[1-9]|1[0-9]|2[0-4])',value,re.I):return value.upper()
    if re.fullmatch(r'0x[0-9a-f]{1,2}',value,re.I) and 1<=int(value,16)<=255:return f'0x{int(value,16):02X}'
    raise ValueError('Toggle key must be a supported key, such as Numpad0, F9, Home, K, or 0x60')


def native_camera_plan(game,values,toggle_key):
    game=Path(game).resolve();master=game/'NativeFirstPerson.ini'
    if not master.is_file():raise ValueError('Install Native FPV before applying camera settings')
    numbers=validate_native_numbers(values)
    if set(numbers)-{'forward_offset','clip_distance'}:raise ValueError('Camera settings contain unsupported fields')
    camera={runtime:numbers[field] for field,runtime in [('forward_offset','CameraForwardOffset'),('clip_distance','CameraClipDistance')] if field in numbers}
    key=validate_toggle_key(toggle_key);text,encoding=read_ini(master)
    rows=[{'target':str(master),'content':patch_ini_values(text,'FirstPerson',dict(camera,ToggleKey=key)),'encoding':encoding}]
    for name,fields in [('Camera.ini',camera),('Input.ini',{'ToggleKey':key})]:
        target=game/'NativeFirstPersonOptions'/name
        text,encoding=read_ini(target) if target.is_file() else ('[FirstPerson]\n','utf-8')
        rows.append({'target':str(target),'content':patch_ini_values(text,'FirstPerson',fields),'encoding':encoding})
    return rows


def installed_native_controls(game):
    game=Path(game);result={}
    for path in [game/'NativeFirstPerson.ini',game/'NativeFirstPersonOptions/Camera.ini',game/'NativeFirstPersonOptions/Input.ini']:
        if not path.is_file():continue
        parser=configparser.ConfigParser(interpolation=None,strict=False);parser.read_string(read_ini(path)[0])
        for field,key in [('forward_offset','CameraForwardOffset'),('clip_distance','CameraClipDistance'),('toggle_key','ToggleKey')]:
            if parser.has_option('FirstPerson',key):result[field]=parser.get('FirstPerson',key)
    if not result:raise ValueError('No installed Native FPV settings found')
    return result

def patch_ini_values(text,section,values):
    lines=text.splitlines();inside=False;seen=set();output=[];found=False
    for line in lines:
        if line.strip().startswith('['):
            if inside:
                output.extend(k+'='+v for k,v in values.items() if k not in seen)
            inside=line.strip().casefold()==('['+section+']').casefold();found=found or inside
        key=line.split('=',1)[0].strip()
        if inside and '=' in line and key in values:
            output.append(key+'='+values[key]);seen.add(key)
        else:output.append(line)
    if inside:output.extend(k+'='+v for k,v in values.items() if k not in seen)
    if not found:output.extend(['','['+section+']']+[k+'='+v for k,v in values.items()])
    return '\n'.join(output)+'\n'
