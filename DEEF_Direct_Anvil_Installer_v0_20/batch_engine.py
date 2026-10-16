"""DEEF 0.16 verified multi-route pipeline with opt-in transactional deployment.

Only explicitly mapped, ZIP/7z/RAR-contained inner-object or whole .data
replacements can be applied. New Forge entries and complete Forge swaps
are deliberately not guessed.
No third-party Anvil code executes from this Python module except through the
existing v0.15.3 independently verified C# stage/inspect bridge.
"""
import storage_guard
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def record_json(path, item):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.pending')
    tmp.write_text(json.dumps(item, indent=2, sort_keys=True), encoding='utf-8')
    os.replace(tmp, path)


def within(parent, child):
    a, b = Path(parent).resolve(), Path(child).resolve()
    try:
        b.relative_to(a)
        return True
    except ValueError:
        return False


def logical(name):
    x = Path(name).name
    prefix, sep, remainder = x.partition('_-_')
    return remainder if sep and prefix.isdecimal() else x


def object_stem(name):
    return logical(name).rsplit('.', 1)[0].casefold()


def select_unique(candidates, wanted, label):
    candidates = [str(x) for x in candidates]
    # Ordinals change when Forge/.data files are rebuilt. Reject duplicate
    # logical names even if the old ordinal happens to match one of them.
    if label == 'data':
        match = [x for x in candidates if logical(x).casefold() == logical(wanted).casefold()]
    else:
        match = [x for x in candidates if object_stem(x) == object_stem(wanted)]
    if len(match) != 1:
        raise ValueError('Cannot resolve unique {} mapping {} ({} logical candidates)'.format(label, wanted, len(match)))
    return match[0]


def game_processes_running():
    if os.name != 'nt':
        raise RuntimeError('Live installation is only supported on Windows.')
    try:
        p = storage_guard.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True,
                           text=True, timeout=15)
        if p.returncode:
            raise RuntimeError('tasklist returned error')
        names = {row[0].strip().casefold() for row in csv.reader(p.stdout.splitlines()) if row}
        return sorted(names & {'grb.exe', 'grb_vulkan.exe', 'grb_upp.exe', 'grb_upp_vulkan.exe',
                              'grb_be.exe', 'ghostreconbreakpoint.exe'})
    except Exception as e:
        raise RuntimeError('Cannot verify whether the game is running. Installation blocked: ' + str(e))


def import_route_manifest(path, mods, game_root):
    """Import explicit, reviewable routes by indexed archive key; no guessed targets."""
    import copy
    manifest=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(manifest,dict) or not isinstance(manifest.get('routes'),list):
        raise ValueError('Expected JSON object with routes list')
    if len(manifest['routes'])>10000:
        raise ValueError('Route manifest exceeds 10,000 entries')
    root=Path(game_root).resolve()
    result=copy.deepcopy(mods)
    n=0
    for item in manifest['routes']:
        if not isinstance(item,dict):raise ValueError('Every mapping must be an object')
        key=item.get('archive')
        if key not in result:
            match=[m for m in result if Path(m).name.casefold()==str(key).casefold()]
            if len(match)!=1:raise ValueError('Archive mapping is missing or ambiguous: '+str(key))
            key=match[0]
        mod=result[key]
        if mod.get('status')!='INDEXED':raise ValueError('Archive could not be indexed: '+key)
        member=item.get('member','')
        if member not in mod['members']:raise ValueError('Archive member missing: '+key+' :: '+str(member))
        forge=Path(item.get('forge',''))
        if not forge.is_absolute():forge=root/forge
        forge=forge.resolve()
        # Direct game-root files only: never stage or deploy Backups\*.forge.
        if forge.parent!=root or forge.suffix.lower()!='.forge' or not forge.is_file():
            raise ValueError('Route must target an active Forge in the game root, not a backup: '+str(forge))
        data=item.get('data','')
        if not isinstance(data,str) or not data.lower().endswith('.data') or any(x in data for x in ('/','\\','..')):
            raise ValueError('Data selector must be one .data filename')
        kind=item.get('kind','inner')
        if kind not in ('inner','data'):raise ValueError('Unsupported route kind')
        obj=item.get('object','')
        if kind=='inner' and (not isinstance(obj,str) or any(x in obj for x in ('/','\\','..')) or not obj):
            raise ValueError('Inner route requires one nested object filename')
        if kind=='data' and not member.lower().endswith('.data'):
            raise ValueError('Complete .data route requires a .data member')
        route={'kind':kind,'forge':str(forge),'data':data,
               'object':obj if kind=='inner' else '', 'member':member,
               'sourceArchive':mod['archive']}
        existing=mod.setdefault('routes',[])
        if not any(all(prev.get(k, 'inner' if k=='kind' else '')==route[k]
                       for k in ('kind','forge','data','object','member','sourceArchive'))
                   for prev in existing):
            existing.append(route)
            n+=1
    return result,n


def make_plan(mods, game_root, toolkit, allow_partial=False):
    root = Path(game_root).resolve()
    toolkit = Path(toolkit).resolve()
    if not root.is_dir() or not (root/'GRB.exe').is_file():
        raise ValueError('Select the Ghost Recon Breakpoint folder containing GRB.exe')
    if not (toolkit/'AnvilToolkit.dll').is_file() or not (toolkit/'Libs').is_dir():
        raise ValueError('Select the AnvilToolkit directory containing DLL and Libs')
    routes, skipped, issues = [], [], []
    archive_hashes = {}
    for mod, info in sorted(mods.items()):
        if not info.get('enabled', True):
            skipped.append({'mod': mod, 'reason': 'DISABLED'})
            continue
        if info.get('status') != 'INDEXED':
            issues.append('{} cannot be indexed: {}'.format(mod, info.get('status')))
            continue
        items = info.get('routes', [])
        if not items:
            skipped.append({'mod': mod, 'reason': 'NO_EXPLICIT_RESOURCE_MAPPINGS'})
            continue
        source_archive = Path(info.get('archive', '')).resolve()
        if not source_archive.is_file():
            issues.append('Missing archive for ' + mod)
            continue
        for item in items:
            try:
                game_forge = Path(item['forge']).resolve()
                if (game_forge.parent != root or not game_forge.is_file()
                    or game_forge.suffix.lower() != '.forge'):
                    raise ValueError('Mapped Forge must be an active .forge directly in game root (not Backups)')
                data, inner, member = item['data'], item.get('object',''), item['member']
                kind=item.get('kind','inner')
                if kind not in ('inner','data'):raise ValueError('Unsupported route kind')
                if not data.endswith('.data') or any(x in data for x in ('/', '\\', '..')):
                    raise ValueError('Invalid .data filename')
                if kind == 'inner' and (not inner or any(x in inner for x in ('/', '\\', '..'))):
                    raise ValueError('Invalid nested object selector')
                if kind == 'data' and not member.lower().endswith('.data'):
                    raise ValueError('Whole-resource replacement must reference a .data member')
                if member not in info['members']:
                    raise ValueError('Archive member no longer exists; rescan')
                if item.get('sourceArchive') and Path(item['sourceArchive']).resolve() != source_archive:
                    raise ValueError('Archive moved or changed; remap this route')
                archive_key=str(source_archive)
                if archive_key not in archive_hashes:
                    archive_hashes[archive_key]=sha256(source_archive)
                routes.append({'mod': mod, 'archive': archive_key, 'forge': str(game_forge),
                               'data': data, 'object': inner, 'kind':kind, 'member': member,
                               'archiveSha256': archive_hashes[archive_key]})
            except (ValueError, KeyError, OSError) as e:
                issues.append('{}: {}'.format(mod, e))
    # Compare original object-target name to protect against original ordinal changes.
    keys = set()
    for r in routes:
        k = (r['forge'].casefold(), logical(r['data']).casefold(),
             object_stem(r['object']) if r['kind']=='inner' else '*WHOLE_DATA*')
        if k in keys:
            issues.append('Two enabled mods target the same object: {} in {}'.format(r['object'], r['forge']))
        keys.add(k)
    for r in routes:
        if r['kind']=='data' and any(x is not r and x['forge'].casefold()==r['forge'].casefold()
                and logical(x['data']).casefold()==logical(r['data']).casefold() for x in routes):
            issues.append('Whole .data replacement conflicts with another route in same container: '+r['data'])
    if not routes:
        issues.append('No eligible mapped replacements exist')
    unmapped = [s for s in skipped if s['reason'] == 'NO_EXPLICIT_RESOURCE_MAPPINGS']
    if unmapped and not allow_partial:
        issues.append('{} enabled mods have no verified object mappings. Choose subset mode to stage only mapped mods.'.format(len(unmapped)))
    sources = {}
    for r in routes:
        if r['forge'] not in sources:
            sources[r['forge']] = sha256(r['forge'])
    return {'schema': 1, 'gameRoot': str(root), 'toolkit': str(toolkit), 'routes': routes,
            'unmappedOrDisabled': skipped, 'issues': issues, 'sourceHashes': sources,
            'allowPartial': bool(allow_partial), 'completeModlist': not bool(unmapped),
            'createdUtc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}


def bridge_inspect(toolkit, forge, data, work, log):
    import mod_manager as manager
    dll = manager.ROOT/'bin'/'DEEF.AnvilBridgeProbe.dll'
    if not dll.is_file():
        p = storage_guard.run(['dotnet', 'build', str(manager.ROOT/'src'/'AnvilBridgeProbe.csproj'),
                            '-c', 'Release', '-o', str(manager.ROOT/'bin'), '--nologo', '-p:AnvilToolkitDirectory='+str(Path(toolkit).resolve())],
                           text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        log(p.stdout)
        if p.returncode or not dll.is_file():
            raise RuntimeError('Anvil bridge build failed')
    report = Path(work)/('inspect_'+uuid.uuid4().hex[:10]+'.json')
    # Large forges are indexed from their entry tables in place. No payload
    # extraction, Forge copy, or modified output is produced by this path.
    large = Path(forge).stat().st_size > 128 * 1024 * 1024
    command = ['dotnet', str(dll), 'stream-inspect', str(toolkit), str(forge), str(report), str(data)]
    # Save diagnostics for every inspection; Python only parses the JSON after exit.
    with open(Path(work)/'INSPECTIONS.txt', 'a', encoding='utf-8') as f:
        p = storage_guard.run(command, stdout=f, stderr=subprocess.STDOUT)
    result = json.loads(report.read_text(encoding='utf-8')) if report.is_file() else {}
    if p.returncode or not result.get('verified'):
        raise RuntimeError('Inspection failed for {}: {} (see {})'.format(data, result.get('error'), report))
    return result


def stage_plan_legacy(plan, batch_dir, log=print, inspect_func=None, stage_func=None):
    """Stage enabled mapped mods sequentially; installed game always remains read-only."""
    if plan.get('issues'):
        raise ValueError('Cannot stage invalid plan:\n'+'\n'.join(plan['issues']))
    import mod_manager as manager
    batch_dir = Path(batch_dir).resolve()
    game = Path(plan['gameRoot']).resolve()
    if within(game, batch_dir) or within(plan['toolkit'], batch_dir):
        raise ValueError('Batch workspace cannot be inside the game or toolkit')
    batch_dir.mkdir(parents=True, exist_ok=False)
    state = {'schema':1, 'status':'RUNNING', 'plan':plan, 'finishedRoutes':[], 'outputs':{},
             'createdUtc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
    manifest_path = batch_dir/'BATCH_RESULT.json'
    record_json(manifest_path,state)
    latest = {str(Path(p).resolve()):Path(p).resolve() for p in plan['sourceHashes']}
    inspect_func = inspect_func or bridge_inspect
    stage_func = stage_func or manager.execute_bridge
    try:
        for index, item in enumerate(plan['routes'],1):
            game_forge = str(Path(item['forge']).resolve())
            source = latest[game_forge]
            log('Route {}/{}: {} -> {}\n'.format(index,len(plan['routes']),item['mod'],Path(source).name))
            if sha256(item['archive']) != item['archiveSha256']:
                raise RuntimeError('Mod archive changed since plan creation: '+item['archive'])
            inventory = inspect_func(plan['toolkit'],source,'AUTO',batch_dir,log)
            data = select_unique(inventory['nestedDataEntries'], item['data'],'data')
            if item.get('kind') == 'data':
                inner = ''
            else:
                nested = inspect_func(plan['toolkit'],source,data,batch_dir,log)
                inner = select_unique(nested['nestedObjects'],item['object'],'object')
            payload = manager.extraction_target(item['archive'],item['member'])
            if within(game, payload) or not payload.is_file():
                raise RuntimeError('Unsafe or missing extracted payload')
            result, run = stage_func('stage-data' if item.get('kind')=='data' else 'stage',
                plan['toolkit'],str(source),data,inner,str(payload),on_line=log)
            verified = Path(result['stagedForge']).resolve()
            if (not result.get('verified') or result.get('originalSourceUnchanged') is not True
                or not (result.get('finalDataBinaryMatchesModPayload') is True if item.get('kind')=='data'
                        else result.get('finalEditedPayloadVerified') is True)):
                raise RuntimeError('Bridge returned an incomplete/failed verification report')
            if not verified.is_file() or sha256(verified) != result['stagedForgeSha256']:
                raise RuntimeError('Verified stage output unavailable or hash mismatch')
            latest[game_forge] = verified
            state['finishedRoutes'].append({'mod':item['mod'],'data':data,'object':inner,
                'payloadSha256':sha256(payload),'run':str(run),'stagedSha256':sha256(verified)})
            record_json(manifest_path,state)
        outputs_dir = batch_dir/'VerifiedForges'; outputs_dir.mkdir()
        for game_forge, source in latest.items():
            if str(source) == game_forge:
                continue
            # Preserve any subdirectory structure inside the game root.
            rel = Path(game_forge).relative_to(game)
            dest = outputs_dir/rel
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,dest)
            if sha256(dest) != sha256(source):
                raise IOError('Final staged Forge copy failed SHA-256 comparison')
            state['outputs'][str(Path(game_forge).resolve())] = {
                'sourceSha256':plan['sourceHashes'][game_forge],
                'staged':str(dest.resolve()),'stagedSha256':sha256(dest), 'relative':rel.as_posix()}
        if not state['outputs']:
            raise ValueError('Staging produced no files')
        # Original game has been read-only for the entire operation.
        for source, original_sha in plan['sourceHashes'].items():
            if sha256(source) != original_sha:
                raise RuntimeError('Game file was changed externally during staging: '+source)
        state['status'] = 'VERIFIED_STAGED'
        record_json(manifest_path,state)
        log('BATCH STAGING PASS. Verified {} mapped replacements into {} output Forges.\n'.format(
            len(state['finishedRoutes']),len(state['outputs'])))
        return state
    except Exception as ex:
        state['status']='FAILED';state['error']=str(ex);record_json(manifest_path,state)
        raise


def stage_plan(plan, batch_dir, log=print, inspect_func=None, stage_func=None, allow_route_exclusions=False):
    """One verified streaming rebuild per Forge; mocked legacy path for old fixture tests.

    Never install from an index match alone: the bridge must independently unpack,
    replace and verify every nested object and preserve all unmodified Forge bytes.
    """
    if inspect_func is not None or stage_func is not None:
        return stage_plan_legacy(plan, batch_dir, log=log, inspect_func=inspect_func, stage_func=stage_func)
    return stage_plan_stream(plan, batch_dir, log=log, allow_route_exclusions=allow_route_exclusions)


def build_bridge(toolkit, log=print):
    """Compile the actual sources (never use a stale binary from a prior version)."""
    import mod_manager as manager
    dll=manager.ROOT/'bin'/'DEEF.AnvilBridgeProbe.dll'
    cmd=['dotnet','build',str(manager.ROOT/'src'/'AnvilBridgeProbe.csproj'),
         '-c','Release','-o',str(manager.ROOT/'bin'),'--nologo','-p:AnvilToolkitDirectory='+str(Path(toolkit).resolve())]
    proc=storage_guard.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, timeout=240)
    log(proc.stdout)
    if proc.returncode or not dll.is_file():
        raise RuntimeError('C# bridge compilation failed; installation blocked')
    return dll


def _space_check(path, needed, purpose):
    """Fail before generating a large partial copy; workspaces must preexist."""
    free=shutil.disk_usage(path).free
    if free < needed:
        raise RuntimeError('{} requires {:,.1f} GiB free at {}; only {:,.1f} GiB available'.format(
            purpose,needed/2**30,path,free/2**30))


def stage_plan_stream(plan, batch_dir, log=print, run_bridge=None,
                      allow_route_exclusions=False, run_preflight=None, private_only=False):
    """Stream a verified subset only after a separate, read-only binary-ID preflight.

    A characterized filename alone does not authorize replacing a binary resource.
    Rejected edits are enumerated in IDENTITY_EXCLUSIONS.json and cannot be deployed.
    Preflight happens BEFORE any multi-gigabyte Forge copying. No installed game
    file is written by staging.
    """
    import mod_manager as manager
    from collections import defaultdict
    if plan.get('issues'):
        raise ValueError('Cannot stage invalid plan: '+' | '.join(plan['issues']))
    batch_dir=Path(batch_dir).resolve()
    game=Path(plan['gameRoot']).resolve()
    toolkit=Path(plan['toolkit']).resolve()
    if within(game,batch_dir) or within(toolkit,batch_dir):
        raise ValueError('Batch workspace must be outside the game and toolkit')
    if not plan.get('routes') or not plan.get('sourceHashes'):
        raise ValueError('No verified routes to stage')
    if batch_dir.exists():
        raise FileExistsError('Batch output directory already exists: '+str(batch_dir))
    batch_dir.mkdir(parents=True)
    manifest=batch_dir/'BATCH_RESULT.json'
    state={'schema':3,'status':'RUNNING','writer':'FORGE_STREAM_BATCH_V27',
           'plan':plan,'finishedRoutes':[],'excludedRoutes':[], 'outputs':{},
           'createdUtc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
    import deployment_policy
    if private_only:
        state['privateOnly']=True
    else:
        deployment_policy.stamp(state)
    record_json(manifest,state)
    try:
        by_forge=defaultdict(list)
        for item in plan['routes']:
            forge=str(Path(item['forge']).resolve())
            if Path(forge).parent!=game or not forge.lower().endswith('.forge'):
                raise RuntimeError('Unsafe active Forge path '+forge)
            if forge not in plan['sourceHashes']:
                raise RuntimeError('Missing original Forge fingerprint')
            by_forge[forge].append(item)
        originals=list(by_forge)
        for forge in originals:
            if sha256(forge)!=plan['sourceHashes'][forge]:
                raise RuntimeError('Installed Forge changed after planning: '+forge)
        runner=run_bridge
        dll=None if runner else build_bridge(toolkit,log)

        # Preflight all selected game archives before creating any staged .forge
        # copies. Also catches incorrect names/headers in otherwise 'mapped' mods.
        accepted_by_forge={}
        for number,(forge,items) in enumerate(sorted(by_forge.items(),key=lambda x:Path(x[0]).stat().st_size),1):
            label=Path(forge).name
            log('Binary identity preflight {}/{}: {} ({} edits)\n'.format(
                number,len(by_forge),label,len(items)))
            unique_archives={r['archive']:r['archiveSha256'] for r in items}
            for archive,expected in unique_archives.items():
                if sha256(archive)!=expected:
                    raise RuntimeError('Mod archive changed since mapping: '+archive)
            req=[]
            for item in items:
                payload=manager.extraction_target(item['archive'],item['member'],
                                                  archive_hash=item['archiveSha256'])
                if not payload.is_file() or within(game,payload):
                    raise RuntimeError('Unsafe or missing mod payload: '+str(payload))
                req.append({'kind':item.get('kind','inner'),'data':item['data'],
                            'object':item.get('object',''),'payload':str(payload.resolve())})
            request=batch_dir/('PREFLIGHT_REQUEST_'+str(number).zfill(3)+'.json')
            resultfile=batch_dir/('PREFLIGHT_RESULT_'+str(number).zfill(3)+'.json')
            record_json(request,req)
            if run_preflight:
                run_preflight(toolkit,forge,resultfile,request,log)
            elif runner is not None:
                # Offline fixtures: fake stage runner does not execute native Anvil.
                # Actual production staging ALWAYS runs the binary identity check.
                record_json(resultfile,{'verified':True,'requestedEdits':len(req),
                    'acceptedIndices':list(range(len(req))),'excluded':[],
                    'sourceForge':forge,'sourceBytes':Path(forge).stat().st_size})
            else:
                cmd=['dotnet',str(dll),'stream-preflight',str(toolkit),forge,
                     str(resultfile),str(request)]
                with (batch_dir/'PREFLIGHT_CONSOLE.txt').open('a',encoding='utf-8') as console:
                    with storage_guard.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                          text=True,bufsize=1) as proc:
                        for line in proc.stdout:
                            console.write(line);console.flush();log(line)
                        if proc.wait()!=0:
                            raise RuntimeError('Identity preflight failed for {}; see {}'.format(label,resultfile))
            result=json.loads(resultfile.read_text(encoding='utf-8'))
            accepted=result.get('acceptedIndices')
            excluded=result.get('excluded')
            if (result.get('verified') is not True or result.get('requestedEdits')!=len(items)
                    or result.get('sourceForge')!=forge or
                    result.get('sourceBytes')!=Path(forge).stat().st_size
                    or not isinstance(accepted,list) or not isinstance(excluded,list)):
                raise RuntimeError('Invalid or incomplete identity preflight: '+str(resultfile))
            if (any(type(i)!=int or i<0 or i>=len(items) for i in accepted)
                    or len(set(accepted))!=len(accepted)):
                raise RuntimeError('Duplicate or invalid accepted index in '+str(resultfile))
            rejected={}
            for e in excluded:
                if not isinstance(e,dict) or type(e.get('index'))!=int or e['index']<0 or e['index']>=len(items):
                    raise RuntimeError('Invalid excluded index in '+str(resultfile))
                if e['index'] in rejected or not str(e.get('reason','')).strip():
                    raise RuntimeError('Duplicate or unexplained preflight exclusion in '+str(resultfile))
                rejected[e['index']]=e['reason']
            if set(accepted)&set(rejected) or set(accepted)|set(rejected)!=set(range(len(items))):
                raise RuntimeError('Preflight did not classify all edit routes: '+str(resultfile))
            for i,reason in sorted(rejected.items()):
                row=items[i]
                state['excludedRoutes'].append({'forge':forge,'mod':row['mod'],
                    'archive':row['archive'],'member':row['member'],'data':row['data'],
                    'object':row.get('object',''),'reason':reason})
                log('QUARANTINED unsafe resource: {} -> {}: {}\n'.format(
                    label,row.get('object') or row['data'],reason))
            accepted_by_forge[forge]=[(items[i],req[i]) for i in accepted]
            record_json(batch_dir/'IDENTITY_EXCLUSIONS.json',state['excludedRoutes'])
            record_json(manifest,state)
        if state['excludedRoutes'] and not allow_route_exclusions:
            raise RuntimeError('{} binary identity mismatch(es) require explicit partial-install option; see IDENTITY_EXCLUSIONS.json'.format(len(state['excludedRoutes'])))
        selected=sum(len(v) for v in accepted_by_forge.values())
        if selected==0:
            raise RuntimeError('No edits passed binary identity preflight; refusing staging')
        log('Identity preflight complete: {} safe routes; {} excluded.\n'.format(
            selected,len(state['excludedRoutes'])))
        # Check capacity for only those Forges containing accepted edits.
        selected_forges=[f for f,pairs in accepted_by_forge.items() if pairs]
        total_size=sum(Path(f).stat().st_size for f in selected_forges)
        _space_check(batch_dir,max(int(total_size*1.10),total_size+512*1024**2),
                     'Verified staging copies')
        outputs=batch_dir/'VerifiedForges';outputs.mkdir()
        # Smallest archives first: a failure should not waste a 22 GiB copy.
        for number,forge in enumerate(sorted(selected_forges,key=lambda f:Path(f).stat().st_size),1):
            pairs=accepted_by_forge[forge]
            label=Path(forge).name
            log('Streaming Forge {}/{}: {} with {} identity-verified edits\n'.format(
                number,len(selected_forges),label,len(pairs)))
            req=[entry for _,entry in pairs]
            out=outputs/label
            report=batch_dir/('STREAM_RESULT_'+str(number).zfill(3)+'.json')
            request=batch_dir/('STREAM_REQUEST_'+str(number).zfill(3)+'.json')
            record_json(request,req)
            if runner:
                runner(toolkit,forge,report,request,out,log)
            else:
                cmd=['dotnet',str(dll),'stream-batch',str(toolkit),forge,str(report),str(request),str(out)]
                with (batch_dir/'STREAM_CONSOLE.txt').open('a',encoding='utf-8') as console:
                    with storage_guard.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                          text=True,bufsize=1) as proc:
                        for line in proc.stdout:
                            console.write(line);console.flush();log(line)
                        if proc.wait()!=0:
                            raise RuntimeError('Stream writer failed for {}; report {}'.format(label,report))
            result=json.loads(report.read_text(encoding='utf-8')) if report.is_file() else {}
            required=('verified','originalSourceUnchanged','prefetchBytesPreserved',
                      'untouchedBytesVerified','independentAnvilIndexVerified','allRequestedEditsVerified')
            if any(result.get(k) is not True for k in required):
                raise RuntimeError('Stream writer missing mandatory verification for {}: {}'.format(label,result))
            if result.get('requestedEdits')!=len(pairs) or not out.is_file():
                raise RuntimeError('Not all accepted edits were included for '+label)
            hash_out=sha256(out)
            if hash_out.casefold()!=str(result.get('stagedForgeSha256','')).casefold():
                raise RuntimeError('Stream writer output changed since verification: '+label)
            if sha256(forge)!=plan['sourceHashes'][forge]:
                raise RuntimeError('Installed Forge modified during staging: '+forge)
            state['outputs'][forge]={'sourceSha256':plan['sourceHashes'][forge],
                       'staged':str(out),'stagedSha256':hash_out,
                       'relative':label,'streamReceipt':str(report)}
            for item,_ in pairs:
                state['finishedRoutes'].append({'mod':item['mod'],'kind':item.get('kind','inner'),
                    'data':item['data'],'object':item.get('object',''),'member':item['member'],
                    'forge':forge,'streamReceipt':str(report)})
            record_json(manifest,state)
        if (not state['outputs'] or len(state['finishedRoutes'])!=selected or
              len(state['finishedRoutes'])+len(state['excludedRoutes'])!=len(plan['routes'])):
            raise RuntimeError('Incomplete streaming stage or accounting mismatch')
        state['status']='VERIFIED_STAGED'
        record_json(manifest,state)
        log('VERIFIED STREAM BATCH: {} edits, {} exclusions, {} game Forges. Game not modified.\n'.format(
            selected,len(state['excludedRoutes']),len(state['outputs'])))
        return state
    except Exception as exc:
        state['status']='FAILED';state['error']=str(exc);record_json(manifest,state)
        raise


def _copy_verify(source, dest, expected):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(source,'rb') as f, open(dest,'xb') as out:
        while chunk:=f.read(1<<20):
            storage_guard.check();out.write(chunk)
        out.flush();os.fsync(out.fileno())
    if sha256(dest)!=expected:
        raise IOError('Copy hash mismatch: '+str(dest))


@storage_guard.suspended
def install_staged(batch_manifest, backups_root, game_check=game_processes_running):
    """Install a verified batch with atomic swaps, recoverable backups, and hash guards.

    Callers provide the user-facing Yes/No decision; typing a confirmation phrase
    is not required. All verification and rollback checks remain mandatory.
    """
    batch_manifest=Path(batch_manifest).resolve()
    state=json.loads(batch_manifest.read_text(encoding='utf-8'))
    import deployment_policy
    deployment_policy.require_install(state,batch_manifest)
    if state.get('status') != 'VERIFIED_STAGED' or not state.get('outputs'):
        raise ValueError('Batch staging did not complete verification')
    game=Path(state['plan']['gameRoot']).resolve()
    backups_root=Path(backups_root).resolve()
    if within(game, backups_root) or within(backups_root, game):
        raise ValueError('Backup directory must not contain/be contained by game folder')
    running=game_check()
    if running:
        raise RuntimeError('Game is running: '+', '.join(running))
    if state.get('writer')=='FORGE_LAYERED_BATCH_V20':
        import layered_pipeline
        layered_pipeline.inspect_stage(batch_manifest,game,verify_backups=True)
    # Verify every staged output and establish the live baseline before making
    # a backup. A Forge can already equal the verified staged bytes when a
    # prior successful batch is being extended; that is a safe, idempotent
    # state and needs neither another huge backup nor an atomic rewrite.
    backups_root.mkdir(parents=True,exist_ok=True)
    preflight=[]
    for original, entry in state['outputs'].items():
        file=Path(original).resolve()
        if not within(game,file) or not file.is_file() or file.suffix.lower()!='.forge':
            raise RuntimeError('Unsafe installation destination '+str(file))
        candidate=Path(entry['staged']).resolve()
        if not candidate.is_file() or not within(batch_manifest.parent,candidate):
            raise RuntimeError('Staged output moved outside batch workspace')
        live_hash=sha256(file)
        staged_hash=sha256(candidate)
        allowed_live={entry['sourceSha256'],entry['stagedSha256']}
        if state.get('writer')=='FORGE_LAYERED_BATCH_V20':
            allowed_live.add(entry.get('previousSha256'))
        if live_hash not in allowed_live or staged_hash!=entry['stagedSha256']:
            raise RuntimeError('Source or staged file changed since verification: '+str(file))
        preflight.append({'target':file,'candidate':candidate,'entry':entry,
                          'liveSha256':live_hash,'stagedSha256':staged_hash})
    changed=[item for item in preflight if item['liveSha256']!=item['stagedSha256']]
    # Check space only for files that need a swap; identical staged files are
    # recorded as already installed and are left untouched.
    bytes_backup=sum(item['target'].stat().st_size for item in changed)
    import workspace_storage as storage,mod_manager as manager
    if within(manager.WORK,batch_manifest) and within(manager.WORK,backups_root):
        limit=storage.limit_bytes(manager.load(manager.CONFIG,{}))
        if storage.usage(manager.WORK)['uniqueBytes']+bytes_backup>limit:
            raise storage.StorageError('Storage limit: this installation would exceed the workspace budget with its rollback backup. Use Clean generated files in Diagnostics or increase the storage budget before installing.')
    bytes_pending=max((item['candidate'].stat().st_size for item in changed),default=0)
    margin=512*1024**2 if changed else 0
    if changed and os.stat(game).st_dev == os.stat(backups_root).st_dev:
        _space_check(game,bytes_backup+bytes_pending+margin,'Installation backup and atomic swap')
    elif changed:
        _space_check(backups_root,bytes_backup+margin,'Original-Forge backup')
        _space_check(game,bytes_pending+margin,'Pending game-side atomic swap')
    install_id=time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]
    backup=backups_root/install_id
    backup.mkdir(parents=True,exist_ok=False)
    tx={'schema':1,'status':'BACKING_UP','gameRoot':str(game),
        'batchManifest':str(batch_manifest), 'files':[], 'installed':[],
        'alreadyStaged':[]}
    record_json(backup/'INSTALL_TRANSACTION.json',tx)
    swapped=[]
    try:
        for item in preflight:
            file=item['target']
            if item['liveSha256']==item['stagedSha256']:
                tx['alreadyStaged'].append(str(file))
                continue
            rel=file.relative_to(game)
            backed_up=backup/'Originals'/rel
            _copy_verify(file,backed_up,item['liveSha256'])
            tx['files'].append({'target':str(file),'backup':str(backed_up),
                'originalSha256':item['liveSha256'],'installedSha256':item['stagedSha256']})
            record_json(backup/'INSTALL_TRANSACTION.json',tx)
        tx['status']='INSTALLING';record_json(backup/'INSTALL_TRANSACTION.json',tx)
        for entry in tx['files']:
            running=game_check()
            if running:raise RuntimeError('Game opened during installation: '+', '.join(running))
            file=Path(entry['target']);staged=Path(state['outputs'][str(file.resolve())]['staged'])
            if sha256(file)!=entry['originalSha256']:
                raise RuntimeError('Game archive was modified during installation')
            temp=file.with_name(file.name+'.deef-pending-'+install_id)
            try:
                _copy_verify(staged,temp,entry['installedSha256'])
                os.replace(temp,file)
            finally:
                temp.unlink(missing_ok=True)
            swapped.append(entry)
            tx['installed'].append(str(file))
            record_json(backup/'INSTALL_TRANSACTION.json',tx)
            if sha256(file)!=entry['installedSha256']:
                raise IOError('Installed Forge hash mismatch')
        tx['status']='INSTALLED';record_json(backup/'INSTALL_TRANSACTION.json',tx)
        return backup/'INSTALL_TRANSACTION.json'
    except Exception as e:
        tx['status']='INSTALL_FAILED_ROLLBACK_ATTEMPTED';tx['error']=str(e)
        errors=[]
        for entry in reversed(swapped):
            f=Path(entry['target'])
            try:
                staged_tmp=f.with_name(f.name+'.deef-rollback-'+install_id)
                _copy_verify(Path(entry['backup']),staged_tmp,entry['originalSha256'])
                os.replace(staged_tmp,f)
                if sha256(f)!=entry['originalSha256']:raise IOError('Rollback checksum mismatch')
            except Exception as rollback_err:errors.append(str(f)+': '+str(rollback_err))
        tx['rollbackErrors']=errors
        tx['status']='ROLLED_BACK_AFTER_FAILURE' if not errors else 'ROLLBACK_NEEDS_MANUAL_RECOVERY'
        record_json(backup/'INSTALL_TRANSACTION.json',tx)
        raise RuntimeError('Install failed. Backup: {}. Rollback errors: {}'.format(backup,errors)) from e


def restore_install(transaction_file, game_check=game_processes_running):
    transaction_file=Path(transaction_file).resolve()
    tx=json.loads(transaction_file.read_text(encoding='utf-8'))
    # Interrupted installs may have swapped only some of their Forges.
    # Safe recovery accepts only original or installed hashes for every target.
    recoverable={'INSTALLED','INSTALLING','INSTALL_FAILED_ROLLBACK_ATTEMPTED',
                 'ROLLBACK_NEEDS_MANUAL_RECOVERY','ROLLED_BACK_AFTER_FAILURE'}
    if tx.get('status') not in recoverable:
        raise ValueError('Transaction is not a completed or recoverable installation')
    if game_check():
        raise RuntimeError('Game is running; close it before restoring')
    root=Path(tx['gameRoot']).resolve()
    # Preflight every file before touching anything.
    for item in tx['files']:
        f,b=Path(item['target']).resolve(),Path(item['backup']).resolve()
        if not within(root,f) or not within(transaction_file.parent,b) or not b.is_file():
            raise RuntimeError('Invalid restore file mapping')
        if sha256(b)!=item['originalSha256']:
            raise RuntimeError('Backup hash mismatch; refusing restore')
        if not f.is_file() or sha256(f) not in (item['installedSha256'],item['originalSha256']):
            raise RuntimeError('Game archive changed: differs from both known versions; manual review required: '+str(f))
    for item in reversed(tx['files']):
        dest=Path(item['target'])
        if sha256(dest)==item['originalSha256']:
            continue
        temp=dest.with_name(dest.name+'.deef-restore-'+uuid.uuid4().hex[:8])
        try:
            _copy_verify(Path(item['backup']),temp,item['originalSha256'])
            os.replace(temp,dest)
        finally:temp.unlink(missing_ok=True)
        if sha256(dest)!=item['originalSha256']:
            raise IOError('Restored archive hash mismatch: '+str(dest))
    tx['status']='RESTORED';tx['restoredUtc']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
    record_json(transaction_file,tx)
    return tx
