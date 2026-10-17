"""Reinstall the exact locally tested build, with content-addressed Forge sources."""
from pathlib import Path
import json,os,time,uuid,struct
import batch_engine as be
import config_install as ci
import mod_manager as mm
import configparser

FORGES={'DataPC.forge','DataPC_Resources.forge','DataPC_Resources_patch_01.forge',
        'DataPC_extra_patch_01.forge','DataPC_patch_01.forge','DataPC_TGT_WorldMap_Bootstrap_Split_patch_01.forge'}
RUNTIME={'d3d11.dll','dxgi.dll','NativeFirstPersonLauncher.exe'}


def source_path(manifest,row):
    path=Path(row['source'])
    return (path if path.is_absolute() else Path(manifest).resolve().parent/path).resolve()


def bundle(manifest,folder,notify=print):
    """Pin the confirmed setup in one folder, independent of diagnostic caches."""
    manifest=Path(manifest).resolve();folder=Path(folder).resolve()
    if not be.within(mm.WORK,folder):raise ValueError('Saved setup must remain inside workspace')
    raw=json.loads(manifest.read_text());state=inspect(manifest,raw['gameRoot'],notify)
    if not state.get('gameplayVerified'):raise ValueError('Confirm gameplay before bundling this setup')
    total=sum(Path(row['source']).stat().st_size for row in state['files'])
    folder.mkdir(parents=True,exist_ok=True)
    be._space_check(folder,total+512*1024**2,'Complete setup payloads')
    for row in state['files']:
        destination=folder/'Payloads'/row['target']
        if row['kind']=='forge':notify('Saving complete setup Forge: '+row['target'])
        be._copy_verify(row['source'],destination,row['sha256'])
        row['source']=destination.relative_to(folder).as_posix()
    state.update(selfContainedPayloads=True,sourceManifest=str(manifest))
    target=folder/'PROVEN_BUILD.json';be.record_json(target,state);return target

def allowed(relative):
    relative=Path(relative)
    if relative.is_absolute() or '..' in relative.parts:return False
    if len(relative.parts)==1:return relative.name in FORGES|RUNTIME or relative.suffix.lower()=='.ini'
    from audio_packages import AUDIO_FILES
    return relative.parts[0] in {'NativeFirstPersonOptions','reshade-shaders'} or relative.as_posix() in {'sounddata/pc/'+name for name in AUDIO_FILES}

def capture(game,sources,folder,notify=print):
    game=Path(game).resolve();folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=True)
    files=[]
    for name,source in sources.items():
        if name not in FORGES:raise ValueError('Unexpected Forge source')
        source=Path(source).resolve();notify('Checking proven Forge source: '+name)
        digest=be.sha256(source)
        if digest!=be.sha256(game/name):raise ValueError('Source does not match live proven Forge: '+name)
        files.append({'target':name,'source':str(source),'sha256':digest,'kind':'forge'})
    if set(sources)!=FORGES:raise ValueError('Proven build requires all six modified Forges')
    loose=[game/n for n in RUNTIME]+list(game.glob('*.ini'))
    for directory in ('NativeFirstPersonOptions','reshade-shaders'):
        loose.extend(p for p in ci.walk([game/directory]) if p.is_file())
    from audio_packages import AUDIO_FILES
    for name in sorted(AUDIO_FILES):
        audio=game/'sounddata/pc'/name
        if audio.is_file():loose.append(audio)
    for source in sorted(set(loose)):
        if not source.is_file():continue
        relative=source.relative_to(game)
        if not allowed(relative):raise ValueError('Unexpected runtime target')
        target=folder/'Payloads'/relative;digest=be.sha256(source)
        be._copy_verify(source,target,digest)
        files.append({'target':relative.as_posix(),'source':str(target),'sha256':digest,'kind':'config'})
    if (game/'dinput8.dll').exists():raise ValueError('Proven DX11 single-loader profile must omit dinput8.dll')
    cfg=mm.load(mm.CONFIG,{})
    state={'schema':'PROVEN_GRB_BUILD_V20','gameRoot':str(game),'exeSha256':be.sha256(game/'GRB.exe'),
           'gameplayVerified':False,'files':files,'nativeSettings':{k:cfg.get(k) for k in ('native_preset_choices','native_numeric','native_toggle_key','native_graphics_compatibility','native_dx11_single_loader','fpv_dx11_only')},
           'removeKnown':{'dinput8.dll':['d30880647ee2a0c288509c126f90a41a317008100da0b7368fc72458b1fcd896'],
                          'd3d11_chain.dll':['b2945c29e7095491a901746b400e58db9b1592ab092bacf2a888ce37f02d08da']}}
    manifest=folder/'PROVEN_BUILD.json';be.record_json(manifest,state);return manifest

def inspect(manifest,game,notify=print,_ancestors=()):
    manifest=Path(manifest).resolve();game=Path(game).resolve();state=json.loads(manifest.read_text())
    if manifest in _ancestors or len(_ancestors)>3:raise ValueError('Build authorization cycle')
    if state.get('schema')!='PROVEN_GRB_BUILD_V20':raise ValueError('Unsupported build manifest')
    if not state.get('gameplayVerified') and state.get('shotgunAdjustmentAuthorized'):
        parent_path=Path(state.get('authorizedShotgunAdjustmentFrom','')).resolve()
        if not be.within(mm.WORK,parent_path) or not parent_path.is_file():raise ValueError('Shotgun parent outside workspace')
        parent=inspect(parent_path,game,notify,_ancestors+ (manifest,))
        before={r['target']:r for r in parent['files']};after={r['target']:r for r in state['files']}
        if before.keys()!=after.keys():raise ValueError('Shotgun adjustment changed build membership')
        if any(after[k]['sha256']!=before[k]['sha256'] for k in before if k!='DataPC_patch_01.forge'):raise ValueError('Shotgun adjustment changed unrelated file')
        proof_path=Path(state['shotgunProof']).resolve();fields_path=Path(state['shotgunFieldsProof']).resolve()
        if not be.within(mm.WORK,proof_path) or not be.within(mm.WORK,fields_path):raise ValueError('Shotgun proof outside workspace')
        proof=json.loads(proof_path.read_text());fields=json.loads(fields_path.read_text())
        if not all(proof.get(k) is True for k in ('verified','sourceUnchanged','independentAnvilIndexVerified','allUnselectedOriginalObjectsPreserved','completeNestedReadbackVerified')):raise ValueError('Incomplete shotgun composition proof')
        if proof.get('addedEntries')!=0 or proof.get('replacedEntries')!=13 or len(fields)!=13:raise ValueError('Unexpected shotgun replacement count')
        if proof['sourceSha256']!=before['DataPC_patch_01.forge']['sha256'] or proof['outputSha256']!=after['DataPC_patch_01.forge']['sha256']:raise ValueError('Shotgun proof does not match build')
        rows={r['name']:r for r in proof['rows'] if r.get('state')=='REPLACED'}
        from shotgun_override import player_shotgun
        extraction_path=Path(state['shotgunExtraction']).resolve()
        if not be.within(mm.WORK,extraction_path):raise ValueError('Shotgun extraction outside workspace')
        extraction=json.loads(extraction_path.read_text());originals={r['name']+'.GR_WeaponDBEntry':r for r in extraction['rows']}
        if extraction['sourceSha256']!=proof['sourceSha256'] or not extraction['sourceUnchanged']:raise ValueError('Shotgun original extraction differs')
        if set(rows)!={r['name'] for r in fields}:raise ValueError('Shotgun proof membership differs')
        for field in fields:
            name=field['name'];changes=field['changes']
            if not player_shotgun(name) or len(changes)!=1:raise ValueError('Unsupported shotgun edit')
            change=changes[0]
            if change['field']!='damage' or change['old']!=40 or change['new']!=200:raise ValueError('Unsupported shotgun damage value')
            original=Path(originals[name]['file']).resolve();payload=proof_path.parent/'Payloads'/name
            if not be.within(mm.WORK,original) or be.sha256(original)!=field['originalSha256'] or be.sha256(payload)!=field['outputSha256'] or rows[name]['sha256']!=field['outputSha256']:raise ValueError('Shotgun payload fingerprint differs')
            old,new=original.read_bytes(),payload.read_bytes();offset=change['offset']
            if len(old)!=len(new) or offset<13 or offset+2>len(old) or old[:offset]!=new[:offset] or old[offset+2:]!=new[offset+2:] or struct.unpack_from('<H',old,offset)[0]!=40 or struct.unpack_from('<H',new,offset)[0]!=200:raise ValueError('Shotgun edit changed unrelated bytes')
    elif not state.get('gameplayVerified'):
        parent_path=Path(state.get('authorizedIniAdjustmentFrom','')).resolve()
        if not state.get('iniAdjustmentAuthorized') or not be.within(mm.WORK,parent_path) or not parent_path.is_file():raise ValueError('Build lacks gameplay confirmation or an authorized INI adjustment')
        parent=json.loads(parent_path.read_text())
        if not parent.get('gameplayVerified') or parent.get('gameRoot')!=state.get('gameRoot') or parent.get('exeSha256')!=state.get('exeSha256'):raise ValueError('Adjustment parent is not the confirmed build')
        before={r['target']:r for r in parent['files']};after={r['target']:r for r in state['files']}
        if before.keys()!=after.keys():raise ValueError('Adjustment changed build membership')
        permitted={'NativeFirstPerson.ini','NativeFirstPersonOptions/Camera.ini'}
        fields={'CameraForwardOffset','CameraClipDistance','DisableLocomotionDOF','DisableLocomotionAutoFocus'}
        for target,row in after.items():
            original=before[target]
            if row['sha256']==original['sha256']:continue
            if target not in permitted:raise ValueError('Adjustment changed a non-camera/DOF file')
            if be.sha256(original['source'])!=original['sha256']:raise ValueError('Adjustment parent INI changed')
            def values(path):
                parser=configparser.ConfigParser(interpolation=None,strict=False);parser.optionxform=str;parser.read_string(ci.read_ini(path)[0])
                return {(section,key):value for section in parser.sections() for key,value in parser.items(section)}
            old,new=values(original['source']),values(row['source'])
            for key in old.keys()|new.keys():
                if old.get(key)!=new.get(key) and (key[0]!='FirstPerson' or key[1] not in fields):raise ValueError('Adjustment changed an unsupported INI field')
            numeric={dst:new[('FirstPerson',src)] for src,dst in [('CameraForwardOffset','forward_offset'),('CameraClipDistance','clip_distance')] if ('FirstPerson',src) in new}
            ci.validate_native_numbers(numeric)
            for key in ('DisableLocomotionDOF','DisableLocomotionAutoFocus'):
                if ('FirstPerson',key) in new and new[('FirstPerson',key)].lower() not in ('true','false'):raise ValueError('Invalid DOF switch')
    if Path(state['gameRoot']).resolve()!=game or be.sha256(game/'GRB.exe')!=state['exeSha256']:raise ValueError('Game installation or executable differs from the proven build')
    seen=set()
    for row in state['files']:
        relative=row['target'];source=source_path(manifest,row)
        if not allowed(relative) or relative in seen or not be.within(mm.WORK,source):raise ValueError('Invalid proven-build file mapping')
        seen.add(relative)
        if row['kind']=='forge':notify('Checking build source: '+relative)
        if be.sha256(source)!=row['sha256']:raise ValueError('Proven source changed: '+relative)
        row['source']=str(source)
    if {r['target'] for r in state['files'] if r['kind']=='forge'}!=FORGES:raise ValueError('Incomplete proven Forge set')
    for name,hashes in state['removeKnown'].items():
        if name not in ('dinput8.dll','d3d11_chain.dll'):raise ValueError('Unsupported loader removal')
        p=game/name
        if p.exists() and be.sha256(p) not in hashes:raise ValueError('Foreign loader must be resolved: '+name)
    for row in state['files']:
        target=game/row['target']
        if target.suffix.lower()=='.dll' and target.exists() and be.sha256(target)!=row['sha256']:
            raise ValueError('Existing graphics DLL differs from proven build: '+target.name)
    notify('Verified '+str(len(seen))+' saved build files')
    return state

def install(manifest,game,backups,notify=print,game_check=be.game_processes_running):
    game=Path(game).resolve();backups=Path(backups).resolve()
    if game_check():raise RuntimeError('Close Breakpoint before installing the proven build')
    if be.within(game,backups) or be.within(backups,game):raise ValueError('Backups must be outside game')
    state=inspect(manifest,game,notify);changed=[]
    for row in state['files']:
        if row['kind']!='forge':continue
        target=game/row['target'];old=be.sha256(target) if target.is_file() else None
        if old is None:raise ValueError('Game Forge is missing: '+target.name)
        if old!=row['sha256']:changed.append((row,target,old))
    folder=backups/('PROVEN_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]);folder.mkdir(parents=True)
    if changed:
        be._space_check(game,max(Path(r['source']).stat().st_size for r,_,_ in changed)+512*1024**2,'Proven build staging')
        be._space_check(backups,sum(p.stat().st_size for _,p,_ in changed)+512*1024**2,'Proven build backups')
    tx={'schema':1,'status':'PREPARING','gameRoot':str(game),'label':'Proven DX11 build','files':[],'installed':[],'manifest':str(manifest)}
    receipt=folder/'INSTALL_TRANSACTION.json'
    for row,target,old in changed:
        backup=folder/'Originals'/target.name;be._copy_verify(target,backup,old)
        tx['files'].append({'target':str(target),'backup':str(backup),'originalSha256':old,'installedSha256':row['sha256']})
    tx['status']='INSTALLING';be.record_json(receipt,tx)
    config_receipt=None
    try:
        for row,target,old in changed:
            pending=target.with_name(target.name+'.deef-proven-'+uuid.uuid4().hex[:6])
            try:
                notify('Installing proven Forge: '+target.name);be._copy_verify(row['source'],pending,row['sha256'])
                if game_check() or be.sha256(target)!=old:raise RuntimeError('Live state changed during installation')
                os.replace(pending,target);tx['installed'].append(str(target));be.record_json(receipt,tx)
            finally:pending.unlink(missing_ok=True)
        plan=[{'source':r['source'],'target':str(game/r['target'])} for r in state['files'] if r['kind']=='config']
        for name,hashes in state['removeKnown'].items():
            p=game/name
            if p.exists():plan.append({'target':str(p),'remove':True,'expectedSha256':be.sha256(p)})
        import backup_slots
        with backup_slots.deferred():config_receipt=ci.install_files(plan,game,backups,game_check=game_check)
        tx.update(status='INSTALLED',configurationTransaction=str(config_receipt),changedForgeCount=len(changed));be.record_json(receipt,tx)
        cfg=mm.load(mm.CONFIG,{});cfg.update(state['nativeSettings']);cfg.update(last_proven_manifest=str(manifest),last_proven_transaction=str(receipt),last_config_transaction=str(config_receipt))
        if changed:cfg['last_weapon_tuning_transaction']=str(receipt)
        mm.save(mm.CONFIG,cfg)
    except Exception:
        if config_receipt is not None:ci.restore_files(config_receipt,game_check=game_check)
        be.record_json(receipt,tx);be.restore_install(receipt,game_check=game_check);raise
    return backup_slots.after_proven(manifest,receipt,backups)
