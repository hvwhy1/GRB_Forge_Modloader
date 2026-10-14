"""DEEF v0.17.3 large-Forge indexing automatic setup. Index -> characterize -> stage -> explicit opt-in install.

No manual path characterization or route manifests required. Unresolvable routes
remain excluded; GUI makes incomplete installations explicit. Advanced v0.16 GUI
is available for manual review and first-person INI editing.
"""
import argparse
import collections
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import traceback

import auto_mapping
import batch_engine as be
import existing_stage
import safe_additions
import auto_additions
import new_resource_audit
import mod_manager as mm
from toolkit_config import DEFAULT_TOOLKIT

ROOT=Path(__file__).resolve().parent
DEFAULT_GAME=r'C:\Program Files (x86)\Ubisoft\Ubisoft Game Launcher\games\Ghost Recon Breakpoint'
DEFAULT_MODS=r'C:\Users\Hurt\Desktop\GRB_MODDING\DEEF_Mods'


def choose_default_flags(mods):
    desired=json.loads(auto_mapping.LIST_FILE.read_text(encoding='utf-8'))['mods']
    by_id=collections.defaultdict(list)
    for item in desired:
        by_id[str(item['nexus_id'])].append(item)
    for key,mod in mods.items():
        if 'enabled' in mod and mod.get('_enabled_by_user'):
            continue
        name=Path(key).name
        match=re.search(r'-(\d{2,5})-(?:\d|v|V)',name)
        if not match:continue
        entries=by_id.get(match.group(1),[])
        if len(entries)==1:
            entry=entries[0]
            if entry.get('enabled') is False:
                mod['enabled']=False
    return mods


def create_paths(archive_dir, game, kit, backup):
    ar=Path(archive_dir).resolve();gm=Path(game).resolve();tk=Path(kit).resolve();bk=Path(backup).resolve()
    if not ar.is_dir():raise ValueError('Select the folder containing your downloaded ZIP/7z/RAR mods.')
    if not (gm/'GRB.exe').is_file():raise ValueError('Game root must contain GRB.exe.')
    if not (tk/'AnvilToolkit.dll').is_file() or not (tk/'Libs').is_dir():raise ValueError('Select AnvilToolkit folder containing DLL and Libs.')
    for path in (ar, bk, mm.WORK):
        if be.within(gm,path):raise ValueError('Mod/backup/workspace directories cannot be inside the game folder.')
    if be.within(gm,bk) or be.within(bk,gm):raise ValueError('Backup cannot contain the game folder.')
    if be.within(tk,bk) or be.within(tk,mm.WORK):raise ValueError('Backup/workspace cannot reside inside toolkit folder.')
    return ar,gm,tk,bk


def _perform(archive_dir,game,toolkit,backup, *, stage=True, install=False,
            allow_subset=False,notify=lambda x:print(x,flush=True),
            saved_mods=None, inspect=None, stage_func=None, game_check=be.game_processes_running,
            full_game_scan=False):
    import deployment_policy
    deployment_policy.require_ready(game)
    ar,gm,tk,bk=create_paths(archive_dir,game,toolkit,backup)
    # Foundation archives must be composed BEFORE mapping or repacking any
    # resources. Installing them after an old resource stage erases those edits.
    import layered_pipeline
    selected=choose_default_flags(mm.scan_archives(ar, saved_mods if saved_mods is not None else mm.load(mm.STATE,{})))
    if layered_pipeline.has_foundations(selected):
        return layered_pipeline.perform(selected,gm,tk,bk,stage=stage,install=install,
            allow_subset=allow_subset,notify=notify,inspect=inspect,game_check=game_check,
            full_game_scan=full_game_scan)
    run=mm.WORK/'auto_runs'/(time.strftime('%Y%m%d_%H%M%S')+'_'+os.urandom(3).hex())
    run.mkdir(parents=True,exist_ok=False)
    report={'schema':'DEEF.auto_setup.19','state':'SCANNING','installRequested':bool(install),
            'subsetApproved':bool(allow_subset),'game':str(gm),'archiveFolder':str(ar),
            'characterization':None,'batch':None,'transaction':None,'errors':[]}
    logpath=run/'AUTO_SETUP_CONSOLE.txt'
    def log(line):
        s=str(line)
        with logpath.open('a',encoding='utf-8') as f:f.write(s if s.endswith('\n') else s+'\n')
        notify(s)
    def write():be.record_json(run/'AUTO_SETUP_RESULT.json',report)
    try:
        old= saved_mods if saved_mods is not None else mm.load(mm.STATE,{})
        mods=mm.scan_archives(ar,old)
        if not mods:raise RuntimeError('No supported ZIP/7z/RAR archives were found in selected folder.')
        mods=choose_default_flags(mods)
        mm.save(mm.STATE,mods)
        log('Discovered {} archives and {} files.\n'.format(len(mods),sum(len(m['members']) for m in mods.values())))
        report['archives']=len(mods);report['members']=sum(len(m['members']) for m in mods.values())
        report['state']='CHARACTERIZING';write()
        mapped,details,path=auto_mapping.analyze(mods,gm,tk,run_dir=run/'characterization',inspect=inspect,log=log,
            full_game_scan=full_game_scan)
        report['characterization']=str(path)
        report['mappingStatistics']=details['statistics']
        report['verifiedRoutes']=details['verifiedCount']
        report['notMapped']=details['unmappedCount']
        report['blockingUnmapped']=details['blockingUnmappedCount']
        report['ignoredSupportFiles']=details['ignoredSupportCount']
        report['forgeDiagnostics']=details.get('gameForgeDiagnostics',{})
        report['blockReasonStatistics']=details.get('blockReasonStatistics',{})
        report['diagnosticSummary']=str(path.parent/'AUTO_CHARACTERIZATION_SUMMARY.txt')
        # Missing targets are classified into a separate, read-only insertion queue.
        # They NEVER become replacement routes, even when verified-subset is enabled.
        try:
            audit=new_resource_audit.run(path,outdir=run/'additions')
            addition_status=safe_additions.build(audit,gm,ar)
            be.record_json(run/'additions'/'ADDITION_STATUS.json',addition_status)
            report['newResourceAudit']=str(run/'additions'/'NEW_RESOURCE_AUDIT.json')
            report['additionStatus']=str(run/'additions'/'ADDITION_STATUS.json')
            report['newResourceCandidates']=addition_status['candidateCount']
            report['deployableNewResources']=0
            log('New mod assets: {} identified; {} are eligible for private insertion trials. The writer must independently verify a staged result before it can be installed.\n'.format(
                addition_status['candidateCount'],addition_status['privateTrialCount']))
        except Exception as addition_error:
            # Preserve the existing stage workflow but do not obscure audit failure.
            report['additionAuditError']=str(addition_error)
            log('WARNING: New-resource audit could not complete: {}. No addition is installed.\n'.format(addition_error))
        report['gameForgeCensus']=str(path.parent/'GAME_FORGE_CENSUS.json')
        mm.save(mm.STATE,mapped)
        log('Characterization: {} locally resolved, {} blocking unmatched, {} ignored supporting files. See {}\n'.format(
            details['verifiedCount'],details['blockingUnmappedCount'],details['ignoredSupportCount'],path))
        report['state']='CHARACTERIZED';write()
        if not stage:return run,report
        if details['verifiedCount']==0:
            report['state']='NO_VERIFIED_ROUTES'
            log('ZERO VERIFIED ROUTES: refusing staging and LIVE installation even if subset mode is enabled.\n'
                'Open {} for per-Forge sizes and failure reasons.\n'.format(report['diagnosticSummary']))
            write();return run,report
        if details['blockingUnmappedCount'] and not allow_subset:
            report['state']='INCOMPLETE_REVIEW_REQUIRED'
            log('AUTOMATIC INSTALL BLOCKED: {} unmapped/excluded members. Enable "verified subset" only if you accept omissions.\n'.format(details['blockingUnmappedCount']))
            write();return run,report
        plan=be.make_plan(mapped,gm,tk,allow_partial=True)
        be.record_json(run/'AUTO_PLAN.json',plan)
        if plan['issues']:
            report['state']='PLAN_BLOCKED';report['errors']=plan['issues'];write()
            log('Plan blocked: {}\n'.format(' | '.join(plan['issues'])))
            return run,report
        report['state']='STAGING';write()
        log('Staging {} verified replacement routes; live game remains untouched.\n'.format(len(plan['routes'])))
        kwargs={'log':log,'allow_route_exclusions':allow_subset}
        if inspect is not None:kwargs['inspect_func']=inspect
        if stage_func is not None:kwargs['stage_func']=stage_func
        result=be.stage_plan(plan,run/'batch',**kwargs)
        report['batch']=str(run/'batch'/'BATCH_RESULT.json')
        report['state']='STAGED_VERIFIED';report['stagedForges']=len(result['outputs']);
        report['stagedRoutes']=len(result['finishedRoutes']);report['identityExcludedRoutes']=len(result.get('excludedRoutes',[]));
        report['identityExclusions']=str(run/'batch'/'IDENTITY_EXCLUSIONS.json');write()
        log('STAGED VERIFIED {} Forge files.\n'.format(len(result['outputs'])))
        if not install:return run,report
        if report.get('additionAuditError'):
            report['state']='ADDITION_AUDIT_REQUIRED';write()
            log('Live automatic installation blocked because new-resource categorization failed; verified batch remains staged.\n')
            return run,report
        report['state']='INSTALLING';write()
        tx=be.install_staged(report['batch'],bk,game_check=game_check)
        report['transaction']=str(tx);report['state']='INSTALLED_VERIFIED_BYTES';write()
        log('LIVE INSTALL COMPLETE: backed up originals at {}. Gameplay has NOT been verified.\n'.format(tx))
        return run,report
    except Exception as exc:
        report['state']='FAILED';report['errors'].append(str(exc))
        write();log('ERROR: '+traceback.format_exc())
        raise


def show_gui():
    from app_v20 import show_gui as launch
    return launch()


def cli():
    parser=argparse.ArgumentParser(description='DEEF v20 staged setup')
    parser.add_argument('--archive-dir');parser.add_argument('--game-dir');parser.add_argument('--toolkit');parser.add_argument('--backup-dir')
    parser.add_argument('--analyze-only',action='store_true');parser.add_argument('--stage',action='store_true')
    parser.add_argument('--install',action='store_true');parser.add_argument('--accept-verified-subset',action='store_true')
    parser.add_argument('--full-game-scan',action='store_true')
    args=parser.parse_args()
    if not args.archive_dir:show_gui();return
    if not args.toolkit:args.toolkit=DEFAULT_TOOLKIT
    for key in ('game_dir','backup_dir'):
        if not getattr(args,key):parser.error('--{} is required in CLI mode'.format(key.replace('_','-')))
    run,report=perform(args.archive_dir,args.game_dir,args.toolkit,args.backup_dir,
        stage=(args.stage or args.install) and not args.analyze_only,
        install=args.install and not args.analyze_only,allow_subset=args.accept_verified_subset,
        full_game_scan=args.full_game_scan)
    print('Result:',run/'AUTO_SETUP_RESULT.json');print('Status:',report['state'])
    if report['state'] in ('FAILED','PLAN_BLOCKED','INCOMPLETE_REVIEW_REQUIRED'):sys.exit(2)



def perform(*args, build_scope='ENABLED_MOD_SELECTION', **kwargs):
    """Keep single-mod diagnostics and analysis results out of live installation."""
    if not kwargs.get('stage',True) and build_scope=='ENABLED_MOD_SELECTION':build_scope='ANALYSIS_ONLY'
    private=build_scope!='ENABLED_MOD_SELECTION'
    if private and kwargs.get('install'):
        raise ValueError('Diagnostic/analysis batches cannot be installed. Use Enabled mod selection and Scan and stage.')
    run,report=_perform(*args,**kwargs)
    report['buildScope']=build_scope
    report['installable']=not private
    if report.get('batch'):
        manifest=Path(report['batch']);state=json.loads(manifest.read_text(encoding='utf-8'))
        state['buildScope']=build_scope
        report['selectedArchiveCount']=len(state.get('selectedArchives',[key for key,mod in mm.load(mm.STATE,{}).items() if mod.get('enabled',True)]))
        if private:state['privateOnly']=True
        be.record_json(manifest,state)
    be.record_json(Path(run)/'AUTO_SETUP_RESULT.json',report)
    return run,report


if __name__=='__main__':cli()
