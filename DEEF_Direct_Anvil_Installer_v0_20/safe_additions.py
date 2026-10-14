"""Fail-closed inventory and diagnostic queue for genuinely NEW Anvil resources.

Existing .data replacements and inner-object replacements belong to the proven
streaming batch. A new .data directory entry, a new object in an existing .data,
and a missing parent container are three distinct operations. None of the
experimental insertion outputs is authorized for live deployment.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path, PureWindowsPath
from datetime import datetime, timezone
import new_resource_audit
import batch_engine as be

ROOT = Path(__file__).resolve().parent
READY = {'NEW_FORGE_DATA_ENTRY_CANDIDATE': 'OUTER_PRIVATE_TRIAL',
         'NEW_INNER_OBJECT_PARENT_FOUND': 'INNER_PRIVATE_TRIAL'}

def logical(name):
    s=str(name).replace('\\','/').split('/')[-1]
    a,sep,b=s.partition('_-_')
    return (b if sep and a.isdecimal() else s).casefold()

def basename(name):
    return PureWindowsPath(str(name)).name

def classify_candidate(row, game_root, mods_dir):
    r=dict(row)
    # These rows were discovered as resources supplied by mods. A failed or
    # unresolved insertion test changes their resolution state, not their kind.
    r['isNewModAsset']=True
    r['assetStatus']='NEW_MOD_ASSET_CANDIDATE'
    category=r.get('classification','')
    forge=Path(str(r.get('parentForge','')))
    archive=Path(mods_dir)/basename(r.get('sourceArchive',''))
    member=str(r.get('member','')).replace('\\','/')
    r['trialKind']=READY.get(category, 'BLOCKED_PARENT_OR_UNRESOLVED')
    r['sourceArchivePath']=str(archive)
    r['reason']=''
    r['liveInstallationAllowed']=False
    # The audit is a lead, never authorization to create a Forge destination.
    # Every private test requires that the Forge is an ACTIVE game-root file.
    try:
        if not forge.is_file() or forge.resolve().parent!=Path(game_root).resolve():
            r['reason']='ACTIVE_GAME_ROOT_FORGE_NOT_FOUND'
        elif not archive.is_file():
            r['reason']='MOD_ARCHIVE_MISSING'
        elif member.startswith('/') or '..' in member.split('/') or ':' in member.split('/')[0]:
            r['reason']='UNSAFE_ARCHIVE_MEMBER'
        elif not r.get('parentData','').lower().endswith('.data'):
            r['reason']='NO_CANONICAL_PARENT_DATA'
        elif r['trialKind']=='OUTER_PRIVATE_TRIAL' and (r.get('kind')!='data' or not member.lower().endswith('.data')):
            r['reason']='WHOLE_DATA_FILE_REQUIRED'
        elif r['trialKind']=='INNER_PRIVATE_TRIAL' and (r.get('kind')!='inner' or not r.get('newObject')):
            r['reason']='NEW_NESTED_OBJECT_REQUIRED'
        elif r['trialKind']=='BLOCKED_PARENT_OR_UNRESOLVED':
            r['reason']='NO_VERIFIED_PARENT_CONTAINER_OR_INSERTION_METHOD'
        else:
            r['reason']='PRIVATE_TRIAL_ONLY'
    except (OSError,ValueError):
        r['reason']='UNRESOLVED_OR_UNSAFE_PATH'
    r['privateTrialEligible'] = r['reason']=='PRIVATE_TRIAL_ONLY'
    return r

def build(audit, game_root, mods_dir):
    if audit.get('schema') not in ('DEEF.new_resource_audit.1','DEEF.new_resource_audit.2'):
        raise ValueError('Not a DEEF new-resource audit')
    if not isinstance(audit.get('candidates'),list):raise ValueError('Audit has no candidates list')
    if len(audit['candidates'])>10000:raise ValueError('Suspiciously large addition audit')
    if str(Path(audit.get('game','')).resolve()).casefold()!=str(Path(game_root).resolve()).casefold():
        raise ValueError('Audit was generated for a different game root; regenerate the audit')
    rows=[classify_candidate(x,game_root,mods_dir) for x in audit['candidates']]
    # Collision check uses logical resource identity, ignoring unstable indices.
    by_target=defaultdict(list)
    for row in rows:
        if row['trialKind']=='OUTER_PRIVATE_TRIAL':
            resource=row.get('parentData','')
        else:
            resource=row.get('newObject','')
        key=(str(row.get('parentForge','')).casefold(),
             logical(row.get('parentData','')), logical(resource), row.get('kind',''))
        by_target[key].append(row)
    for group in by_target.values():
        if len(group)>1:
            for row in group:
                row['reason']='MULTIPLE_MODS_TARGET_SAME_NEW_RESOURCE'
                row['privateTrialEligible']=False
    counts=Counter(x['reason'] for x in rows)
    ready=[{'index':x.get('index'), 'kind':x['trialKind'],'archive':x.get('sourceArchive'),
            'member':x.get('member'),'forge':x.get('parentForge'),'data':x.get('parentData'),
            'object':x.get('newObject','')} for x in rows if x['privateTrialEligible']]
    return {'schema':'DEEF.safe_additions.1','generatedUtc':datetime.now(timezone.utc).isoformat(),
            'gameRoot':str(Path(game_root).resolve()),'modsDir':str(Path(mods_dir).resolve()),
            'candidateCount':len(rows),'classificationCounts':dict(Counter(x.get('classification','') for x in rows)),
            'statusCounts':dict(counts),'privateTrialQueue':ready,'privateTrialCount':len(ready),
            'rows':rows, 'installedNewResources':0, 'deployableNewResources':0,
            'liveInstallationEnabled':False,
        'warning':'Candidates are new mod assets, not file errors or replacements. Private readback is diagnostic and never authorizes live installation. Missing .data parents require parent creation + identity validation, not a blind insertion.'}

def run(audit_path=None,game_root=None,mods_dir=None,out_dir=None):
    import route_convention_audit
    src=Path(audit_path) if audit_path else route_convention_audit.locate_audit()
    if src is None or not src.is_file():
        raise FileNotFoundError('No NEW_RESOURCE_AUDIT.json found. Run automatic characterization or select an audit file.')
    audit=json.loads(src.read_text(encoding='utf-8-sig'))
    game_root=game_root or audit.get('game')
    if not game_root:raise ValueError('Game directory is unknown')
    if mods_dir is None:
        import mod_manager
        mods_dir=mod_manager.load(mod_manager.CONFIG,mod_manager.DEFAULT_SETTINGS).get('archive_dir')
    if not mods_dir:raise ValueError('Specify mod archive folder')
    result=build(audit,game_root,mods_dir)
    folder=Path(out_dir) if out_dir else ROOT/'workspace'/'safe_additions'
    folder.mkdir(parents=True,exist_ok=True)
    be.record_json(folder/'ADDITION_STATUS.json',result)
    with (folder/'ADDITION_QUEUE.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        keys=['index','classification','reason','privateTrialEligible','sourceArchive','member','parentForge','parentData','newObject']
        writer=csv.DictWriter(stream,fieldnames=keys,extrasaction='ignore')
        writer.writeheader();writer.writerows(result['rows'])
    lines=['DEEF — NEW mod assets (not file errors or replacements)',
           f"Audit candidates: {result['candidateCount']}",
           f"Eligible PRIVATE diagnostics: {result['privateTrialCount']}",
           'Eligible live additions: 0','']
    for key,n in sorted(result['classificationCounts'].items()):lines.append(f'{key}: {n}')
    lines+=['','Outcome categories:']
    for key,n in sorted(result['statusCounts'].items()):lines.append(f'{key}: {n}')
    lines+=['','Outer .data addition: requires a new Forge directory entry.',
            'Inner object addition: requires a verified, existing .data parent.',
            'Missing parent .data: requires a validated parent-container build.',
            'Conflicting mod variants targeting the same identity are blocked.',
           'Candidates stay labeled as new mod assets even when a parent or insertion method is unresolved.',
           'No private insertion output can be installed with the verified-stage installer.',
            'Never copy private trial .forge files into Ghost Recon Breakpoint.']
    (folder/'ADDITION_STATUS.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('\n'.join(lines));print('Reports:',folder)
    return result

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--audit');ap.add_argument('--game-root');ap.add_argument('--mods-dir');ap.add_argument('--output')
    a=ap.parse_args()
    run(a.audit,a.game_root,a.mods_dir,a.output)
