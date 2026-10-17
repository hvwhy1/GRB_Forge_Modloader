"""Reuse only complete verified Forge outputs; shared containers rebuild together."""
import copy,hashlib,json,os
from pathlib import Path
import batch_engine as be,mod_manager as mm,conflict_policy as cp

PHASES=('beforeOuter','afterOuter','preResource')

class RestartFresh(RuntimeError):pass


def capture_phase(phase,view,run,current,checkpoints,reused):
    from layered_pipeline import link_copy
    for target,digest in current.items():
        if target in reused:continue
        rows=checkpoints.setdefault(target,{})
        prior=next((row for row in rows.values() if row['sha256']==digest),None)
        if prior:rows[phase]=dict(prior);continue
        file=Path(run)/'PhaseSources'/phase/Path(target).name
        link_copy(Path(view)/Path(target).name,file);rows[phase]={'file':str(file),'sha256':digest}


def advance_view(phase,view,current,reused,through=None):
    from layered_pipeline import link_copy
    for target,item in reused.items():
        if through is not None and Path(target).name>=through:continue
        row=item['phaseSources'][phase]
        if current[target]==row['sha256']:continue
        file=Path(view)/Path(target).name;file.unlink();link_copy(row['file'],file);current[target]=row['sha256']


def revision():
    h=hashlib.sha256()
    files=list(mm.ROOT.glob('*.py'))+list((mm.ROOT/'src').glob('*.cs'))+list((mm.ROOT/'src').glob('*.csproj'))+list((mm.ROOT/'data').glob('*.json'))
    for p in sorted(files):
        h.update(p.name.encode());h.update(p.read_bytes())
    return h.hexdigest()


def fingerprints(mods,game,baseline,cfg,global_lookups=None):
    import complete_content as cc,layered_pipeline as lp,manual_install_rules as rules
    targets=set(baseline['hashes']);by_name={Path(t).name.casefold():t for t in targets}
    dependencies={t:{} for t in targets};handled=set();routes={};uncertain=False
    def add(mod,member,forge):
        target=by_name.get(Path(forge).name.casefold())
        if target:
            dependencies[target].setdefault(mod,[]).append(member)
            routes.setdefault((mod,member),set()).add(target);handled.add((mod,member))
    outer,inner=cc.inventory(mods)
    for forge,items in list(outer.items())+[(key[0],items) for key,items in inner.items()]:
        for item in items:add(item['mod'],item['member'],forge)
    for row in lp.foundation_plan(mods,game,True):add(row['mod'],row['member'],row['forge'])
    hashes={}
    for key,mod in mods.items():
        if not mod.get('enabled',True):continue
        hashes[key]=be.sha256(mod['archive'])
        helper=Path(mod['archive']).name.casefold().startswith('wabo compatibility tool-')
        if helper:
            for target in targets:dependencies[target].setdefault(key,[]).append('COMPATIBILITY_READER')
        for member in mod.get('members',[]):
            if (key,member) in handled or helper or rules.excluded_variant(mod['archive'],member,cfg):continue
            if Path(member).suffix.lower() in ('.txt','.md','.pdf','.png','.jpg','.jpeg','.dll','.exe','.ini','.addon64','.pck','.fx','.fxh','.h'):continue
            # Unrecognized payloads may affect any Forge. Never guess a smaller
            # invalidation set from a filename or a previous successful scan.
            if member.lower().endswith('.metafile'):
                import re
                match=re.search(r'(DataPC[^/\\]*\.forge)',member,re.I)
                if match:add(key,member,match.group(1));continue
            uncertain=True
            for target in targets:add(key,member,target)
    settings={k:v for k,v in cp.build_settings(cfg).items() if k in cp.BUILD_KEYS[:cp.BUILD_KEYS.index('native_package')]}
    settings['weapon_variants']=cp.build_settings(cfg)['weapon_variants']
    toolkit=Path(cfg.get('toolkit') or '')
    binaries=[toolkit/'AnvilToolkit.dll']+list((toolkit/'Libs').glob('*.dll')) if toolkit.is_dir() else []
    settings['toolkit_hashes']={p.name:be.sha256(p) for p in sorted(binaries) if p.is_file()}
    reader=Path(cfg.get('wabo_compatibility_tool') or '')
    if reader.is_file():settings['external_weapon_reader']=be.sha256(reader)
    if global_lookups is None:global_lookups=mm.load(cfg.get('last_stage_manifest') or mm.WORK/'missing-stage.json',{}).get('globalLookupForges',[])
    engine=revision();result={}
    all_rows=[(target,key,hashes[key],sorted(set(members))) for target,groups in sorted(dependencies.items()) for key,members in sorted(groups.items())]
    for target in targets:
        rows=[(key,hashes[key],sorted(set(members))) for key,members in sorted(dependencies[target].items())]
        broad=uncertain or target in global_lookups
        result[target]=hashlib.sha256(json.dumps([engine,baseline['hashes'] if broad else baseline['hashes'][target],settings,all_rows if broad else rows],sort_keys=True).encode()).hexdigest()
    return result,routes


def global_lookups(layers,reused):
    result=set()
    for target,item in reused.items():
        if target in mm.load(item['manifest'],{}).get('globalLookupForges',[]):result.add(target)
    for layer in layers:
        if layer.get('kind')!='composed_assets':continue
        proof=mm.load(layer['contentReceipt'],{})
        if proof.get('newContainers',0)>0 or any(row.get('baseForge') and Path(row['baseForge']).name!=Path(layer['forge']).name for row in proof.get('rows',[])):result.add(layer['forge'])
    return sorted(result)


def rebase(value,old,new):
    if isinstance(value,dict):return {rebase(k,old,new):rebase(v,old,new) for k,v in value.items()}
    if isinstance(value,list):return [rebase(v,old,new) for v in value]
    if isinstance(value,str):
        if value==str(old):return str(new)
        if value.startswith(str(old)+os.sep):return str(new)+value[len(str(old)):]
    return value


def export_target(manifest,target,dest):
    """Flatten reuse to its original proof, with only required binary inputs."""
    import layered_pipeline as lp
    manifest=Path(manifest).resolve();old=manifest.parent;state=mm.load(manifest,{})
    if target in state.get('reusedForges',{}):
        return export_target(state['reusedForges'][target]['manifest'],target,dest)
    doc=copy.deepcopy(state)
    for name in ('foundationLayers','postLayers','finishedRoutes','excludedRoutes'):
        doc[name]=[r for r in doc.get(name,[]) if r['forge']==target]
    doc['plan']['routes']=[r for r in doc['plan']['routes'] if r['forge']==target]
    doc['plan']['sourceHashes']={target:doc['plan']['sourceHashes'][target]}
    doc['outputs']={target:doc['outputs'][target]};doc['runtimeManifest']=None
    doc['phaseSources']={target:doc.get('phaseSources',{}).get(target,{})}
    doc['runtimeContributions']=[];doc['installedPayloads']=[]
    doc['presentPayloads']=[r for r in doc.get('presentPayloads',[]) if r['forge']==target]
    doc['baselineRestores']=[t for t in doc.get('baselineRestores',[]) if t==target]
    doc.pop('reusedForges',None);files={doc['outputs'][target]['staged']};documents={}
    files.update(row['file'] for row in doc['phaseSources'][target].values())
    resource=doc.get('resourceManifest')
    if resource:
        sub=mm.load(resource,{});virtual=str(Path(sub['plan']['gameRoot'])/Path(target).name)
        if virtual in sub['outputs']:
            for name in ('finishedRoutes','excludedRoutes'):sub[name]=[r for r in sub[name] if r['forge']==virtual]
            sub['plan']['routes']=[r for r in sub['plan']['routes'] if r['forge']==virtual]
            sub['plan']['sourceHashes']={virtual:sub['plan']['sourceHashes'][virtual]}
            sub['outputs']={virtual:sub['outputs'][virtual]};documents[resource]=sub
            files.update((virtual,str(Path(virtual).parent/'GRB.exe'),sub['outputs'][virtual]['staged']))
        else:doc['resourceManifest']=None
    # Copy receipt JSON transitively, but never copy intermediate Forge outputs
    # merely because a diagnostic proof mentions their former filename.
    pending=[doc]+list(documents.values());seen=set()
    def strings(value):
        if isinstance(value,dict):
            for key,row in value.items():yield from strings(key);yield from strings(row)
        elif isinstance(value,list):
            for row in value:yield from strings(row)
        elif isinstance(value,str):yield value
    while pending:
        value=pending.pop()
        for name in strings(value):
            path=Path(name)
            if not path.is_absolute() or not be.within(old,path) or not path.is_file():continue
            if path.suffix.lower()=='.json' and name not in seen:
                seen.add(name)
                if name not in documents:documents[name]=mm.load(path,{})
                pending.append(documents[name])
    for layer in doc['foundationLayers']+doc.get('postLayers',[]):
        if layer.get('archive'):files.add(layer['archive'])
        files.update(layer.get('sourceArchives',{}))
    for row in doc['plan']['routes']+doc['presentPayloads']:
        if row.get('archive'):files.add(row['archive'])
    if resource and doc.get('resourceManifest'):
        for row in documents[resource]['plan']['routes']:
            if row.get('archive'):files.add(row['archive'])
    dest.mkdir(parents=True)
    for name in files:
        source=Path(name).resolve()
        if not be.within(old,source) or not source.is_file():raise ValueError('Incremental input missing/outside original stage: '+str(source))
        lp.link_copy(source,Path(rebase(str(source),old,dest)))
    for name,value in documents.items():be.record_json(Path(rebase(name,old,dest)),rebase(value,old,dest))
    output=dest/'BATCH_RESULT.json';be.record_json(output,rebase(doc,old,dest))
    lp.inspect_stage(output,Path(target).parent)
    return output


def reuse(mods,game,baseline,run,previous,log,enabled=True):
    import layered_pipeline as lp
    cfg=mm.load(mm.CONFIG,{});keys,routes=fingerprints(mods,game,baseline,cfg)
    result={};old_path=Path(cfg.get('last_stage_manifest') or '')
    if not enabled or not cfg.get('complete_content_pipeline',True):return result,keys,routes,[]
    if not old_path.is_file():return result,keys,routes,[]
    old=mm.load(old_path,{})
    if old.get('baselineId')!=baseline['baselineId'] or old.get('writer')!=lp.WRITER:return result,keys,routes,[]
    changed_after_build={row['forge'] for row in old.get('postLayers',[])}
    eligible=[t for t in old.get('outputs',{}) if t not in set(old.get('baselineRestores',[]))|changed_after_build and keys.get(t)==old.get('forgeFingerprints',{}).get(t) and set(old.get('phaseSources',{}).get(t,{}))==set(PHASES) and not previous.get(t,{}).get('previousFoundationOnly')]
    if not eligible:return result,keys,routes,[]
    lp.inspect_stage(old_path,game)
    for target in eligible:
        entry=old['outputs'][target]
        if be.sha256(entry['staged'])!=entry['stagedSha256']:raise ValueError('Retained stage bytes changed; cannot reuse '+Path(target).name)
        checked={entry['staged']:entry['stagedSha256']}
        for row in old['phaseSources'][target].values():
            if row['file'] not in checked:checked[row['file']]=be.sha256(row['file'])
            if checked[row['file']]!=row['sha256']:raise ValueError('Retained phase source changed; cannot reuse '+Path(target).name)
        certificate=export_target(old_path,target,run/'Reuse'/Path(target).stem)
        lp.inspect_stage(certificate,game,verify_backups=True)
        result[target]={'manifest':str(certificate),'fingerprint':keys[target],'phaseSources':mm.load(certificate,{})['phaseSources'][target]}
        log('Reusing verified complete Forge: '+Path(target).name)
    actual={}
    for row in old.get('finishedRoutes',[]):
        actual.setdefault((row['mod'],row['member']),set()).add(row['forge'])
        for key,members in row.get('coveredMembers',{}).items():
            for member in members:actual.setdefault((key,member),set()).add(row['forge'])
        for member in row.get('addedMembers',[]):actual.setdefault((row['mod'],member),set()).add(row['forge'])
    for pair,destinations in actual.items():
        if destinations.issubset(result) and pair in routes:routes[pair]=destinations
    details=mm.load(old_path.parent/'characterization/AUTO_CHARACTERIZATION.json',{}).get('entries',[])
    return result,keys,routes,details


def exclude_reused(mods,reused,routes):
    updated=copy.deepcopy(mods)
    for key,mod in updated.items():mod['members']=[m for m in mod.get('members',[]) if not routes.get((key,m)) or not routes[(key,m)].issubset(reused)]
    return updated


def select_one(folder,selected,current):
    mods=mm.scan_archives(folder,current)
    matches=[selected] if selected in mods else [key for key in mods if Path(key).name==selected] if selected else []
    if selected and len(matches)!=1:raise ValueError('Choose one unambiguous archive to add to the current setup.')
    for key in mods:
        if key not in current and (not matches or key!=matches[0]):mods[key].update(enabled=False,_enabled_by_user=True)
    if matches:mods[matches[0]].update(enabled=True,_enabled_by_user=True)
    return mods
