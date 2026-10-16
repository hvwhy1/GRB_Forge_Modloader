"""Generate runtime/configuration payloads from selected packages for a batch."""
from pathlib import Path
import json,hashlib,configparser
import batch_engine as be,config_install as ci,mod_manager as mm


def prepare(mods,game,run,notify=print):
    game=Path(game).resolve();folder=Path(run)/'Runtime';cfg=mm.load(mm.CONFIG,{})
    plan=[];contributions={}
    if cfg.get('native_package'):
        choices=dict(cfg.get('native_preset_choices') or ci.PRESETS)
        if cfg.get('preserve_game_sensitivity',False):choices['adsfov']='vanilla'
        plan.extend(ci.native_plan(cfg['native_package'],game,choices,cfg.get('native_chain_proxy',False),cfg.get('native_numeric'),toggle_key=cfg.get('native_toggle_key'),graphics_compatibility=cfg.get('native_graphics_compatibility',False),dx11_single_loader=cfg.get('native_dx11_single_loader',True)))
        main=next(r for r in plan if Path(r['target']).name=='NativeFirstPerson.ini')
        text,encoding=ci.read_ini(main['source']);text=main.get('content',text);values={}
        for row in plan:
            if Path(row['target']).parent.name!='NativeFirstPersonOptions':continue
            parser=configparser.ConfigParser(interpolation=None,strict=False);parser.optionxform=str
            parser.read_string(row.get('content') or ci.read_ini(row['source'])[0])
            if parser.has_section('FirstPerson'):values.update(dict(parser.items('FirstPerson')))
        if cfg.get('preserve_game_sensitivity',False):values['ADSFOV']='0'
        main.update(content=ci.patch_ini_values(text,'FirstPerson',values),encoding=encoding)
    if cfg.get('reshade_setup'):
        import reshade_install as rs
        shaders=cfg.get('reshade_shader_folder','')
        rows=rs.prepare(cfg['reshade_setup'],game,cfg.get('reshade_preset',''),shaders,fetch_effects=not bool(shaders),notify=notify,runtime_override=cfg.get('reshade_runtime_override',''))
        # The confirmed Native profile uses the runtime without ShaderToggler.
        # Its independent addon remains a separate explicit user choice.
        if not cfg.get('enable_shader_toggler_addon',False):rows=[r for r in rows if Path(r['target']).name not in ('ShaderToggler.addon64','ShaderToggler.ini')]
        plan.extend(rows)
    for key,mod in mods.items():
        if not mod.get('enabled',True) or '-1245-' not in key:continue
        for member in mod['members']:
            if Path(member).name!='soundspc_sfx_wea_.pck':continue
            source=mm.extraction_target(mod['archive'],member,archive_hash=be.sha256(mod['archive']))
            target=game/'sounddata/pc'/Path(member).name
            plan.append(dict(source=str(source),target=str(target)));contributions[str(target)]={'mod':key,'member':member}
    # Disabled shader addons must not survive an earlier/manual installation.
    if not cfg.get('enable_shader_toggler_addon',False):
        planned={Path(row['target']).resolve() for row in plan}
        for name in ('ShaderToggler.addon64','ShaderToggler.ini'):
            target=game/name
            if target.is_file() and target not in planned:
                plan.append(dict(target=str(target),remove=True,expectedSha256=be.sha256(target)))
    # Keep ReShade's overlay key separate from the user's FPV toggle.
    if str(cfg.get('native_toggle_key','')).casefold()=='home':
        for row in plan:
            if Path(row['target']).name=='ReShade.ini' and not row.get('remove'):
                if 'content' in row:text,encoding=row['content'],row.get('encoding','utf-8')
                else:text,encoding=ci.read_ini(row['source'])
                row.update(content=ci.patch_ini_values(text,'INPUT',{'KeyOverlay':'36,0,1,0'}),encoding=encoding)
    import runtime_history
    cleanup=runtime_history.cleanup(plan,game,cfg)
    plan.extend(cleanup)
    if cleanup:notify('Removed-mod runtime cleanup: '+str(len(cleanup))+' recorded files')
    rows=[];seen=set()
    for item in plan:
        target=Path(item['target']).resolve()
        if not runtime_history.safe_target(game,target) or target in seen:raise ValueError('Invalid or duplicate runtime destination')
        seen.add(target);row=dict(target=str(target),relative=target.relative_to(game).as_posix())
        if 'expectedSha256' in item:row['expectedSha256']=item['expectedSha256']
        if item.get('cleanupHistory'):row['cleanupHistory']=item['cleanupHistory']
        if item.get('remove'):row.update(remove=True,expectedSha256=item['expectedSha256'])
        else:
            dest=folder/'Payloads'/target.relative_to(game);dest.parent.mkdir(parents=True,exist_ok=True)
            if 'content' in item:dest.write_bytes(item['content'].encode(item.get('encoding','utf-8')))
            else:be._copy_verify(item['source'],dest,be.sha256(item['source']))
            row.update(source=str(dest.resolve()),sha256=be.sha256(dest),allowProxyReplace=item.get('allowProxyReplace',False))
        if str(target) in contributions:row['contribution']=contributions[str(target)]
        rows.append(row)
    state={'schema':'GRB_RUNTIME_STAGE_V20','gameRoot':str(game),'files':rows}
    folder.mkdir(parents=True,exist_ok=True);receipt=folder/'RUNTIME_STAGE.json';be.record_json(receipt,state)
    notify('Generated runtime stage from packages: '+str(len(rows))+' files/settings')
    return str(receipt),[r['contribution'] for r in rows if r.get('contribution')]


def inspect(receipt,game,batch):
    import runtime_history
    receipt=Path(receipt).resolve();game=Path(game).resolve();batch=Path(batch).resolve()
    if not be.within(batch,receipt):raise ValueError('Runtime receipt outside batch')
    state=json.loads(receipt.read_text());seen=set()
    if state.get('schema')!='GRB_RUNTIME_STAGE_V20' or Path(state['gameRoot']).resolve()!=game:raise ValueError('Wrong runtime stage')
    for row in state['files']:
        target=Path(row['target']).resolve()
        if not runtime_history.safe_target(game,target) or target in seen or target.relative_to(game).as_posix()!=row['relative']:raise ValueError('Invalid runtime target')
        seen.add(target)
        if not row.get('remove'):
            source=Path(row['source']).resolve()
            if not be.within(receipt.parent,source) or be.sha256(source)!=row['sha256']:raise ValueError('Runtime staged source changed')
    return state


def install(receipt,game,batch,backups,game_check=be.game_processes_running):
    state=inspect(receipt,game,batch)
    return ci.install_files(state['files'],game,backups,game_check=game_check)
