"""Account for every selected payload; a menu icon is not a complete mod."""
from collections import Counter,defaultdict
from pathlib import Path
import json
import re

WEAPON_PACKS=re.compile(r'-(?:1155|1125)-')
IGNORED={'IGNORED_SUPPORT_FILE','USER_VARIANT_EXCLUDED','USER_CONFLICT_EXCLUDED'}


def summarize(characterization,stage):
    finished=set()
    for row in stage.get('finishedRoutes',[]):
        finished.add((row['mod'],row['member']))
        finished.update((row['mod'],m) for m in row.get('addedMembers',[]))
        finished.update((key,m) for key,members in row.get('coveredMembers',{}).items() for m in members)
    finished.update((row['mod'],row['member']) for row in stage.get('presentPayloads',[]))
    finished.update((row['mod'],row['member']) for row in stage.get('runtimeContributions',[]))
    import batch_engine as be
    finished.update((row['mod'],row['member']) for row in stage.get('installedPayloads',[]) if Path(row['target']).is_file() and be.sha256(row['target'])==row['payloadSha256'])
    rejected={(r['mod'],r['member']):r.get('reason','Writer rejected payload') for r in stage.get('excludedRoutes',[])}
    groups=defaultdict(list)
    for row in characterization.get('entries',[]):
        if row['status'] in IGNORED:continue
        groups[row['archive']].append(row)
    mods=[]
    for name,entries in sorted(groups.items()):
        missing=[];accepted=[]
        for row in entries:
            key=(name,row['member'])
            if key in finished:accepted.append(row['member'])
            else:missing.append({'member':row['member'],'status':row['status'],'reason':rejected.get(key) or row.get('explanation') or row.get('blockReasonCode') or row['status']})
        # Only classify menu names as evidence; this cannot certify gameplay.
        menus=[m for m in accepted if re.search(r'(?:^|[_/])UI_',m,re.I)]
        mods.append({'mod':name,'state':'INCOMPLETE' if missing else 'ALL_SELECTED_PAYLOADS_STAGED',
            'selectedPayloads':len(entries),'stagedPayloads':len(accepted),'menuPayloadsStaged':len(menus),
            'missingPayloads':missing,'weaponAssetPack':bool(WEAPON_PACKS.search(name)),
            'gameplayVerified':False})
    return {'schema':'DEEF.mod_coverage.1','mods':mods,'incompleteMods':sum(m['state']=='INCOMPLETE' for m in mods),
        'warning':'Verified Forge writes do not imply every mod was installed. Inspect missingPayloads before calling a mod complete.'}


def write(characterization,stage,folder):
    report=summarize(characterization,stage);folder=Path(folder)
    (folder/'MOD_COVERAGE.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    lines=['Selected mod payload coverage (gameplay remains unverified)','']
    for mod in report['mods']:
        lines.append(f"{mod['mod']}: {mod['state']} ({mod['stagedPayloads']}/{mod['selectedPayloads']} payloads; {mod['menuPayloadsStaged']} menu payloads)")
        lines.extend('  MISSING: '+r['member']+' | '+r['reason'] for r in mod['missingPayloads'])
    (folder/'MOD_COVERAGE.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return report


def hold_incomplete_weapon_packs(entries):
    """Do not install an icon from a weapon pack with unmapped required assets."""
    blocked={r['archive'] for r in entries if WEAPON_PACKS.search(r['archive']) and r['status'] not in IGNORED|{'VERIFIED_LOCAL_TARGET'}}
    for row in entries:
        if row['archive'] in blocked and row['status']=='VERIFIED_LOCAL_TARGET':
            row.update(status='INCOMPLETE_WEAPON_PACK_HELD',explanation='Required weapon payloads remain unresolved. Stage the complete pack before applying its menu or asset edits.',blockReasonCode='INCOMPLETE_WEAPON_ASSET_PACK')
    return sorted(blocked)
