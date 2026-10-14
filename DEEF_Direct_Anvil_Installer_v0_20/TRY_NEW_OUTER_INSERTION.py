"""Run a single, verified private-copy outer Forge-entry insertion on Windows.

This command never stages an installable Forge. It saves a separate experimental
copy for readback diagnostics; do not put that Forge into the game.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import time
import traceback
import batch_engine as be
import mod_manager as mm
import safe_additions
import route_convention_audit as rc
from toolkit_config import require_toolkit

ROOT=Path(__file__).resolve().parent
GAME=Path(r'C:\Program Files (x86)\Ubisoft\Ubisoft Game Launcher\games\Ghost Recon Breakpoint')
MODS=Path(r'C:\Users\Hurt\Desktop\GRB_MODDING\DEEF_Mods')
ELIGIBLE={'NEW_FORGE_DATA_ENTRY_CANDIDATE'}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audit',help='NEW_RESOURCE_AUDIT.json from v0.19.5')
    p.add_argument('--index',type=int,help='Choose candidate index; default chooses smallest active Forge')
    p.add_argument('--mods-dir',help='Archive folder, overriding the previously hard-coded path')
    p.add_argument('--game-root',help='Game root; must match the audit')
    args=p.parse_args(argv)
    audit=Path(args.audit) if args.audit else rc.locate_audit()
    if audit is None or not audit.is_file():
        raise FileNotFoundError('No insertion audit found; specify --audit path/to/NEW_RESOURCE_AUDIT.json')
    source=json.loads(audit.read_text(encoding='utf-8'))
    game=Path(args.game_root or source.get('game') or GAME).resolve()
    mods=Path(args.mods_dir or mm.load(mm.CONFIG,mm.DEFAULT_SETTINGS).get('archive_dir') or MODS).resolve()
    if not (game/'GRB.exe').is_file() or not mods.is_dir():
        raise FileNotFoundError('Check game root with GRB.exe and your mod archive folder')
    if Path(source.get('game','')).resolve()!=game:
        raise ValueError('Audit was created for a different game root; regenerate it')
    approved={x['index'] for x in safe_additions.build(source,game,mods)['privateTrialQueue']
              if x['kind']=='OUTER_PRIVATE_TRIAL'}
    options=[]
    for row in source.get('candidates',[]):
        if row['index'] not in approved or row['classification'] not in ELIGIBLE or row.get('kind')!='data':continue
        forge=Path(row['parentForge']); archive=mods/Path(row['sourceArchive']).name
        if not(forge.is_file() and forge.resolve().parent==game and archive.is_file()):continue
        if not str(row['member']).lower().endswith('.data'):continue
        options.append((row,forge,archive))
    options.sort(key=lambda x:(x[1].stat().st_size,x[0]['index']))
    if not options:raise RuntimeError('No original outer .data payloads found in active mod archive and installed game root')
    print('Private Forge table insertion candidates:')
    for row,forge,archive in options[:12]:
        print(f" [{row['index']}] {forge.name} ({forge.stat().st_size/2**20:.1f} MiB) <- {row['member']}")
    selected=[x for x in options if x[0]['index']==args.index] if args.index is not None else [options[0]]
    if len(selected)!=1:raise ValueError('Selected candidate is not a uniquely eligible whole .data file')
    row,forge,archive=selected[0];kit=require_toolkit()
    output=ROOT/'workspace'/'private_outer_trials'/time.strftime('%Y%m%d_%H%M%S')
    output.mkdir(parents=True,exist_ok=False)
    if be.within(game,output) or be.within(kit,output):raise ValueError('Unsafe private output location')
    required=forge.stat().st_size*2+1024**3
    free=shutil.disk_usage(output).free
    if free<required:raise OSError(f'Need {required/2**30:.2f} GiB free for private copy; have {free/2**30:.2f} GiB')
    payload=mm.extraction_target(archive,row['member'])
    request=output/'OUTER_ADD_REQUEST.json';report=output/'OUTER_ADD_RESULT.json';console=output/'OUTER_ADD_CONSOLE.txt'
    request.write_text(json.dumps([{'data':row['parentData'],'payload':str(payload)}],indent=2),encoding='utf-8')
    dll=be.build_bridge(kit, lambda msg:print(msg,flush=True))
    cmd=['dotnet',str(dll),'stream-add-outer-trial',str(kit),str(forge),str(report),str(request),str(output/forge.name)]
    print('Private trial running. Game archive remains read-only.')
    with console.open('w',encoding='utf-8') as sink:
        with subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,errors='replace',bufsize=1) as child:
            for line in child.stdout:
                sink.write(line);sink.flush();print(line,end='',flush=True)
            code=child.wait()
    print('Result:',report,'\nConsole:',console)
    print('Do NOT copy experimental output into the game. No live deployment approval.')
    return code

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        (ROOT/'OUTER_INSERTION_LAUNCH_ERROR.txt').write_text(traceback.format_exc(),encoding='utf-8')
        raise
