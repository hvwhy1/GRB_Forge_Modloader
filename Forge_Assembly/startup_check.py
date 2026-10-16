"""Observe the actual standard/Ubisoft+ game process, rather than its bootstrapper."""
import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time

import batch_engine as be

GAME_NAMES={'grb.exe','grb_vulkan.exe','grb_upp.exe','grb_upp_vulkan.exe','grb_be.exe'}


def processes():
    result=subprocess.run(['tasklist','/FO','CSV','/NH'],capture_output=True,text=True,timeout=15)
    if result.returncode:raise RuntimeError('Cannot observe the game process list')
    return [{'name':r[0],'pid':int(r[1])} for r in csv.reader(result.stdout.splitlines())
            if len(r)>1 and r[0].casefold() in GAME_NAMES]


def check(game,report,seconds=90,label='DIAGNOSTIC',notify=print,launcher='GRB.exe'):
    game=Path(game).resolve();report=Path(report).resolve()
    if seconds<10 or seconds>180:raise ValueError('Observe between 10 and 180 seconds')
    if processes():raise RuntimeError('A game is already running; close it before starting a diagnostic')
    # Find the launcher log relative to Ubisoft Game Launcher/games/<game>.
    log=game.parent.parent/'logs'/'launcher_log.txt'
    start_size=log.stat().st_size if log.is_file() else 0
    start=datetime.now(timezone.utc).isoformat();begin=time.monotonic()
    if launcher not in ('GRB.exe','GRB_UPP.exe'):raise ValueError('Unsupported diagnostic launcher')
    info=subprocess.STARTUPINFO();info.dwFlags|=subprocess.STARTF_USESHOWWINDOW;info.wShowWindow=1
    bootstrap=subprocess.Popen([str(game/launcher)],cwd=game,startupinfo=info)
    observed=[];seen=False;status='NO_GAME_PROCESS';last_notice=-10
    while time.monotonic()-begin<seconds:
        elapsed=time.monotonic()-begin;active=processes()
        # The bootstrapper may exit with code 0 while GRB_UPP is still starting.
        active=[p for p in active if p['pid']!=bootstrap.pid or elapsed>8]
        if active:
            seen=True;observed.append({'seconds':round(elapsed,1),'processes':active})
            status='RUNNING'
        elif seen:
            status='PROCESS_EXITED';break
        elif elapsed>30:
            break
        if elapsed-last_notice>=10:
            notify(f'{label}: {int(elapsed)}s, '+(', '.join(p['name'] for p in active) or 'waiting for actual game process')+'\n')
            last_notice=elapsed
        time.sleep(2)
    active=processes()
    if status=='RUNNING' and active:status='ALIVE_AT_END_OF_OBSERVATION'
    crash_marker=False
    if log.is_file():
        with log.open('rb') as f:
            f.seek(start_size if log.stat().st_size>=start_size else 0)
            delta=f.read().decode('utf-8',errors='replace')
        crash_marker=any(marker in delta for marker in ('CrashReporter.WindowName','CrashReporter.cpp'))
        if crash_marker and status=='PROCESS_EXITED':status='CRASH_REPORTED'
    result={'status':status,'label':label,'startedUtc':start,'observedSeconds':round(time.monotonic()-begin,1),
            'observations':observed,'activeAtEnd':active,'crashReporterMarker':crash_marker,
            'gameplayVerified':False,'note':'Process survival does not establish menu/save/gameplay correctness.'}
    be.record_json(report,result)
    notify(json.dumps({k:result[k] for k in ('status','label','observedSeconds','activeAtEnd','crashReporterMarker','gameplayVerified')},indent=2))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game',required=True);parser.add_argument('--report',required=True)
    parser.add_argument('--seconds',type=int,default=90);parser.add_argument('--label',default='DIAGNOSTIC')
    parser.add_argument('--launcher',choices=['GRB.exe','GRB_UPP.exe'],default='GRB.exe')
    a=parser.parse_args();r=check(a.game,a.report,a.seconds,a.label,notify=lambda s:print(s,flush=True),launcher=a.launcher)
    raise SystemExit(2 if r['status']=='CRASH_REPORTED' else 0)
