"""Compose prepatched Forges, complete Forge mods, then verified resource edits.

The live game is never used as scratch space. Every final Forge is swapped only
once; the recorded hash chain rejects stale stages and silent overwrites.
"""
import storage_guard
import collections
import copy
import json
import os
from pathlib import Path
import time
import uuid

import batch_engine as be
import mod_manager as mm
import deployment_policy as policy

WRITER='FORGE_LAYERED_BATCH_V20'
PHASES=('PREPATCH','COMPLETE_FORGE','FOUNDATION_CONTENT','GUIDED_CONTENT','RESOURCE_EDITS')


def has_foundations(mods):
    return any(v.get('enabled',True) and any(m.lower().endswith('.forge') for m in v.get('members',[])) for v in mods.values())


def foundation_plan(mods,game,allow_subset=False,exclusions=None):
    """No alphabetical last-wins behavior within either whole-Forge phase."""
    game=Path(game).resolve(); rows=[]; seen=set()
    exclusions=[] if exclusions is None else exclusions
    # User requested the standalone prepatch and Unlock Everything packs.
    # Precombined alternatives remain explicit exclusions in subset mode.
    standalone=any(v.get('enabled',True) and Path(v['archive']).name.casefold().startswith('unlock everything-548-') for v in mods.values())
    for key,mod in mods.items():
        if not mod.get('enabled',True):continue
        members=[m for m in mod.get('members',[]) if m.lower().endswith('.forge')]
        essentials=Path(mod['archive']).name.casefold().startswith('breakpoint modding starter essentials pack-1492-')
        vanilla=[m for m in members if essentials and Path(m.replace('\\','/')).name.casefold() in ('datapc_extra_patch_01.forge','datapc_tgt_worldmap_bootstrap_split_patch_01.forge')]
        exclusions.extend({'mod':key,'member':m,'reason':'GUIDE_VANILLA_REFERENCE_NOT_REQUIRED'} for m in vanilla)
        members=[m for m in members if m not in vanilla]
        backups=[m for m in members if not essentials and any('backup' in p.casefold() for p in m.replace('\\','/').split('/')[:-1])]
        exclusions.extend({'mod':key,'member':m,'reason':'ARCHIVED_BACKUP_NOT_INSTALL_PAYLOAD'} for m in backups)
        members=[m for m in members if m not in backups]
        alternatives=[m for m in members if 'prepatch-ue-' in m.replace('\\','/').casefold()]
        if alternatives and standalone and not allow_subset:raise ValueError('Precombined UE foundation competes with selected standalone Unlock Everything: '+key)
        exclusions.extend({'mod':key,'member':m,'reason':'PRECOMBINED_ALTERNATIVE_TO_SELECTED_STANDALONE_UNLOCK' if standalone else 'UNLOCK_EVERYTHING_NOT_SELECTED'} for m in alternatives)
        members=[m for m in members if m not in alternatives]
        if not members:continue
        if mod.get('status')!='INDEXED':raise ValueError('Unreadable foundation archive: '+key)
        # The user's prepatch-first rule overrides incidental archive enumeration.
        name=Path(mod['archive']).name.casefold()
        phase='PREPATCH' if (essentials or 'pre patch' in name or 'prepatch' in name or mod.get('installPhase')=='PREPATCH') else 'COMPLETE_FORGE'
        tier=1 if essentials else 0
        for member in members:
            leaf=Path(member.replace('\\','/')).name
            targets=[p for p in game.glob('*.forge') if p.name.casefold()==leaf.casefold()]
            if len(targets)!=1:raise ValueError('Complete Forge has no unique active filename match: '+member)
            target=targets[0]; identity=(phase,tier,target.name.casefold())
            if identity in seen:raise ValueError('Competing complete Forge files in '+phase+': '+target.name+'; choose one archive/variant')
            seen.add(identity)
            rows.append({'mod':key,'archive':str(Path(mod['archive']).resolve()),'member':member,
                         'forge':str(target.resolve()),'kind':'forge','phase':phase,'tier':tier,'data':'','object':''})
    return sorted(rows,key=lambda r:(PHASES.index(r['phase']),r['tier'],Path(r['forge']).name.casefold(),r['mod'].casefold()))


def verified_diagnostic(path,game,target,digest,verify_files=False):
    """Recognize our backed-up trial without treating it as a clean baseline."""
    receipt=Path(path).resolve()
    if not be.within(policy.WORK.resolve(),receipt) or not receipt.is_file():return False
    tx=json.loads(receipt.read_text(encoding='utf-8'))
    if tx.get('status')!='INSTALLED' or tx.get('diagnosticOnly') is not True or Path(tx.get('gameRoot','')).resolve()!=game:return False
    rows=[r for r in tx.get('files',[]) if r.get('target')==target and r.get('installedSha256')==digest]
    if len(rows)!=1 or target not in tx.get('installed',[]):return False
    proof_file=Path(tx.get('proof','')).resolve()
    if not be.within(policy.WORK.resolve(),proof_file) or not proof_file.is_file():return False
    proof=json.loads(proof_file.read_text(encoding='utf-8'))
    if any(proof.get(k) is not True for k in ('verified','sourceUnchanged','independentAnvilIndexVerified')) or proof.get('outputSha256')!=digest:return False
    candidate=Path(proof.get('output','')).resolve();backup=Path(rows[0].get('backup','')).resolve()
    if not be.within(proof_file.parent,candidate) or candidate.name!=Path(target).name or not candidate.is_file() or not be.within(receipt.parent,backup) or not backup.is_file():return False
    return not verify_files or (be.sha256(candidate)==digest and be.sha256(backup)==rows[0]['originalSha256'])


def receipt_history(game, seeds):
    """Follow explicit receipt links, never search arbitrary backup directories."""
    pending=list(seeds);seen=set();documents=[]
    while pending:
        path=Path(pending.pop(0) or '').resolve()
        if path in seen:continue
        if len(seen)>=256:raise ValueError('Backup receipt history exceeds supported depth')
        seen.add(path)
        if not be.within(policy.WORK.resolve(),path) or not path.is_file():continue
        document=json.loads(path.read_text(encoding='utf-8'))
        document_game=document.get('gameRoot',document.get('plan',{}).get('gameRoot'))
        if document_game and Path(document_game).resolve()!=game:continue
        if document.get('files'):documents.append((path,document))
        pending.extend(document[k] for k in ('installationTransaction','originalTransaction','batchManifest','runtimeTransaction','previousConfigTransaction') if document.get(k))
        pending.extend(row['previousTransaction'] for row in document.get('outputs',{}).values() if row.get('previousTransaction'))
    return documents


def clean_history_entry(history,target,digest,live):
    candidates=[]
    for receipt,document in history:
        matches=[row for row in document.get('files',[]) if row.get('target')==target and row.get('originalSha256')==digest]
        if len(matches)>1:raise ValueError('Duplicate recovery target: '+target)
        if matches:candidates.append((matches[0],receipt))
    candidates.sort(key=lambda item:item[0].get('installedSha256')!=live)
    for row,receipt in candidates:
        backup=Path(row.get('backup','')).resolve()
        if be.within(receipt.parent,backup) and backup.is_file() and be.sha256(backup)==digest:
            return row,receipt,backup
    raise ValueError('No verified original for modified Forge: '+target+'; verified baseline backup missing/changed')


def baseline_sources(game,baseline,notify=lambda s:None):
    """Use clean originals from the bounded, explicitly linked install history."""
    cfg=mm.load(mm.CONFIG,{})
    history=receipt_history(game,[cfg.get('last_transaction'),cfg.get('last_actual_transaction')])
    sources={};previous={}
    for n,(target,digest) in enumerate(baseline['hashes'].items(),1):
        notify(f'Checking baseline source {n}/{len(baseline["hashes"])}: {Path(target).name}')
        file=Path(target);live=be.sha256(file)
        if live==digest:sources[target]=file;continue
        entry,txfile,backup=clean_history_entry(history,target,digest,live)
        sources[target]=backup
        previous[target]={'previousSha256':live,'previousTransaction':str(txfile)}
        if entry.get('installedSha256')!=live:
            diagnostic=cfg.get('last_isolation_transaction','')
            if diagnostic and verified_diagnostic(diagnostic,game,target,live,verify_files=True):
                previous[target]['previousDiagnosticTransaction']=str(Path(diagnostic).resolve())
            else:previous[target]['previousFoundationOnly']=True
    return sources,previous


def link_copy(source,target):
    target.parent.mkdir(parents=True,exist_ok=True)
    try:os.link(source,target)
    except OSError:
        be._copy_verify(source,target,be.sha256(source))


def snapshot_archives(mods,run):
    """Pin selected archive bytes before long indexing; survive download moves."""
    pinned=copy.deepcopy(mods);records=[]
    for n,(key,mod) in enumerate(pinned.items()):
        if not mod.get('enabled',True):continue
        source=Path(mod['archive']).resolve()
        target=run/'Archives'/str(n)/source.name
        link_copy(source,target)
        mod['archive']=str(target)
        records.append({'mod':key,'originalArchive':str(source),'snapshot':str(target),
                        'archiveSha256':be.sha256(target)})
    be.record_json(run/'ARCHIVE_SNAPSHOT.json',records)
    return pinned


def perform(mods,game,toolkit,backups,*,stage=True,install=False,allow_subset=False,
            notify=print,inspect=None,game_check=be.game_processes_running,full_game_scan=False,choose_conflict=None):
    import auto_mapping
    baseline=policy.require_ready(game);game=Path(game).resolve();toolkit=Path(toolkit).resolve()
    import conflict_policy
    initial_settings=conflict_policy.build_settings(mm.load(mm.CONFIG,{}))
    run=mm.WORK/'auto_runs'/(time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]+'_layered')
    run.mkdir(parents=True)
    report={'schema':'DEEF.layered_setup.20','state':'PREPARING_FOUNDATIONS','game':str(game),
            'installationOrder':list(PHASES),'batch':None,'errors':[]}
    def log(line):
        with (run/'AUTO_SETUP_CONSOLE.txt').open('a',encoding='utf-8') as f:f.write(str(line).rstrip()+'\n')
        notify(str(line))
    def save():be.record_json(run/'AUTO_SETUP_RESULT.json',report)
    save()
    try:
        original_mods=copy.deepcopy(mods)
        mods=snapshot_archives(mods,run)
        foundation_exclusions=[]
        foundations=foundation_plan(mods,game,allow_subset,foundation_exclusions)
        report['foundationExclusions']=foundation_exclusions
        sources,previous=baseline_sources(game,baseline,log)
        view=run/'ComposedGame';view.mkdir()
        # Hardlinks are read-only inputs. Writers produce distinct output files.
        link_copy(game/'GRB.exe',view/'GRB.exe')
        for target,source in sources.items():link_copy(source,view/Path(target).name)
        layers=[];archives={};current={k:v for k,v in baseline['hashes'].items()};authored={}
        for n,row in enumerate(foundations,1):
            log(f"Foundation {n}/{len(foundations)} [{row['phase']}]: {row['mod']} -> {Path(row['forge']).name}")
            archive=row['archive']
            if archive not in archives:archives[archive]=be.sha256(archive)
            payload=mm.extraction_target(archive,row['member'],archive_hash=archives[archive])
            if be.sha256(archive)!=archives[archive]:raise ValueError('Foundation archive changed during extraction')
            # Both parsers must accept the delivered file; do not synthesize it.
            import forge_audit
            index=forge_audit.read_index(payload)
            inventory=(inspect or be.bridge_inspect)(toolkit,payload,'AUTO',run,log)
            if inventory.get('verified') is not True or inventory.get('independentAnvilIndexVerified') is not True or len(inventory.get('nestedDataEntries',[]))!=sum(e['id'] not in ('0000000000000010','0000000000000091') for e in index['entries']):
                raise ValueError('Independent Anvil index disagrees with complete Forge: '+row['member'])
            candidate=view/Path(row['forge']).name
            digest=be.sha256(payload)
            layer=dict(row,archiveSha256=archives[archive],inputSha256=current[row['forge']],
                       outputSha256=digest,anvilVerified=True,indexEntries=len(index['entries']))
            candidate.unlink();link_copy(payload,candidate)
            current[row['forge']]=digest;layers.append(layer)
            if row.get('tier')==1 and Path(row['forge']).name.casefold()=='datapc_patch_01.forge':
                authored['TEAMMATE_Template']=(payload,layer)
        # Unlock Everything supplies a smaller stock TEAMMATE parent. Preserve
        # the Essentials author's complete parent after whole-file replacements,
        # before mapping cosmetic edits which require its individual buildtables.
        for parent,(author,origin) in authored.items():
            target=origin['forge'];candidate=view/Path(target).name
            output=run/'FoundationContents'/Path(target).name;output.parent.mkdir(exist_ok=True)
            receipt=run/'FoundationContents'/('AUTHOR_'+parent+'.json')
            dll=be.build_bridge(toolkit,log)
            command=['dotnet',str(dll),'stream-foundation-parent',str(toolkit),str(candidate),str(author),parent,str(output),str(receipt)]
            import subprocess
            with (run/'FOUNDATION_CONTENT_CONSOLE.txt').open('a',encoding='utf-8') as console:
                proc=storage_guard.run(command,stdout=console,stderr=subprocess.STDOUT)
            proof=json.loads(receipt.read_text(encoding='utf-8')) if receipt.is_file() else {}
            if proc.returncode or not proof.get('authorCompleteParentVerified') or not proof.get('independentAnvilIndexVerified'):
                raise ValueError('Essentials complete parent could not be preserved: '+str(receipt))
            digest=be.sha256(output)
            if str(proof['sourceSha256']).casefold()!=current[target] or proof['stagedForgeSha256'].casefold()!=digest:
                raise ValueError('Foundation content hash chain failed')
            layer=dict(origin,phase='FOUNDATION_CONTENT',kind='foundation_data',data=parent+'.data',
                inputSha256=current[target],outputSha256=digest,contentReceipt=str(receipt),member=origin['member']+' :: '+parent)
            layers.append(layer);current[target]=digest;candidate.unlink();link_copy(output,candidate)
            log('Preserved complete Essentials TEAMMATE_Template including individual buildtables before cosmetic mapping.')
        guided_layers=prepare_guided_additions(mods,view,game,toolkit,run,current,log)
        layers.extend(guided_layers)
        for layer in guided_layers:archives[layer['archive']]=layer['archiveSha256']
        import complete_content
        runtime_mods=copy.deepcopy(mods)
        def request_choice(path,message):
            nonlocal initial_settings
            if conflict_policy.build_settings(mm.load(mm.CONFIG,{}))!=initial_settings:
                raise ValueError('Build settings changed outside the current conflict choice; restart staging.')
            import conflict_flow
            report.update(state='WAITING_FOR_CHOICE',conflictReport=str(path));save()
            accepted=conflict_flow.resolve(path,message,choose_conflict)
            if accepted:
                initial_settings=conflict_policy.build_settings(mm.load(mm.CONFIG,{}))
                be.record_json(run/'CONFLICT_PRIORITIES.json',conflict_policy.settings(mm.load(mm.CONFIG,{})))
                report['conflictChoicesResumed']=report.get('conflictChoicesResumed',0)+1
            report['state']='COMPOSING';save()
            return accepted
        mods,content_layers=complete_content.prepare(mods,view,game,toolkit,run,current,log,baseline_sources=sources,choose_conflict=request_choice if choose_conflict else None)
        layers.extend(content_layers)
        for layer in content_layers:archives.update(layer['sourceArchives'])
        be.record_json(run/'INSTALL_ORDER.json',{'phases':list(PHASES),'layers':layers,
            'note':'Each later resource edit reads the final complete-Forge layer; live files swap once.'})
        for target,prior in previous.items():
            if prior.get('previousFoundationOnly') and not any(r['forge']==target and r['outputSha256']==prior['previousSha256'] for r in layers):
                raise ValueError('Active file is neither a recorded install nor a selected complete Forge: '+target)
        # Whole Forge payloads have already been handled. Keep other files from
        # mixed archives eligible for resource mapping.
        resources=copy.deepcopy(mods)
        import runtime_stage
        runtime_receipt,runtime_contributions=runtime_stage.prepare(runtime_mods,game,run,log)
        for key,mod in resources.items():
            mod['members']=[m for m in mod['members'] if not m.lower().endswith('.forge')]
            covered={r['member'] for r in runtime_contributions if r['mod']==key}
            mod['members']=[m for m in mod['members'] if m not in covered]
            if not mod['members']:mod['enabled']=False
        log('Mapping resource edits against prepatch + complete-Forge results...')
        mapped,details,path=auto_mapping.analyze(resources,view,toolkit,run_dir=run/'characterization',
            inspect=inspect,log=log,full_game_scan=full_game_scan)
        for layer in content_layers:
            for key,members in layer['coveredMembers'].items():
                details['entries'].extend({'archive':key,'member':member,'status':'VERIFIED_COMPOSED_CONTENT','explanation':'Complete authored container/inner composition with independent readback','compositionProof':layer['contentReceipt']} for member in members)
        present_file=run/'PRESENT_PAYLOADS.json'
        present=json.loads(present_file.read_text(encoding='utf-8')) if present_file.is_file() else []
        loose_file=run/'LOOSE_PAYLOADS.json'
        loose=json.loads(loose_file.read_text(encoding='utf-8')) if loose_file.is_file() else []
        details.setdefault('entries',[]).extend({'archive':item['mod'],'member':item['member'],'status':'VERIFIED_INSTALLED_LOOSE','explanation':'Selected archive payload already installed and checked by SHA-256'} for item in loose)
        details['entries'].extend({'archive':r['mod'],'member':r['member'],'status':'VERIFIED_STAGED_LOOSE','explanation':'Generated runtime payload independently fingerprinted in the batch'} for r in runtime_contributions)
        details.setdefault('entries',[]).extend({'archive':item['mod'],'member':item['member'],'status':'VERIFIED_COMPOSED_CONTENT','explanation':'Exact authored bytes already present in the composed foundation'} for item in present)
        excluded=mm.load(run/'PRIORITY_EXCLUSIONS.json',[])
        excluded_keys={(r['mod'],r['member']) for r in excluded}
        for row in details['entries']:
            if (row['archive'],row['member']) in excluded_keys:
                row.update(status='USER_CONFLICT_EXCLUDED',explanation='Excluded by saved resource/mod priority; see composition proof')
        be.record_json(path,details)
        report.update(characterization=str(path),verifiedRoutes=details['verifiedCount'],
                      blockingUnmapped=details['blockingUnmappedCount'],foundationFiles=len(layers))
        # Preserve library enable flags and expose direct replacement routes.
        saved=copy.deepcopy(original_mods)
        for key,item in mapped.items():
            if key=='Installer gameplay options':continue
            saved[key]['routes']=item.get('routes',[])
        for row in foundations:
            saved[row['mod']].setdefault('routes',[]).append(dict(row,sourceArchive=row['archive']))
        mm.save(mm.STATE,saved)
        if not stage:report['state']='CHARACTERIZED';save();return run,report
        if details['blockingUnmappedCount'] and not allow_subset:
            report['state']='INCOMPLETE_REVIEW_REQUIRED';save();return run,report
        inner=None;inner_path=None
        if details['verifiedCount']:
            plan=be.make_plan(mapped,view,toolkit,allow_partial=True)
            if plan['issues']:raise ValueError('Resource plan blocked: '+' | '.join(plan['issues']))
            be.record_json(run/'RESOURCE_PLAN.json',plan)
            inner=be.stage_plan_stream(plan,run/'resources',log=log,allow_route_exclusions=allow_subset,private_only=True)
            inner_path=run/'resources/BATCH_RESULT.json'
        import conflict_policy
        if initial_settings!=conflict_policy.build_settings(mm.load(mm.CONFIG,{})):
            raise ValueError('Build settings changed during staging. Run Scan and stage again with the final choices.')
        final={'schema':3,'writer':WRITER,'status':'VERIFIED_STAGED','productVersion':policy.VERSION,
               'buildSettings':initial_settings,
               'baselineId':baseline['baselineId'],'buildScope':'ENABLED_MOD_SELECTION',
               'selectedArchives':[key for key,mod in original_mods.items() if mod.get('enabled',True)],'installationOrder':list(PHASES),'foundationLayers':layers,
               'resourceManifest':str(inner_path) if inner_path else None,'runtimeManifest':runtime_receipt,
               'runtimeContributions':runtime_contributions,
               'presentPayloads':present,'installedPayloads':loose,
               'plan':{'gameRoot':str(game),'toolkit':str(toolkit),'routes':copy.deepcopy(layers),
                       'sourceHashes':{}},'outputs':{},'finishedRoutes':copy.deepcopy(layers),'excludedRoutes':[]}
        targets={r['forge'] for r in layers}
        if inner:
            def translate(row):
                result=copy.deepcopy(row);result['forge']=str(game/Path(row['forge']).name);return result
            final['plan']['routes'].extend(translate(r) for r in inner['plan']['routes'])
            final['finishedRoutes'].extend(translate(r) for r in inner['finishedRoutes'])
            final['excludedRoutes']=[translate(r) for r in inner.get('excludedRoutes',[])]
            targets.update(str(game/Path(k).name) for k in inner['outputs'])
        # A removed archive must not leave behind an older whole-Forge mod.
        # The private view already starts from verified originals, so include
        # previously modified destinations even when no selected mod edits them.
        final['baselineRestores']=sorted(set(previous)-targets)
        for target in final['baselineRestores']:
            log('Removed-mod cleanup: stage verified original '+Path(target).name)
        targets.update(previous)
        for target in sorted(targets):
            virtual=str(view/Path(target).name)
            entry=inner['outputs'].get(virtual) if inner else None
            staged=Path(entry['staged']) if entry else Path(virtual)
            final['outputs'][target]={'relative':Path(target).name,'sourceSha256':baseline['hashes'][target],
                'staged':str(staged),'stagedSha256':be.sha256(staged),**previous.get(target,{})}
            final['plan']['sourceHashes'][target]=baseline['hashes'][target]
        # Archive fingerprints travel with every direct route for compatibility checks.
        for rows in (final['plan']['routes'],final['finishedRoutes']):
            for row in rows:
                if row.get('kind') in ('forge','foundation_data','guided_inner'):row['archiveSha256']=archives[row['archive']]
        manifest=run/'BATCH_RESULT.json';be.record_json(manifest,final)
        import mod_coverage
        coverage=mod_coverage.write(details,final,run)
        report.update(modCoverage=str(run/'MOD_COVERAGE.json'),incompleteMods=coverage['incompleteMods'])
        summary=inspect_stage(manifest,game)
        report.update(state='STAGED_VERIFIED',batch=str(manifest),stagedRoutes=len(final['finishedRoutes']),
                      stagedForges=len(final['outputs']),identityExcludedRoutes=len(final['excludedRoutes']))
        log(f"Layered build verified: {len(layers)} complete Forge steps then {summary['resourceEdits']} resource edits. No live changes.")
        save()
        if install:
            import existing_stage
            _,tx=existing_stage.install_existing(manifest,backups,expected_game=game,game_check=game_check)
            import recovery_snapshot
            tx=recovery_snapshot.create_layered(manifest,tx,backups)
            cfg=mm.load(mm.CONFIG,{});cfg['last_transaction']=str(tx);mm.save(mm.CONFIG,cfg)
            report.update(state='INSTALLED_VERIFIED_BYTES',transaction=str(tx));save()
        return run,report
    except Exception as exc:
        from conflict_flow import StagingCancelled
        report['state']='CANCELLED' if isinstance(exc,StagingCancelled) else 'FAILED';report['errors'].append(str(exc));save();raise


def inspect_stage(manifest,expected_game=None,verify_backups=False):
    """Verify order, coverage and exact hash continuity before any live swap."""
    import existing_stage
    path=Path(manifest).resolve();state=json.loads(path.read_text(encoding='utf-8'))
    game=Path(state['plan']['gameRoot']).resolve()
    if state.get('runtimeManifest'):
        import runtime_stage
        runtime_stage.inspect(state['runtimeManifest'],game,path.parent)
    if expected_game is not None and game!=Path(expected_game).resolve():raise ValueError('Wrong game for layered stage')
    if state.get('writer')!=WRITER or state.get('status')!='VERIFIED_STAGED' or state.get('installationOrder')!=list(PHASES):
        raise ValueError('Invalid layered stage/order')
    if not (game/'GRB.exe').is_file():raise ValueError('Missing game executable')
    layers=state.get('foundationLayers',[]);phase=(-1,-1);seen=set();hashes=dict(state['plan']['sourceHashes'])
    expected_routes=[]
    for layer in layers:
        step=PHASES.index(layer['phase'])
        target=layer['forge'];key=(step,layer.get('tier',0),target.casefold())
        rank=(step,layer.get('tier',0))
        if step>3 or rank<phase or key in seen:raise ValueError('Foundation order conflict or duplicate target')
        if Path(target).parent!=game or Path(target).suffix.lower()!='.forge':raise ValueError('Unsafe foundation destination')
        if layer.get('inputSha256')!=hashes.get(target) or layer.get('anvilVerified') is not True or layer.get('indexEntries',0)<=0:
            raise ValueError('Broken foundation hash chain: '+target)
        hashes[target]=layer['outputSha256'];seen.add(key);phase=rank
        if step==2:
            receipt=Path(layer.get('contentReceipt','')).resolve()
            if not be.within(path.parent,receipt):raise ValueError('Foundation content proof escaped workspace')
            proof=json.loads(receipt.read_text(encoding='utf-8'))
            if any(proof.get(k) is not True for k in existing_stage.REQUIRED_RECEIPT_FLAGS) or proof.get('authorCompleteParentVerified') is not True or proof.get('authorParent')!='TEAMMATE_Template':
                raise ValueError('Missing complete authored-parent verification')
            if proof['sourceSha256'].casefold()!=layer['inputSha256'] or proof['stagedForgeSha256'].casefold()!=layer['outputSha256']:
                raise ValueError('Foundation content proof hash mismatch')
        if step==3:
            receipt=Path(layer.get('contentReceipt','')).resolve()
            if not be.within(path.parent,receipt):raise ValueError('Guided insertion proof escaped workspace')
            proof=json.loads(receipt.read_text(encoding='utf-8'))
            outer=proof.get('outerStreamReceipt',{})
            composed=layer.get('kind') in ('composed_assets','composed_inner')
            if proof.get('verified') is not True or proof.get('sourceUnchanged') is not True or proof.get('independentAnvilIndexVerified') is not True or (not composed and (proof.get('parent')!=layer['data'] or proof.get('addedEntries')!=len(layer.get('addedMembers',[])))):
                raise ValueError('Incomplete guided resource insertion proof')
            if composed:
                if not layer.get('coveredMembers') or not layer.get('sourceArchives'):raise ValueError('Composed content provenance missing')
                if layer['kind']=='composed_inner' and (proof.get('completeNestedReadbackVerified') is not True or proof.get('allUnselectedOriginalObjectsPreserved') is not True):raise ValueError('Complete inner preservation/readback proof missing')
                for archive,digest in layer['sourceArchives'].items():
                    if not be.within(path.parent,archive) or not Path(archive).is_file():raise ValueError('Composed source archive outside snapshot')
                    if verify_backups and be.sha256(archive)!=digest:raise ValueError('Composed source archive changed')
            elif any(outer.get(k) is not True for k in ('verified','originalSourceUnchanged','prefetchBytesPreserved','untouchedBytesVerified','independentAnvilIndexVerified')):
                raise ValueError('Guided insertion did not preserve the source Forge')
            if proof['sourceSha256'].casefold()!=layer['inputSha256'] or proof['outputSha256'].casefold()!=layer['outputSha256']:
                raise ValueError('Guided insertion hash chain mismatch')
        expected_routes.append((layer['mod'],layer['member'],target,layer.get('kind','forge')))
    inner_path=state.get('resourceManifest');resource_count=0
    if inner_path:
        inner_path=Path(inner_path).resolve()
        if not be.within(path.parent,inner_path):raise ValueError('Resource manifest escaped layered workspace')
        summary=existing_stage.inspect_existing_stage(inner_path)
        inner=json.loads(inner_path.read_text(encoding='utf-8'))
        if inner.get('writer')!='FORGE_STREAM_BATCH_V27' or not inner.get('privateOnly'):
            raise ValueError('Layered resources must come from a private stream stage')
        if Path(inner['plan']['gameRoot']).resolve()!=path.parent/'ComposedGame':raise ValueError('Resource stage used the wrong composed sources')
        for source,entry in inner['outputs'].items():
            target=str(game/Path(source).name)
            if entry['sourceSha256']!=hashes.get(target):raise ValueError('Resources were packed against an earlier Forge layer: '+target)
            proof=json.loads(Path(entry['streamReceipt']).read_text(encoding='utf-8'))
            if str(proof.get('sourceSha256','')).casefold()!=entry['sourceSha256'].casefold():
                raise ValueError('Resource proof does not certify the composed source hash: '+target)
            hashes[target]=entry['stagedSha256']
            if state['outputs'][target]['staged']!=entry['staged'] and not any(r['forge']==target for r in state.get('postLayers',[])):raise ValueError('Final build omits resource output')
        for r in inner['finishedRoutes']:
            expected_routes.append((r['mod'],r['member'],str(game/Path(r['forge']).name),r.get('kind','inner')))
        resource_count=summary['acceptedEdits']
        if len(state.get('excludedRoutes',[]))!=summary['excludedEdits']:raise ValueError('Missing resource exclusions')
    for layer in state.get('postLayers',[]):
        target=layer['forge'];receipt=Path(layer.get('contentReceipt','')).resolve()
        if layer.get('phase')!='FINAL_TWEAKS' or layer.get('kind')!='composed_inner' or target not in hashes or not be.within(path.parent,receipt):raise ValueError('Invalid final tweak layer')
        proof=json.loads(receipt.read_text(encoding='utf-8'))
        flags=('verified','sourceUnchanged','independentAnvilIndexVerified','completeNestedReadbackVerified','allUnselectedOriginalObjectsPreserved')
        if any(proof.get(k) is not True for k in flags) or not layer.get('coveredMembers'):raise ValueError('Final tweak readback/preservation proof missing')
        if layer['inputSha256']!=hashes[target] or proof['sourceSha256'].casefold()!=hashes[target] or proof['outputSha256'].casefold()!=layer['outputSha256']:raise ValueError('Broken final tweak hash chain')
        if not layer.get('sourceArchives'):raise ValueError('Final tweak provenance missing')
        for archive,digest in layer['sourceArchives'].items():
            if not be.within(path.parent,archive) or not Path(archive).is_file():raise ValueError('Final tweak archive outside snapshot')
            if verify_backups and be.sha256(archive)!=digest:raise ValueError('Final tweak archive changed')
        hashes[target]=layer['outputSha256'];resource_count+=sum(map(len,layer['coveredMembers'].values()))
        expected_routes.append((layer['mod'],layer['member'],target,layer['kind']))
    actual=[(r['mod'],r['member'],r['forge'],r.get('kind','inner')) for r in state['finishedRoutes']]
    if collections.Counter(actual)!=collections.Counter(expected_routes) or not actual:
        raise ValueError('Layered mod coverage changed or incomplete')
    if len(state['plan']['routes'])!=len(actual)+len(state.get('excludedRoutes',[])):raise ValueError('Invalid layered plan accounting')
    targets={r[2] for r in expected_routes}
    restores=state.get('baselineRestores',[])
    if not isinstance(restores,list) or len(restores)!=len(set(restores)) or targets.intersection(restores):
        raise ValueError('Invalid removed-mod baseline restoration coverage')
    if restores:
        baseline=policy.require_ready(game)
        for target in restores:
            entry=state['outputs'].get(target,{})
            original=baseline['hashes'].get(target)
            receipt=Path(entry.get('previousTransaction','')).resolve()
            if not original or entry.get('sourceSha256')!=original or entry.get('stagedSha256')!=original or not entry.get('previousSha256') or not be.within(policy.WORK.resolve(),receipt):
                raise ValueError('Removed-mod restoration lacks a verified original and install history')
        targets.update(restores)
    if set(state['outputs'])!=targets or set(state['plan']['sourceHashes'])!=targets:raise ValueError('Incomplete final Forge coverage')
    for target,entry in state['outputs'].items():
        staged=Path(entry['staged']).resolve()
        if Path(target).parent!=game or entry.get('relative')!=Path(target).name or not staged.is_file() or not be.within(path.parent,staged):
            raise ValueError('Unsafe layered output path')
        if entry['stagedSha256']!=hashes[target] or entry['sourceSha256']!=state['plan']['sourceHashes'][target]:
            raise ValueError('Final Forge does not include the last planned layer')
        if entry.get('previousSha256'):
            txfile=Path(entry.get('previousTransaction','')).resolve()
            tx=json.loads(txfile.read_text(encoding='utf-8'))
            previous=[r for r in tx.get('files',[]) if r['target']==target and r['originalSha256']==entry['sourceSha256'] and
                      (r['installedSha256']==entry['previousSha256'] or entry.get('previousFoundationOnly') or entry.get('previousDiagnosticTransaction'))]
            if len(previous)!=1 or Path(tx['gameRoot']).resolve()!=game:raise ValueError('Unrecognized previous installed build')
            if entry.get('previousDiagnosticTransaction') and not verified_diagnostic(entry['previousDiagnosticTransaction'],game,target,entry['previousSha256'],verify_files=verify_backups):raise ValueError('Previous diagnostic proof or backup changed')
            if entry.get('previousFoundationOnly'):
                matches=[r for r in layers if r['forge']==target and r['outputSha256']==entry['previousSha256']]
                if not matches:raise ValueError('Active bytes are not a selected complete Forge')
                if verify_backups:
                    for layer in matches:
                        if be.sha256(layer['archive'])!=layer['archiveSha256']:raise ValueError('Foundation archive changed')
                        payload=mm.extraction_target(layer['archive'],layer['member'],archive_hash=layer['archiveSha256'])
                        if be.sha256(payload)!=entry['previousSha256']:raise ValueError('Selected complete Forge changed')
            backup=Path(previous[0]['backup']).resolve()
            if not be.within(txfile.parent,backup) or not backup.is_file():raise ValueError('Previous baseline backup missing')
            if verify_backups and be.sha256(backup)!=entry['sourceSha256']:raise ValueError('Previous baseline backup changed')
    for item in state.get('presentPayloads',[]):
        target=item['forge']
        if target not in state['outputs'] or item.get('id')!='0000000000000010' or item.get('type')!=0:raise ValueError('Unrecognized already-present payload')
        archive=Path(item['archive']).resolve()
        if not be.within(path.parent,archive) or not archive.is_file():raise ValueError('Already-present payload archive outside snapshot')
        if verify_backups:
            if be.sha256(archive)!=item['archiveSha256']:raise ValueError('Already-present payload archive changed')
            payload=mm.extraction_target(archive,item['member'],archive_hash=item['archiveSha256'])
            if be.sha256(payload)!=item['payloadSha256']:raise ValueError('Already-present payload fingerprint differs')
        import forge_audit,hashlib
        staged=Path(state['outputs'][target]['staged']);index=forge_audit.read_index(staged)
        entries=[e for e in index['entries'] if e['id']==item['id'] and e['type']==item['type']]
        if len(entries)!=1:raise ValueError('Already-present payload identity missing')
        entry=entries[0]
        with staged.open('rb') as f:f.seek(entry['offset']);digest=hashlib.sha256(f.read(entry['length'])).hexdigest()
        if digest!=item['payloadSha256']:raise ValueError('Final stage omits the supplied metafile bytes')
    for item in state.get('installedPayloads',[]):
        target=Path(item['target']).resolve();archive=Path(item['archive']).resolve()
        if not be.within(game,target) or target.suffix.lower() in ('.forge','.data') or not be.within(path.parent,archive):raise ValueError('Installed loose payload provenance/path differs')
        if not target.is_file() or be.sha256(target)!=item['payloadSha256']:raise ValueError('Separate installed payload changed: '+str(target))
        if verify_backups and be.sha256(archive)!=item['archiveSha256']:raise ValueError('Separate payload archive changed')
    return {'manifest':str(path),'gameRoot':str(game),'acceptedEdits':len(actual),'resourceEdits':resource_count,
            'foundationSteps':len(layers),'baselineRestores':len(restores),'excludedEdits':len(state.get('excludedRoutes',[])),
            'forges':[{'forge':Path(k).name,'stagedBytes':Path(v['staged']).stat().st_size} for k,v in state['outputs'].items()]}


def prepare_guided_additions(mods,view,game,toolkit,run,current,log):
    """Known manual-guide additions to a confirmed complete WorldMap parent.

    Never invents an outer parent. The existing native trial verifies new binary
    IDs, preserves every old object and independently unpacks the final parent.
    """
    import subprocess
    result=[]
    for key,mod in mods.items():
        name=Path(mod['archive']).name.casefold()
        if not mod.get('enabled',True) or not ('nightmare' in name and '-1102-' in name):continue
        members=[m for m in mod['members'] if m.casefold().endswith('.wavespawner')]
        if not members:continue
        source=view/'DataPC_TGT_WorldMap_Bootstrap_Split_patch_01.forge'
        inventory=be.bridge_inspect(toolkit,source,'AUTO',run,log)
        parents=[n for n in inventory['nestedDataEntries'] if be.object_stem(n)=='tgt_worldmap']
        if len(parents)!=1:raise ValueError('Nightmare guide requires one confirmed TGT_WorldMap parent')
        parent=parents[0];nested=be.bridge_inspect(toolkit,source,parent,run,log)
        existing={be.object_stem(n) for n in nested['nestedObjects']}
        missing=[m for m in members if be.object_stem(m) not in existing]
        if not missing:continue
        folder=run/'GuidedContents';folder.mkdir(exist_ok=True)
        digest=be.sha256(mod['archive'])
        request=folder/'NIGHTMARE_REQUEST.json';receipt=folder/'NIGHTMARE_PROOF.json';output=folder/source.name
        req=[{'name':Path(m.replace('\\','/')).name,'payload':str(mm.extraction_target(mod['archive'],m,archive_hash=digest))} for m in missing]
        be.record_json(request,req)
        dll=be.build_bridge(toolkit,log)
        command=['dotnet',str(dll),'stream-add-inner-batch',str(toolkit),str(source),parent,str(request),str(receipt),str(output)]
        with (folder/'CONSOLE.txt').open('w',encoding='utf-8') as console:
            proc=storage_guard.run(command,stdout=console,stderr=subprocess.STDOUT)
        proof=json.loads(receipt.read_text(encoding='utf-8')) if receipt.is_file() else {}
        if proc.returncode or proof.get('verified') is not True or proof.get('addedEntries')!=len(missing):
            raise ValueError('Nightmare new WaveSpawner assets did not pass the complete-parent insertion trial: '+str(receipt))
        target=str(game/source.name);hash_out=be.sha256(output)
        if proof['sourceSha256'].casefold()!=current[target] or proof['outputSha256'].casefold()!=hash_out:
            raise ValueError('Guided Nightmare addition source/output changed')
        layer={'mod':key,'archive':mod['archive'],'archiveSha256':digest,'forge':target,
            'kind':'guided_inner','phase':'GUIDED_CONTENT','tier':0,'data':parent,'object':'',
            'member':'GUIDED_NEW_WAVESPAWNERS','addedMembers':missing,'contentReceipt':str(receipt),
            'inputSha256':current[target],'outputSha256':hash_out,'anvilVerified':True,
            'indexEntries':len(inventory['nestedDataEntries'])}
        result.append(layer);current[target]=hash_out;source.unlink();link_copy(output,source)
        log(f'Included {len(missing)} new Nightmare WaveSpawner resources after full parent and Forge readback checks.')
    return result
