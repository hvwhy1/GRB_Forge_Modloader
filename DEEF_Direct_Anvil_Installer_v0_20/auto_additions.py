"""Auto-stage non-conflicting NEW GRB resources above a verified replacement stage.

On Windows, runs the AnvilToolkit-backed writers against PRIVATE staged copies.
Failed, conflicting or unlocatable additions are recorded and excluded. A combined
manifest is installable only by the existing backup/hash/rollback installer after
an explicit user Yes/No choice. Neither structural verification nor an install guarantees
that newly added assets are referenced by the game or behave correctly.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import copy
import hashlib
import json
import os
import re
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
import sys
import time
import uuid
import zipfile

import batch_engine as be
import existing_stage
import mod_manager as mm
import route_conventions as rc

ROOT = Path(__file__).resolve().parent

def logical(value):
    leaf = str(value).replace('\\', '/').split('/')[-1]
    token,sep,name = leaf.partition('_-_')
    return (name if sep and token.isdigit() else leaf).casefold()

def canonical_parent(label):
    return rc.parent_data_label(str(label))[0]

def source_basename(name):
    return PureWindowsPath(str(name)).name

def candidates(audit, installed_routes=(), disabled_archives=()):
    """Conflict and provenance preflight; read-only, testable on any OS."""
    disabled = {source_basename(x).casefold() for x in disabled_archives}
    occupied=set()
    for r in installed_routes:
        forge=str(r.get('forge','')).casefold()
        parent=canonical_parent(r.get('data',''))
        data=logical(parent) if r.get('kind')=='data' else parent.casefold()
        if r.get('kind')=='data': occupied.add((forge,data,'*'))
        else: occupied.add((forge,data,logical(r.get('object',''))))
    parsed=[]
    for item in audit.get('candidates',[]):
        r=dict(item)
        r['parentData']=canonical_parent(r.get('parentData',''))
        r['forgeKey']=str(r.get('parentForge','')).casefold()
        r['dataKey']=logical(r['parentData'])
        r['objectKey']=logical(r.get('newObject','')) if r.get('kind')=='inner' else '*'
        # Forge outer identity uses the logical parent name. Missing-parent
        # inner variants retain numeric prefixes until the folder merger can
        # compare their actual payloads and coalesce safe duplicates.
        collision_data=r['dataKey'] if r.get('kind')=='data' else str(r['parentData']).casefold()
        r['collisionKey']=(r['forgeKey'],collision_data,r['objectKey'])
        r['status']='PENDING';r['reason']=''
        raw = str(r.get('member','')).replace('\\','/')
        parts=raw.split('/')
        if (not raw or raw.startswith('/') or any(p in ('','.','..') for p in parts)
            or ':' in parts[0] or '\x00' in raw):
            r['status']='EXCLUDED';r['reason']='UNSAFE_MEMBER'
        elif source_basename(r.get('sourceArchive','')).casefold() in disabled:
            r['status']='EXCLUDED';r['reason']='MOD_DISABLED'
        elif r.get('kind') not in ('data','inner') or not r['parentData'].lower().endswith('.data'):
            r['status']='EXCLUDED';r['reason']='UNKNOWN_RESOURCE_TYPE_OR_PARENT'
        elif r.get('kind')=='data' and not raw.lower().endswith('.data'):
            r['status']='EXCLUDED';r['reason']='NOT_A_COMPLETE_DATA_RESOURCE'
        elif r.get('kind')=='inner' and (not r.get('newObject') or not r['objectKey']):
            r['status']='EXCLUDED';r['reason']='MISSING_INNER_OBJECT'
        elif r['collisionKey'] in occupied:
            r['status']='EXCLUDED';r['reason']='CONFLICTS_WITH_STAGED_REPLACEMENT'
        parsed.append(r)
    by_resource=defaultdict(list)
    for r in parsed:
        if r['status']=='PENDING':by_resource[r['collisionKey']].append(r)
    for group in by_resource.values():
        if len(group)>1:
            for r in group:r.update(status='EXCLUDED',reason='MULTIPLE_MODS_SAME_RESOURCE')
    return parsed

def _disabled_mods(audit_candidates=()):
    """Honor explicit user toggles, then default-off mods in DEEF's manifest."""
    state=mm.load(mm.STATE,{})
    disabled={source_basename(key).casefold() for key,value in state.items()
              if isinstance(value,dict) and value.get('enabled') is False}
    config=ROOT/'data'/'deef_modlist_v3_2_5.json'
    if not config.is_file():return sorted(disabled)
    choices=json.loads(config.read_text(encoding='utf-8'))['mods']
    by_id=defaultdict(list)
    for item in choices:by_id[str(item.get('nexus_id'))].append(item)
    for name in (x.get('sourceArchive','') for x in audit_candidates):
        leaf=source_basename(name)
        match=re.search(r'-(\d{2,5})-(?:\d|v|V)',leaf)
        if not match:continue
        entry=by_id.get(match.group(1),[])
        if len(entry)!=1 or entry[0].get('enabled') is not False:continue
        relevant=[m for k,m in state.items() if source_basename(k).casefold()==leaf.casefold()]
        if any(m.get('_enabled_by_user') and m.get('enabled') is True for m in relevant):continue
        disabled.add(leaf.casefold())
    return sorted(disabled)

def _receipt_ok(receipt, src, out, expected_count):
    return bool(receipt.get('verified') is True and
        receipt.get('independentAnvilIndexVerified') is True and
        receipt.get('sourceUnchanged') is True and
        receipt.get('eligibleForDeployment') is True and
        receipt.get('addedEntries')==expected_count and
        receipt.get('gameModified') is False and
        str(receipt.get('sourceForge','')).casefold()==str(src).casefold() and
        receipt.get('outputSha256','').casefold()==be.sha256(out).casefold())

def _bridge(toolkit, dll, source, kind, data, requests, dest_dir, notify, runner=None):
    dest_dir.mkdir(parents=True,exist_ok=False)
    req=dest_dir/'REQUEST.json'; report=dest_dir/'RESULT.json'; out=dest_dir/Path(source).name
    req.write_text(json.dumps(requests,indent=2),encoding='utf-8')
    command='stream-add-outer-trial' if kind=='outer' else 'stream-add-inner-batch'
    cmd=['dotnet',str(dll),command,str(toolkit),str(source)]
    if kind=='inner':cmd.append(data)
    cmd += ([str(report),str(req),str(out)] if kind=='outer'
            else [str(req),str(report),str(out)])
    if runner is not None:
        code=runner(cmd,report,out)
    else:
        with (dest_dir/'CONSOLE.txt').open('w',encoding='utf-8') as f:
            with subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                  text=True,errors='replace',bufsize=1) as proc:
                for line in proc.stdout:
                    f.write(line);f.flush();notify(line)
                code=proc.wait()
    result=json.loads(report.read_text(encoding='utf-8')) if report.exists() else {}
    if code or not out.is_file() or not _receipt_ok(result,source,out,len(requests)):
        out.unlink(missing_ok=True)
        return None,report,str(result.get('error') or 'WRITER_FAILED_OR_INCOMPLETE_INDEPENDENT_READBACK')[:600]
    return out,report,''

def _inventory(toolkit,dll,forge,dest_dir,notify, inspector=None):
    if inspector:
        return inspector(forge)
    report=dest_dir/('INVENTORY_'+uuid.uuid4().hex[:8]+'.json')
    p=subprocess.run(['dotnet',str(dll),'stream-inspect',str(toolkit),str(forge),str(report),'AUTO'],
                     capture_output=True,text=True,errors='replace')
    if p.returncode or not report.exists():
        raise RuntimeError('Forge index readback failed: '+p.stdout[-600:])
    result=json.loads(report.read_text(encoding='utf-8'))
    if result.get('verified') is not True:
        raise RuntimeError('Forge index not verified: '+str(result.get('error')))
    return result.get('nestedDataEntries',[])

def _matching_parent(available, wanted):
    matches=[n for n in available if logical(n)==logical(wanted)]
    return matches[0] if len(matches)==1 else None

def _extract_parent_folder(rows,dest):
    """Merge direct files from the archives' unpacked .data folders safely."""
    dest.mkdir(parents=True,exist_ok=False)
    roots={}
    for row in rows:
        member=str(row.get('member','')).replace('\\','/')
        root=member.rpartition('/')[0]
        if not root or PureWindowsPath(root).name.casefold()!=source_basename(row.get('parentData','')).casefold():
            raise ValueError('UNPACKED_PARENT_PATH_DOES_NOT_MATCH_DECLARED_DATA')
        roots.setdefault((str(row['archivePath']),root.casefold()),
                         (Path(row['archivePath']),root,row.get('parentData','')))
    written={}
    conflicts={}
    for archive,root,parent_data in roots.values():
        if not zipfile.is_zipfile(archive):raise ValueError('MISSING_PARENT_REPACK_REQUIRES_ZIP_ARCHIVE')
        prefix=root.rstrip('/')+'/'
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                raw=info.filename.replace('\\','/')
                if not raw.casefold().startswith(prefix.casefold()) or info.is_dir():continue
                rel=raw[len(prefix):]
                if not rel or '/' in rel or rel in ('.','..') or ':' in rel or '\x00' in rel:continue
                mode=(info.external_attr>>16)&0xF000
                if mode==0xA000:continue
                key=rel.casefold();payload=bundle.read(info)
                digest=hashlib.sha256(payload).hexdigest()
                previous=written.get(key)
                if previous:
                    if previous[1]!=digest:
                        conflict=conflicts.setdefault(key,{'file':rel,'winnerParentData':previous[2],
                            'winnerSha256':previous[1],'variants':{previous[2].casefold():previous[1]}})
                        conflict['variants'][str(parent_data).casefold()]=digest
                    continue
                out=dest/rel;out.write_bytes(payload);written[key]=(out,digest,parent_data)
    if not written:raise ValueError('UNPACKED_NEW_PARENT_HAS_NO_FILES')
    return len(written),conflicts

def _zip_member_sha(row):
    with zipfile.ZipFile(row['archivePath']) as bundle:
        return hashlib.sha256(bundle.read(str(row['member']).replace('\\','/'))).hexdigest()

def _pack_new_data(toolkit,dll,source_dir,output,report,notify,runner=None):
    command=['dotnet',str(dll),'stream-pack-new-data',str(toolkit),str(source_dir),str(output),str(report)]
    if runner is not None:code=runner(command,report,output)
    else:
        with (report.parent/'PACK_CONSOLE.txt').open('a',encoding='utf-8') as log:
            with subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                  text=True,errors='replace',bufsize=1) as proc:
                for line in proc.stdout:
                    log.write(line);log.flush();notify(line)
                code=proc.wait()
    result=json.loads(report.read_text(encoding='utf-8')) if report.exists() else {}
    ok=(code==0 and result.get('verified') is True and result.get('sourceUnchanged') is True and
        result.get('gameModified') is False and result.get('readbackFileCount',0)>0 and
        result.get('serializedFileCount')==result.get('readbackFileCount') and
        result.get('rootIdentityVerified') is True and output.is_file() and
        result.get('outputSha256','').casefold()==be.sha256(output).casefold())
    if not ok:
        output.unlink(missing_ok=True)
        raise RuntimeError(str(result.get('error') or 'NEW_DATA_PACK_OR_INDEPENDENT_READBACK_FAILED')[:700])
    return result

def _base_manifest(base,game):
    if base is None:
        return {'schema':3,'writer':'FORGE_STREAM_BATCH_V27','plan':{'gameRoot':str(game),
                'routes':[], 'sourceHashes':{},'toolkit':''},'outputs':{},
                'finishedRoutes':[],'excludedRoutes':[]},None
    original=Path(base).resolve()
    existing_stage.inspect_existing_stage(original,expected_game=game)
    return json.loads(original.read_text(encoding='utf-8')),original

def stage(audit_path,game_root,mods_dir,toolkit, *, base_manifest=None,
          notify=print,bridge_runner=None,inspector=None,pack_runner=None,max_fail_splits=24):
    audit_path=Path(audit_path).resolve();game=Path(game_root).resolve();mods=Path(mods_dir).resolve()
    toolkit=Path(toolkit).resolve()
    if not (game/'GRB.exe').is_file() or not mods.is_dir() or not (toolkit/'AnvilToolkit.dll').is_file():
        raise FileNotFoundError('Missing GRB.exe, mod archives, or AnvilToolkit.dll')
    audit=json.loads(audit_path.read_text(encoding='utf-8-sig'))
    if audit.get('schema') not in ('DEEF.new_resource_audit.1','DEEF.new_resource_audit.2'):
        raise ValueError('Select NEW_RESOURCE_AUDIT.json, not a characterization JSON')
    if Path(audit.get('game','')).resolve()!=game:
        raise ValueError('Audit belongs to a different game root; rerun characterization')
    state,base=_base_manifest(base_manifest,game)
    state=copy.deepcopy(state)
    records=candidates(audit,state['finishedRoutes'],_disabled_mods(audit.get('candidates',[])))
    ts=time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]
    parent=base.parent if base else ROOT/'workspace'/'addition_batches'/ts
    parent.mkdir(parents=True,exist_ok=True)
    work=parent/('NewResources_'+ts)
    work.mkdir(parents=True,exist_ok=False)
    manifest=parent/('BATCH_WITH_ADDITIONS_'+ts+'.json') if base else parent/'BATCH_RESULT.json'
    state['plan']['gameRoot']=str(game);state['plan']['toolkit']=str(toolkit)
    status={'schema':'DEEF.additions.19.9','baseStage':str(base) if base else None,
            'sourceAudit':str(audit_path),'candidateCount':len(records),'attempted':[],
            'added':[],'excluded':[],'packing':[],'stageManifest':str(manifest),'gameModified':False,
            'gameCompatibilityTested':False}
    def exclude(r,reason):
        r['status']='EXCLUDED';r['reason']=reason
        status['excluded'].append({'index':r.get('index'),'archive':r.get('sourceArchive'),
            'member':r.get('member'),'forge':r.get('parentForge'),'reason':reason})
    for r in records:
        if r['status']=='EXCLUDED':exclude(r,r['reason'])
    dll=None if bridge_runner else be.build_bridge(toolkit,notify)
    pending=[x for x in records if x['status']=='PENDING']
    by_forge=defaultdict(list)
    for row in pending:
        forge=Path(row.get('parentForge','')).resolve()
        archive=mods/source_basename(row.get('sourceArchive',''))
        if forge.parent!=game or not forge.is_file() or forge.suffix.lower()!='.forge':
            exclude(row,'GAME_ROOT_FORGE_ABSENT');continue
        if not archive.is_file():exclude(row,'MOD_ARCHIVE_ABSENT');continue
        row['actualForge']=str(forge);row['archivePath']=str(archive)
        by_forge[str(forge)].append(row)
    if len(by_forge)>100:raise ValueError('Unexpected number of target Forges')
    # A single successful private output is promoted to the next group's input;
    # a failed group NEVER changes the running base copy.
    seq=0
    other_inventory={}
    def base_parent_matches(wanted,target):
        matches=[]
        for forge in game.glob('*.forge'):
            if str(forge.resolve()).casefold()==str(target).casefold():continue
            key=str(forge.resolve())
            if key not in other_inventory:
                try:other_inventory[key]=_inventory(toolkit,dll,forge,work,notify,inspector)
                except Exception as error:
                    # An incomplete game index cannot prove a genuinely new parent.
                    raise RuntimeError('Base-parent discovery failed: '+forge.name+': '+str(error))
            matches.extend((key,name) for name in other_inventory[key] if logical(name)==logical(wanted))
        return matches
    for original in sorted(by_forge,key=lambda x:Path(x).stat().st_size):
        rows=by_forge[original]
        base_entry=state['outputs'].get(original)
        current=Path(base_entry['staged']) if base_entry else Path(original)
        if base_entry and (not current.is_file() or
                           be.sha256(current)!=base_entry['stagedSha256']):
            for r in rows:exclude(r,'BASE_VERIFIED_STAGE_HASH_CHANGED')
            continue
        original_sha=state['plan']['sourceHashes'].get(original) or be.sha256(original)
        live_sha=be.sha256(original)
        if live_sha!=original_sha:
            # If the user already installed this exact staged Forge, it is now
            # the live baseline. Rebase only on the matching recorded output;
            # any other unexpected game-file change still fails closed.
            if base_entry and live_sha==base_entry.get('stagedSha256'):
                original_sha=live_sha
                state['plan']['sourceHashes'][original]=live_sha
                base_entry['sourceSha256']=live_sha
            else:
                for r in rows:exclude(r,'ORIGINAL_GAME_FORGE_CHANGED')
                continue
        accepted=[]; receipts=[]
        null_directory_single_failures=0
        def inventory():return _inventory(toolkit,dll,current,work,notify,inspector)
        # For new complete .data files, first check that the name is genuinely absent.
        try:existing=inventory()
        except Exception as ex:
            for r in rows:exclude(r,'FORGE_INDEX_FAILED: '+str(ex)[:200])
            continue
        for r in rows:
            if r['kind']=='data' and _matching_parent(existing,r['parentData']):
                exclude(r,'RESOURCE_ALREADY_EXISTS_REPLACEMENT_OR_CONFLICT')
        remaining=[r for r in rows if r['status']=='PENDING' and r['kind']=='data']
        # Existing parent .data may be missing in characterization because legacy
        # routing interpreted an instruction folder as the literal filename.
        # The parent is rechecked against the actual Forge table below.
        for kind,groups in [('outer', [('NEW_OUTER_DATA',remaining)])]:
            for group_name,group in groups:
                if not group:continue
                for r in list(group):
                    try:
                        r['payload']=str(mm.extraction_target(r['archivePath'],r['member']))
                    except Exception as ex:
                        exclude(r,'ARCHIVE_EXTRACTION_FAILED: '+str(ex)[:200])
                group=[r for r in group if r['status']=='PENDING']
                if not group:continue
                # If a whole group fails, split to rescue independent non-conflicting
                # additions. Splits are bounded to avoid unbounded multi-GB copies.
                queue=[group];failures=0
                while queue:
                    group=queue.pop(0)
                    req=[{'data':r['parentData'],'payload':r['payload']} for r in group]
                    seq+=1;folder=work/('stage_%04d'%seq)
                    needed=Path(current).stat().st_size+256*1024**2
                    if shutil.disk_usage(work).free<needed:
                        for r in group:exclude(r,'INSUFFICIENT_DISK_SPACE_FOR_PRIVATE_COPY')
                        continue
                    output,receipt,error=_bridge(toolkit,dll,current,'outer','',req,folder,notify,bridge_runner)
                    status['attempted'].append({'forge':original,'kind':'outer','count':len(group),
                                                'receipt':str(receipt),'verified':bool(output)})
                    if output:
                        receipts.append(str(receipt));old=current;current=output
                        for r in group:
                            r['status']='STAGED';accepted.append(r)
                        if be.within(work,old):old.unlink(missing_ok=True)
                    elif (len(group)==1 and
                          'AnvilToolkit returned NULL Directories' in error):
                        null_directory_single_failures+=1
                        for r in group:exclude(r,'NEW_MOD_ASSET_UNSUPPORTED_BY_ANVIL_TOOLKIT_DIRECTORY_INDEX')
                        if null_directory_single_failures>=3:
                            # Three distinct payloads from the same Forge have
                            # passed the local Forge parser but are rejected by
                            # AnvilToolkit's independent directory index. More
                            # copies cannot make an unsupported index readable.
                            for r in rows:
                                if r['status']=='PENDING' and r.get('kind')=='data':
                                    exclude(r,'NEW_MOD_ASSET_UNSUPPORTED_BY_ANVIL_TOOLKIT_DIRECTORY_INDEX')
                            queue.clear()
                    elif len(group)>1 and failures<max_fail_splits:
                        failures+=1;mid=len(group)//2
                        queue[:0]=[group[:mid],group[mid:]]
                    else:
                        for r in group:exclude(r,'OUTER_INSERTION_NOT_VERIFIED: '+error[:170])
        try:available=inventory()
        except Exception as ex:
            for r in rows:
                if r['status']=='PENDING':exclude(r,'FINAL_FORGE_INDEX_FAILED: '+str(ex)[:200])
            available=[]
        inner_groups=defaultdict(list)
        missing_parent_groups=defaultdict(list)
        for r in rows:
            if r['status']!='PENDING' or r.get('kind')!='inner':continue
            parent_name=_matching_parent(available,r['parentData'])
            if parent_name is None:
                missing_parent_groups[logical(r['parentData'])].append(r)
            else:
                r['parentMatched']=parent_name
                inner_groups[parent_name.casefold()].append(r)
        # A missing parent is often present in the mod archive as an unpacked
        # folder. Repack each exact parent folder once, verify its nested index,
        # then add the resulting .data file to the private Forge copy.
        synth=[]
        # Keep extracted leaf files under the short workspace path. Full run
        # paths plus long game resource names exceed legacy Windows path limits.
        synth_root=ROOT/'workspace'/'new_parent_packs'/ts
        for key,group in sorted(missing_parent_groups.items()):
            parent_name=group[0]['parentData']
            folder=synth_root/(re.sub(r'[^A-Za-z0-9_.-]+','_',parent_name)+'.files')
            packed=synth_root/(source_basename(parent_name))
            pack_report=synth_root/(re.sub(r'[^A-Za-z0-9_.-]+','_',parent_name)+'.PACK_RESULT.json')
            try:
                matches=base_parent_matches(parent_name,original)
                if matches:
                    for r in group:exclude(r,'EXISTING_BASE_PARENT_REQUIRES_REPLACEMENT_ROUTE: '+
                        ', '.join(Path(f).name+'/'+n for f,n in matches)[:400])
                    continue
                _,variant_conflicts=_extract_parent_folder(group,folder)
                accepted_rows=[]
                for r in group:
                    conflict=variant_conflicts.get(PureWindowsPath(
                        str(r['member']).replace('\\','/')).name.casefold())
                    if conflict:
                        digest=_zip_member_sha(r)
                        winner=conflict['winnerSha256']
                        if digest!=winner:
                            exclude(r,'COMPETING_PARENT_VARIANT_DIFFERENT_PAYLOAD:'+conflict['file'])
                            continue
                    accepted_rows.append(r)
                if not accepted_rows:continue
                packed.parent.mkdir(parents=True,exist_ok=True)
                pack_result=_pack_new_data(toolkit,dll,folder,packed,pack_report,notify,pack_runner)
                synth.append({'data':parent_name,'payload':packed,'rows':accepted_rows,
                              'packReceipt':str(pack_report),'packedFiles':pack_result['serializedFileCount']})
                status['packing'].append({'data':parent_name,'resourceCount':len(accepted_rows),
                    'packedFileCount':pack_result['serializedFileCount'],'variantConflictFiles':len(variant_conflicts),
                    'receipt':str(pack_report),'verified':True})
            except Exception as ex:
                for r in group:exclude(r,'NEW_PARENT_REPACK_FAILED: '+str(ex)[:220])
                status['packing'].append({'data':parent_name,'resourceCount':len(group),
                    'receipt':str(pack_report),'verified':False,'error':str(ex)[:400]})
        if synth:
            try:existing_after_pack=inventory()
            except Exception as ex:
                existing_after_pack=[]
                for item in synth:
                    for r in item['rows']:exclude(r,'FORGE_INDEX_FAILED_BEFORE_NEW_PARENT_INSERTION: '+str(ex)[:180])
                synth=[]
            for item in list(synth):
                if _matching_parent(existing_after_pack,item['data']):
                    for r in item['rows']:exclude(r,'NEW_PARENT_ALREADY_EXISTS_OR_IDENTITY_AMBIGUOUS')
                    synth.remove(item)
            queue=[synth];failures=0
            while queue:
                group=queue.pop(0)
                req=[{'data':item['data'],'payload':str(item['payload'])} for item in group]
                seq+=1;folder=work/('stage_%04d'%seq)
                needed=Path(current).stat().st_size+256*1024**2
                if shutil.disk_usage(work).free<needed:
                    for item in group:
                        for r in item['rows']:exclude(r,'INSUFFICIENT_DISK_SPACE_FOR_PRIVATE_COPY')
                    continue
                output,receipt,error=_bridge(toolkit,dll,current,'outer','',req,folder,notify,bridge_runner)
                status['attempted'].append({'forge':original,'kind':'new_parent_outer','count':len(group),
                                            'receipt':str(receipt),'verified':bool(output)})
                if output:
                    receipts.append(str(receipt));old=current;current=output
                    for item in group:
                        for r in item['rows']:r['status']='STAGED';accepted.append(r)
                    if be.within(work,old):old.unlink(missing_ok=True)
                elif len(group)>1 and failures<max_fail_splits:
                    failures+=1;mid=len(group)//2;queue[:0]=[group[:mid],group[mid:]]
                else:
                    for item in group:
                        for r in item['rows']:exclude(r,'NEW_PARENT_OUTER_INSERTION_NOT_VERIFIED: '+error[:180])
        for _,batch in sorted(inner_groups.items()):
            for r in batch:
                try:r['payload']=str(mm.extraction_target(r['archivePath'],r['member']))
                except Exception as ex:exclude(r,'ARCHIVE_EXTRACTION_FAILED: '+str(ex)[:180])
            batch=[r for r in batch if r['status']=='PENDING']
            if not batch:continue
            queue=[batch];splits=0
            while queue:
                group=queue.pop(0)
                req=[{'name':r['newObject'],'payload':r['payload']} for r in group]
                seq+=1;folder=work/('stage_%04d'%seq)
                needed=Path(current).stat().st_size+256*1024**2
                if shutil.disk_usage(work).free<needed:
                    for r in group:exclude(r,'INSUFFICIENT_DISK_SPACE_FOR_PRIVATE_COPY')
                    continue
                output,receipt,error=_bridge(toolkit,dll,current,'inner',group[0]['parentMatched'],
                                              req,folder,notify,bridge_runner)
                status['attempted'].append({'forge':original,'kind':'inner','count':len(group),
                                            'receipt':str(receipt),'verified':bool(output)})
                if output:
                    receipts.append(str(receipt));old=current;current=output
                    for r in group:r['status']='STAGED';accepted.append(r)
                    if be.within(work,old):old.unlink(missing_ok=True)
                elif len(group)>1 and splits<max_fail_splits:
                    splits+=1;mid=len(group)//2;queue[:0]=[group[:mid],group[mid:]]
                else:
                    for r in group:exclude(r,'INNER_INSERTION_NOT_VERIFIED: '+error[:170])
        if accepted:
            stage_hash=be.sha256(current)
            stream_receipt=receipts[-1]
            route_count=sum(1 for route in state['finishedRoutes']
                            if str(Path(route.get('forge','')).resolve()).casefold()==original.casefold())
            if base and base_entry and route_count:
                combined_receipt=work/('COMBINED_STAGE_'+Path(original).stem+'.json')
                be.record_json(combined_receipt,{
                    'schema':'DEEF.combined_stage_receipt.1','verified':True,
                    'originalSourceUnchanged':True,'prefetchBytesPreserved':True,
                    'untouchedBytesVerified':True,'independentAnvilIndexVerified':True,
                    'allRequestedEditsVerified':True,'requestedEdits':route_count,
                    'sourceSha256':original_sha,'stagedForgeSha256':stage_hash,
                    'additionChain':{'baseManifest':str(base),'receipts':receipts},
                    'gameModified':False,'gameCompatibilityTested':False,
                    'warning':'This composite proof links the verified replacement stage to independently checked private additions.'})
                stream_receipt=str(combined_receipt)
            state['outputs'][original]={
                'sourceSha256':original_sha,'staged':str(current),
                'stagedSha256':stage_hash,'relative':Path(original).name,
                'streamReceipt':stream_receipt}
            state['plan']['sourceHashes'][original]=original_sha
            for r in accepted:
                status['added'].append({'index':r.get('index'),'archive':r.get('sourceArchive'),
                    'member':r.get('member'),'forge':original,'data':r['parentData'],
                    'object':r.get('newObject',''),'kind':r['kind']})
            notify('Staged {} new non-conflicting resources in {}\n'.format(len(accepted),Path(original).name))
    status['verifiedAdditionCount']=len(status['added'])
    status['excludedCount']=len(status['excluded'])
    state['additionSummary']={'added':len(status['added']),'excluded':len(status['excluded']),
        'sourceAudit':str(audit_path),'baseManifest':str(base) if base else None,
        'receipts':[r['receipt'] for r in status['attempted'] if r['verified']],
        'gameCompatibilityTested':False}
    state['privateOnly']=True
    state['installationAllowed']=False
    state['deploymentReason']='New assets are private experiments; existing base parents must be routed as replacements.'
    state['status']='VERIFIED_STAGED' if state['outputs'] else 'NO_INSTALLABLE_CHANGES'
    # This batch is only structurally verified. It does NOT claim all mods were installed.
    be.record_json(work/'ADDITION_RESULT.json',status)
    with (work/'ADDITION_EXCLUSIONS.csv').open('w',encoding='utf-8-sig',newline='') as f:
        import csv
        writer=csv.DictWriter(f,fieldnames=['index','archive','member','forge','reason'])
        writer.writeheader();writer.writerows(status['excluded'])
    be.record_json(manifest,state)
    notify('Addition stage: {} verified new resources; {} excluded or unresolved.\n'.format(
        len(status['added']),len(status['excluded'])))
    notify('Manifest: {}\nReport: {}\n'.format(manifest,work/'ADDITION_RESULT.json'))
    return str(manifest),status

def install(manifest,backup, *,game_check=be.game_processes_running):
    """Install a verified combined batch after the caller obtains a Yes/No choice."""
    state=json.loads(Path(manifest).read_text(encoding='utf-8'))
    if state.get('status')!='VERIFIED_STAGED' or not state.get('additionSummary'):
        raise ValueError('No verified combined batch')
    return be.install_staged(manifest,backup,game_check=game_check)

def locate_audit():
    import route_convention_audit
    recent=sorted((ROOT/'workspace'/'auto_runs').glob('*/additions/NEW_RESOURCE_AUDIT.json'),
                   key=lambda p:p.stat().st_mtime,reverse=True)
    return recent[0] if recent else route_convention_audit.locate_audit()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audit');p.add_argument('--game-root');p.add_argument('--mods-dir');p.add_argument('--toolkit')
    p.add_argument('--base-stage');p.add_argument('--backup-root');p.add_argument('--install',action='store_true')
    p.add_argument('--max-fail-splits',type=int,default=24)
    a=p.parse_args()
    settings=mm.load(mm.CONFIG,mm.DEFAULT_SETTINGS)
    from toolkit_config import require_toolkit
    game=a.game_root or settings.get('game_root');mods=a.mods_dir or settings.get('archive_dir')
    kit=a.toolkit or str(require_toolkit())
    audit=a.audit or locate_audit()
    if not audit:raise SystemExit('No new-resource audit found. Run SCAN + STAGE first.')
    base=a.base_stage
    if not base:
        available=existing_stage.find_existing_stages(ROOT,expected_game=game)
        base=available[0]['manifest'] if available else None
    manifest,report=stage(audit,game,mods,kit,base_manifest=base,max_fail_splits=a.max_fail_splits)
    if a.install:
        transaction=install(manifest,a.backup_root or settings.get('backup_root'))
        print('Original Forges backed up; installed verified staged bytes. Transaction:',transaction)
    else:print('STAGED ONLY. No live game files modified. Use the GUI to install after reviewing exclusions.')

if __name__=='__main__':main()
