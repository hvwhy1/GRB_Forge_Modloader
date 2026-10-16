"""Independently validate missing authored weapon .data on private Forge copies.

This is a diagnostic, not an installation manifest: weapon definitions, complete
parent merges and competing variants must also resolve before a full installation.
"""
import storage_guard
from pathlib import Path
import json,time,uuid,subprocess
import auto_mapping as am
import mod_manager as mm
import batch_engine as be
import mod_coverage


def run(characterization,toolkit,notify=print):
    document=json.loads(Path(characterization).read_text(encoding='utf-8'))
    game=Path(document['game']).resolve();toolkit=Path(toolkit).resolve()
    folder=mm.WORK/'weapon_asset_trials'/(time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]);folder.mkdir(parents=True)
    report={'state':'PRIVATE_TEST_RUNNING','gameModified':False,'eligibleForDeployment':False,'mods':[]}
    be.record_json(folder/'RESULT.json',report)
    dll=be.build_bridge(toolkit,notify)
    names=sorted({r['archive'] for r in document['entries'] if mod_coverage.WEAPON_PACKS.search(r['archive'])})
    for n,name in enumerate(names):
        rows=[r for r in document['entries'] if r['archive']==name]
        requests=[];target=None
        for row in rows:
            if row['status']!='UNRESOLVED_TARGET' or 'NOT_IN_INSTALLED_GAME' not in row.get('explanation',''):continue
            route=am.infer_member_route(row['member'])
            if not route or route['kind']!='data' or route['forge'].casefold()!='datapc_resources_patch_01.forge':continue
            source=next((r['resolved']['sourceArchive'] for r in rows if r.get('resolved')),None)
            if not source:raise ValueError('No pinned archive provenance for '+name)
            payload=mm.extraction_target(source,row['member'])
            requests.append({'data':route['data'],'payload':str(payload)})
            target=game/route['forge']
        item={'mod':name,'newAssetCandidates':len(requests),'completeModVerified':False,'eligibleForDeployment':False};report['mods'].append(item)
        if not requests:item['state']='NO_OUTER_CANDIDATES';continue
        work=folder/str(n);work.mkdir();request=work/'REQUEST.json';proof=work/'PROOF.json';output=work/target.name
        be.record_json(request,requests);notify(f'Private weapon asset test: {name}, {len(requests)} new .data assets')
        with (work/'CONSOLE.txt').open('w',encoding='utf-8') as log:
            proc=storage_guard.run(['dotnet',str(dll),'stream-add-outer-trial',str(toolkit),str(target),str(proof),str(request),str(output)],stdout=log,stderr=subprocess.STDOUT)
        result=json.loads(proof.read_text(encoding='utf-8')) if proof.exists() else {}
        valid=proc.returncode==0 and result.get('verified') is True and result.get('independentAnvilIndexVerified') is True and result.get('addedEntries')==len(requests) and result.get('sourceUnchanged') is True and result.get('outputSha256','').casefold()==be.sha256(output).casefold()
        item.update(state='NEW_ASSETS_STRUCTURALLY_VERIFIED' if valid else 'REJECTED',proof=str(proof),output=str(output) if valid else None)
        be.record_json(folder/'RESULT.json',report)
    report['state']='PRIVATE_TEST_FINISHED';be.record_json(folder/'RESULT.json',report)
    notify(str(folder/'RESULT.json'));return folder,report


if __name__=='__main__':
    import sys
    run(sys.argv[1],mm.load(mm.CONFIG,{})['toolkit'])
