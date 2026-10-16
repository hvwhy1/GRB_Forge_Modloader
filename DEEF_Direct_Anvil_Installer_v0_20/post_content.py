"""Extend a verified composed build with newly supplied camera/cover tweaks."""
import storage_guard
from pathlib import Path
from collections import defaultdict
import copy,json,subprocess,time,uuid
import mod_manager as mm
import batch_engine as be
import complete_content as cc
import layered_pipeline as lp


def prepare(manifest,archives,notify=print):
    manifest=Path(manifest).resolve();state=json.loads(manifest.read_text(encoding='utf-8'))
    lp.inspect_stage(manifest)
    game=Path(state['plan']['gameRoot']);target=str(game/'DataPC_patch_01.forge')
    work=manifest.parent/('FinalTweaks_'+time.strftime('%H%M%S')+'_'+uuid.uuid4().hex[:6]);work.mkdir()
    mods={}
    for n,archive in enumerate(archives):
        archive=Path(archive).resolve();digest=be.sha256(archive)
        pinned=work/'Archives'/str(n)/archive.name;be._copy_verify(archive,pinned,digest)
        mods[archive.name]={'archive':str(pinned),'members':mm.archive_inventory(pinned),'enabled':True}
    outer,inner=cc.inventory(mods)
    expected={('DataPC_patch_01.forge','game bootstrap settings.data'),('DataPC_patch_01.forge','dbcontainerentry_0x104634f921.data')}
    if outer or set(inner)!=expected:raise ValueError('Unexpected final tweak destinations')
    if sum(map(len,inner.values()))!=3:raise ValueError('Expected two updated ADS nodes and one documented cover removal')
    cfg=mm.load(mm.CONFIG,{});dll=be.build_bridge(cfg['toolkit'],notify)
    source=state['outputs'][target]['staged']
    layers=[];digest=state['outputs'][target]['stagedSha256']
    for n,((forge,parent),items) in enumerate(sorted(inner.items())):
        folder=work/str(n);folder.mkdir();req=[];covered=defaultdict(list)
        for item in items:
            req.append({'name':item['name'],'payload':str(cc.payload(item)),'operation':item.get('operation','replace')})
            covered[item['mod']].append(item['member'])
        be.record_json(folder/'REQUEST.json',req);proof=folder/'PROOF.json';output=folder/'Output/DataPC_patch_01.forge';output.parent.mkdir()
        command=['dotnet',str(dll),'compose-inner',cfg['toolkit'],source,parent,str(folder/'REQUEST.json'),str(proof),str(output)]
        with (folder/'CONSOLE.txt').open('w',encoding='utf-8') as f:result=storage_guard.run(command,stdout=f,stderr=subprocess.STDOUT)
        receipt=json.loads(proof.read_text(encoding='utf-8')) if proof.is_file() else {}
        if result.returncode or receipt.get('verified') is not True:raise ValueError('Final tweak composition failed: '+str(proof))
        if receipt['sourceSha256']!=digest or be.sha256(output)!=receipt['outputSha256']:raise ValueError('Final tweak composition fingerprint mismatch')
        layer={'mod':'Updated ADS and disabled forced cover','archive':next(iter(mods.values()))['archive'],
            'member':'UPDATED_ADS_AND_DISABLED_COVER_'+str(n),'forge':target,'data':parent,'kind':'composed_inner','phase':'FINAL_TWEAKS',
            'inputSha256':digest,'outputSha256':receipt['outputSha256'],'contentReceipt':str(proof),'coveredMembers':dict(covered),
            'sourceArchives':{m['archive']:be.sha256(m['archive']) for m in mods.values()}}
        layers.append(layer);source=str(output);digest=receipt['outputSha256']
        notify('Final tweak parent independently verified: '+parent)
    plan={'sourceManifest':str(manifest),'work':str(work),'layers':layers,'output':str(output)}
    be.record_json(work/'PREPARED.json',plan);notify('Updated ADS and cover removal independently verified: '+str(work));return work/'PREPARED.json'


def finalize(prepared,previous_transaction):
    plan=json.loads(Path(prepared).read_text(encoding='utf-8'));source=Path(plan['sourceManifest']);work=Path(plan['work'])
    state=json.loads(source.read_text(encoding='utf-8'));layers=plan['layers'];target=layers[0]['forge']
    if state['outputs'][target]['stagedSha256']!=layers[0]['inputSha256']:raise ValueError('Final tweak base changed')
    txfile=Path(previous_transaction).resolve();tx=json.loads(txfile.read_text(encoding='utf-8'))
    if tx.get('status')!='INSTALLED' or tx.get('mode')!='LAYERED_VERIFIED_RECOVERY':raise ValueError('Complete main installation/recovery first')
    for forge,entry in state['outputs'].items():
        rows=[r for r in tx['files'] if r['target']==forge and r['originalSha256']==entry['sourceSha256'] and r['installedSha256']==entry['stagedSha256']]
        if len(rows)!=1:raise ValueError('Installed main build does not match final tweak base')
        entry.update(previousSha256=entry['stagedSha256'],previousTransaction=str(txfile));entry.pop('previousFoundationOnly',None)
    state.setdefault('postLayers',[]).extend(layers);state['finishedRoutes'].extend(copy.deepcopy(layers));state['plan']['routes'].extend(copy.deepcopy(layers))
    state['outputs'][target].update(staged=plan['output'],stagedSha256=layers[-1]['outputSha256'])
    manifest=source.parent/'BATCH_RESULT_UPDATED_TWEAKS.json';be.record_json(manifest,state);lp.inspect_stage(manifest)
    details=json.loads((source.parent/'characterization/AUTO_CHARACTERIZATION.json').read_text(encoding='utf-8'))
    for row in details['entries']:
        if row['archive'].lower().startswith('various tweaks-537-') and '/no ads zoom/' in '/'+row['member'].replace('\\','/').lower():
            row.update(status='USER_VARIANT_EXCLUDED',explanation='Superseded by selected author No ADS Zoom Updated')
    for layer in layers:
        for key,members in layer['coveredMembers'].items():
            details['entries'].extend({'archive':key,'member':member,'status':'VERIFIED_COMPOSED_CONTENT','explanation':'Updated authored ADS bytes or documented cover removal independently read back'} for member in members)
    be.record_json(work/'CHARACTERIZATION.json',details)
    import mod_coverage
    coverage=mod_coverage.write(details,state,source.parent)
    cfg=mm.load(mm.CONFIG,{});cfg['last_stage_manifest']=str(manifest);mm.save(mm.CONFIG,cfg)
    mm.save(mm.STATE,mm.scan_archives(cfg['archive_dir'],mm.load(mm.STATE,{})))
    be.record_json(work/'FINAL_RESULT.json',{'manifest':str(manifest),'incompleteMods':coverage['incompleteMods'],'gameModified':False})
    return manifest
