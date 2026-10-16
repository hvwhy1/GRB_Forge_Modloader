"""Explicit one-object, private-copy new-resource insertion trial.

The resulting modified Forge is NOT intended for game deployment.
Only candidates with an existing verified parent .data are offered.
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
from toolkit_config import require_toolkit
from new_resource_audit import run as audit_run

ROOT=Path(__file__).resolve().parent
GAME=Path(r'C:\Program Files (x86)\Ubisoft\Ubisoft Game Launcher\games\Ghost Recon Breakpoint')
MODS=Path(r'C:\Users\Hurt\Desktop\GRB_MODDING\DEEF_Mods')

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--audit',help='Existing NEW_RESOURCE_AUDIT.json')
    p.add_argument('--index',type=int,help='One candidate index from the audit (skips menu)')
    p.add_argument('--mods-dir',help='Archive folder override')
    p.add_argument('--game-root',help='Game root, must match audit')
    args=p.parse_args()
    if not args.audit:
        report=audit_run()
    else:
        report=json.loads(Path(args.audit).read_text(encoding='utf-8'))
    game=Path(args.game_root or report.get('game') or GAME).resolve()
    mods=Path(args.mods_dir or mm.load(mm.CONFIG,mm.DEFAULT_SETTINGS).get('archive_dir') or MODS).resolve()
    if not (game/'GRB.exe').is_file() or not mods.is_dir():
        raise FileNotFoundError('Game root or mod archive folder missing')
    if Path(report.get('game','')).resolve()!=game:
        raise ValueError('Selected audit belongs to another game root')
    approved={x['index'] for x in safe_additions.build(report,game,mods)['privateTrialQueue']
              if x['kind']=='INNER_PRIVATE_TRIAL'}
    choices=[]
    for row in report['candidates']:
        if row['index'] not in approved or row['classification']!='NEW_INNER_OBJECT_PARENT_FOUND':continue
        if row['kind']!='inner' or not row['newObject'] or not row['sourceArchive']:continue
        forge=Path(row['parentForge'])
        archive=mods/Path(row['sourceArchive']).name
        if forge.is_file() and forge.parent.resolve()==game and archive.is_file():
            choices.append((row,forge,archive))
    choices.sort(key=lambda r:(r[1].stat().st_size,r[0]['index']))
    if not choices:
        print('No existing-parent new-object candidates are ready for a private test.')
        print('Upload workspace/new_resource_audit/NEW_RESOURCE_AUDIT.json to diagnose the outer Forge additions.')
        return 2
    print('\nCandidate new INNER object additions (private copy only):')
    for row,forge,archive in choices[:25]:
        print(f"[{row['index']}] {forge.name} / {row['parentData']} / {row['newObject']}  ({archive.name})")
    index=args.index
    if index is None:
        index=int(input('Select one candidate index to test (Enter is not accepted): ').strip())
    found=[x for x in choices if x[0]['index']==index]
    if len(found)!=1:raise ValueError('Index is not an eligible new INNER resource candidate')
    row,forge,archive=found[0]
    kit=require_toolkit()
    payload=mm.extraction_target(archive,row['member'])
    if not payload.is_file():raise FileNotFoundError('Mod payload extraction failed')
    out=ROOT/'workspace'/'new_add_trials'/time.strftime('%Y%m%d_%H%M%S')
    out.mkdir(parents=True,exist_ok=False)
    free=shutil.disk_usage(out).free
    required=forge.stat().st_size*2+2*1024**3
    if free<required:
        raise OSError(f'Private Forge needs at least {required/1024**3:.2f} GiB free, have {free/1024**3:.2f} GiB')
    reportfile=out/'NEW_INNER_TRIAL_RESULT.json'
    dst=out/forge.name
    dll=be.build_bridge(kit,lambda s:print(s,end='' if s.endswith('\n') else '\n'))
    cmd=['dotnet',str(dll),'stream-add-trial',str(kit),str(forge),row['parentData'],row['newObject'],str(payload),str(reportfile),str(dst)]
    console=out/'NEW_INNER_TRIAL_CONSOLE.txt'
    with console.open('w',encoding='utf-8') as sink:
        with subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1) as child:
            for line in child.stdout:
                sink.write(line);sink.flush();print(line,end='',flush=True)
            code=child.wait()
    print('Trial result:',reportfile,'\nConsole:',console)
    print('Original installed game was not modified. Do not copy the output Forge into the game.')
    return code

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        (ROOT/'PRIVATE_NEW_INSERTION_LAUNCH_ERROR.txt').write_text(traceback.format_exc(),encoding='utf-8')
        raise
