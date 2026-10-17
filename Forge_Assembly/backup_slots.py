"""Two durable recovery sets: clean baseline and pre-change installed bytes.

Ordinary transaction backups remain until both replacement sets are verified.
Retired receipts survive as diagnostics; save files are never managed here.
"""
import contextlib,contextvars,copy,functools,json,os,re,shutil,uuid
from pathlib import Path
import batch_engine as be,mod_manager as mm

_deferred=contextvars.ContextVar('defer_backup_rotation',default=False)


def ordered_runtime_original(target,current,history):
    """Accept a dated history only when every adjacent hash transition agrees.

    Reinstalling A after B produces repeated hashes, so a hash-only graph is
    ambiguous even though the recorded chronological install chain is complete.
    Missing dates, tied timestamps, gaps and unknown edits remain unresolved.
    """
    rows=[]
    for receipt,doc in history:
        matches=[r for r in doc.get('files',[]) if r['target']==target and r.get('originalSha256')!=r.get('installedSha256')]
        if not matches:continue
        date=re.search(r'(\d{8}_\d{6})',receipt.parent.name)
        if not date or doc.get('status')!='INSTALLED' or len(matches)!=1:return None
        rows.append((date.group(1),matches[0]))
    if not rows or len({date for date,_ in rows})!=len(rows):return None
    rows.sort(key=lambda row:row[0]);digest=rows[0][1].get('originalSha256')
    for _,row in rows:
        if row.get('originalSha256')!=digest:return None
        digest=row.get('installedSha256')
    return rows[0][1] if digest==current else None

@contextlib.contextmanager
def deferred():
    token=_deferred.set(True)
    try:yield
    finally:_deferred.reset(token)


def after_config(receipt,game,backups):
    if _deferred.get():return receipt
    base=Path(backups).resolve();anchor=base/'Baseline/INSTALL_TRANSACTION.json'
    if not be.within(mm.WORK.resolve(),base):return receipt
    if not anchor.is_file():
        import deployment_policy
        try:deployment_policy.require_ready(game)
        except (RuntimeError,ValueError):return receipt
        folder=base/('CONFIG_BASELINE_'+uuid.uuid4().hex[:6]);folder.mkdir()
        anchor=folder/'INSTALL_TRANSACTION.json'
        be.record_json(anchor,{'status':'INSTALLED','gameRoot':str(Path(game).resolve()),'files':[]})
    rotate(None,None,base,anchor,receipt,runtime_only=True)
    return base/'LastWorking/CONFIG_TRANSACTION.json'


def config_rotation(fn):
    @functools.wraps(fn)
    def wrapped(plan,game,backups,*args,**kwargs):
        receipt=fn(plan,game,backups,*args,**kwargs)
        return after_config(receipt,game,backups)
    return wrapped


def after_restore(receipt):
    """A verified slot restore becomes the known live version for future builds."""
    receipt=Path(receipt).resolve()
    if receipt.parent.name not in ('Baseline','LastWorking') or not be.within(mm.WORK.resolve(),receipt):return
    base=receipt.parent.parent;anchor=base/'Baseline'/receipt.name
    if not anchor.is_file():return
    doc=mm.load(anchor,{});live={}
    for row in doc.get('files',[]):
        target=Path(row['target']);digest=be.sha256(target) if target.is_file() else None
        row['installedSha256']=digest;live[str(target)]=digest
    doc['status']='INSTALLED';be.record_json(anchor,doc)
    if receipt.name!='INSTALL_TRANSACTION.json':return
    cfg=mm.load(mm.CONFIG,{});stage=Path(cfg.get('last_stage_manifest') or '')
    if stage.is_file() and be.within(mm.WORK.resolve(),stage):
        for path in stage.parent.rglob('BATCH_RESULT.json'):
            state=mm.load(path,{})
            if state.get('writer')!='FORGE_LAYERED_BATCH_V20':continue
            for target,row in state['outputs'].items():
                if row.get('previousSha256') and target in live:row.update(previousTransaction=str(anchor),previousSha256=live[target])
            be.record_json(path,state)


def known_baseline(receipt,target):
    receipt=Path(receipt).resolve()
    if receipt.parent.name not in ('Baseline','LastWorking') or not be.within(mm.WORK.resolve(),receipt):return False,None
    anchor=receipt.parent.parent/'Baseline'/receipt.name
    for row in mm.load(anchor,{}).get('files',[]):
        if row['target']==str(target):return True,row.get('originalSha256')
    return False,None


def after_proven(manifest,receipt,backups):
    import deployment_policy,layered_pipeline as lp
    base=Path(backups).resolve()
    if not be.within(mm.WORK.resolve(),base):return receipt
    cfg=mm.load(mm.CONFIG,{});tx=mm.load(receipt,{});game=Path(tx['gameRoot']).resolve()
    try:baseline=deployment_policy.require_ready(game)
    except (RuntimeError,ValueError):return receipt
    history=lp.receipt_history(game,[str(receipt),cfg.get('last_transaction'),cfg.get('last_actual_transaction')])
    rows=[]
    for item in mm.load(manifest,{})['files']:
        if item['kind']!='forge':continue
        target=str(game/item['target']);live=be.sha256(target)
        if live!=item['sha256']:raise ValueError('Saved-setup Forge changed before recovery recording')
        row,prior,original=lp.clean_history_entry(history,target,baseline['hashes'][target],live)
        rows.append(dict(row,installedSha256=live))
    folder=base/('PROVEN_BASELINE_'+uuid.uuid4().hex[:6]);folder.mkdir()
    # rotate() verifies and copies the baseline source; this temporary receipt
    # deliberately references the original recorded backup instead of a new copy.
    anchor=folder/'INSTALL_TRANSACTION.json';be.record_json(anchor,{'status':'INSTALLED','gameRoot':str(game),'files':rows})
    return rotate(None,receipt,base,anchor)


def rotate(manifest,installation,backups,baseline_receipt,configuration=None,notify=lambda _:None,*,runtime_only=False):
    import deployment_policy as policy,layered_pipeline as lp,runtime_history,workspace_storage as ws
    base=Path(backups).resolve();work=mm.WORK.resolve();cfg=mm.load(mm.CONFIG,{})
    if not be.within(work,base):return Path(baseline_receipt)
    baseline_doc=mm.load(baseline_receipt,{});game=Path(baseline_doc['gameRoot']).resolve()
    baseline=policy.require_ready(game);actual=mm.load(installation,{}) if installation else {}
    runtime_path=configuration or actual.get('runtimeTransaction') or actual.get('configurationTransaction')
    runtime=mm.load(runtime_path,{}) if runtime_path else {}
    old_runtime=mm.load(base/'Baseline/CONFIG_TRANSACTION.json',{})
    history=lp.receipt_history(game,[str(installation or ''),str(runtime_path or ''),cfg.get('last_transaction'),cfg.get('last_config_transaction'),cfg.get('baseline_config_transaction')])
    old_rows={r['target']:r for r in old_runtime.get('files',[])}
    config_rows={r['target']:r for r in runtime.get('files',[])}
    ownership={r['target'] for _,doc in history for r in doc.get('files',[]) if runtime_history.safe_target(game,r['target'])}
    changes={r['target']:r for r in actual.get('files',[])}
    no_change=not changes and not any(r.get('originalSha256')!=r.get('installedSha256') for r in config_rows.values()) and (base/'LastWorking/INSTALL_TRANSACTION.json').is_file()
    last_forges={r['target']:r for r in mm.load(base/'LastWorking/INSTALL_TRANSACTION.json',{}).get('files',[])}
    last_runtime={r['target']:r for r in mm.load(base/'LastWorking/CONFIG_TRANSACTION.json',{}).get('files',[])}
    slots={name:base/(name+'.pending-'+uuid.uuid4().hex[:6]) for name in ('Baseline','LastWorking')}
    retired={};published=[];rewrites={};original_cfg=copy.deepcopy(cfg);carried_docs={}
    def rename(origin,destination):
        be.replace_with_retry(origin,destination,error_code='BACKUP_RENAME_BLOCKED',attempts=16)
    usage=None;copied=0
    for folder in slots.values():folder.mkdir(parents=True)
    def copy_verified(origin,pending,digest):
        nonlocal usage,copied
        size=Path(origin).stat().st_size
        be._space_check(base,size+64*1024**2,'Recovery slots')
        if size>=64*1024**2:
            if usage is None:usage=ws.usage(work)['uniqueBytes']
            if usage+copied+size>ws.limit_bytes(cfg):raise ws.StorageError('Storage limit: two verified recovery slots would exceed the workspace budget. Existing backups retained.')
            copied+=size
        be._copy_verify(origin,pending,digest)
    def source(row):
        value=row.get('backup');digest=row.get('originalSha256')
        if digest is None:return None
        file=Path(value or '').resolve()
        if not be.within(base,file) or not file.is_file() or be.sha256(file)!=digest:raise ValueError('Recovery backup missing or changed; older backups retained: '+str(file))
        return file
    def keep(slot,target,original,current,origin):
        final=base/slot/'Originals'/Path(target).relative_to(game)
        pending=slots[slot]/final.relative_to(base/slot)
        if original is not None:
            if origin is None:raise ValueError('Missing verified backup source: '+target)
            pending.parent.mkdir(parents=True,exist_ok=True)
            if be.within(game,origin):copy_verified(origin,pending,original)
            else:
                try:os.link(origin,pending)
                except OSError:copy_verified(origin,pending,original)
                if be.sha256(pending)!=original:raise ValueError('Recovery slot hash mismatch')
        return {'target':target,'backup':str(final) if original is not None else None,'originalSha256':original,'installedSha256':current}
    def carry_forges(name):
        # Runtime installs do not change Forges. Preserve their existing recovery
        # generations without reading live game archives or rehashing gigabytes.
        # Linking the immutable backup object preserves the exact same bytes.
        receipt=base/name/'INSTALL_TRANSACTION.json'
        doc=mm.load(receipt,{}) if receipt.is_file() else baseline_doc
        if doc.get('status') not in ('INSTALLED','RESTORED') or Path(doc['gameRoot']).resolve()!=game:
            raise ValueError('Forge recovery receipt is not ready; existing backups retained')
        carried_docs[name]=doc
        rows=[]
        for row in doc.get('files',[]):
            target=Path(row['target']).resolve();digest=row.get('originalSha256')
            if target.parent!=game or target.suffix.lower()!='.forge':raise ValueError('Unsafe Forge recovery target')
            if name=='Baseline' and digest!=baseline['hashes'].get(str(target)):raise ValueError('Backup baseline does not match verified game')
            if digest is not None:
                origin=Path(row.get('backup') or '').resolve()
                if not be.within(base/name,origin) or not origin.is_file():raise ValueError('Forge recovery backup missing; existing backups retained')
                pending=slots[name]/'Originals'/target.relative_to(game)
                pending.parent.mkdir(parents=True,exist_ok=True)
                os.link(origin,pending)
            rows.append(dict(row,backup=str(base/name/'Originals'/target.relative_to(game)) if digest is not None else None))
        return rows
    try:
        forge_rows={name:[] for name in slots};runtime_rows={name:[] for name in slots};live_hashes={}
        if runtime_only:
            for name in slots:forge_rows[name]=carry_forges(name)
        for row in ([] if runtime_only else baseline_doc.get('files',[])):
            target=str(Path(row['target']).resolve());file=Path(target)
            notify('Verifying recovery slots: '+file.name)
            if file.parent!=game or file.suffix.lower()!='.forge' or row['originalSha256']!=baseline['hashes'].get(target):raise ValueError('Backup baseline does not match verified game')
            live=be.sha256(file);live_hashes[target]=live
            if live!=row['installedSha256']:raise ValueError('Installed Forge changed; backup rotation stopped')
            forge_rows['Baseline'].append(keep('Baseline',target,row['originalSha256'],live,source(row)))
            previous=last_forges.get(target) if no_change else changes.get(target)
            digest=previous['originalSha256'] if previous else live
            origin=source(previous) if previous else file
            if not previous and manifest:
                candidate=mm.load(manifest,{})['outputs'].get(target,{})
                if candidate.get('stagedSha256')==live:origin=Path(candidate['staged'])
            forge_rows['LastWorking'].append(keep('LastWorking',target,digest,live,origin))
        # Existing baseline ownership is stable; a new runtime file follows its
        # explicit install chain back to its original bytes (or original absence).
        for target in sorted(set(old_rows)|set(config_rows)|ownership):
            file=Path(target).resolve()
            if file.is_file() and file.stat().st_size>=64*1024**2:notify('Verifying runtime recovery: '+file.name)
            if not runtime_history.safe_target(game,file):raise ValueError('Unsafe runtime backup target')
            current=be.sha256(file) if file.is_file() else None
            latest=config_rows.get(target);older=old_rows.get(target)
            historical=next((r for _,doc in history for r in doc.get('files',[]) if r['target']==target and r.get('installedSha256')==current),None)
            if not latest and not older and historical is None:raise ValueError('Historical runtime has unrecorded edits; backups retained: '+target)
            expected=latest.get('installedSha256') if latest else older.get('installedSha256') if older else current
            if current!=expected:raise ValueError('Runtime changed outside the recorded install; backups retained: '+target)
            row=older or latest or historical;origin=source(row);original=row.get('originalSha256')
            ordered=ordered_runtime_original(target,current,history) if not older else None
            if ordered is not None:row=ordered;original=row.get('originalSha256');origin=source(row)
            elif not older:
                visited=set()
                while original is not None:
                    matches=[r for _,doc in history for r in doc.get('files',[]) if r['target']==target and r.get('installedSha256')==original and r.get('originalSha256')!=original]
                    if not matches:break
                    if original in visited or len({r.get('originalSha256') for r in matches})!=1:raise ValueError('Ambiguous runtime baseline history; backups retained: '+target)
                    visited.add(original);row=matches[0];original=row.get('originalSha256');origin=source(row)
            runtime_rows['Baseline'].append(keep('Baseline',target,original,current,origin))
            prior_runtime=last_runtime.get(target) if no_change else latest
            previous=prior_runtime.get('originalSha256') if prior_runtime else current
            runtime_rows['LastWorking'].append(keep('LastWorking',target,previous,current,source(prior_runtime) if prior_runtime else file if current is not None else None))
        for name,folder in slots.items():
            config=base/name/'CONFIG_TRANSACTION.json'
            be.record_json(folder/'CONFIG_TRANSACTION.json',{'status':'INSTALLED','gameRoot':str(game),'files':runtime_rows[name],'mode':'TWO_SLOT_RUNTIME'})
            forge_doc=dict(carried_docs[name],files=forge_rows[name],runtimeTransaction=str(config)) if runtime_only else {'schema':1,'status':'INSTALLED','gameRoot':str(game),'files':forge_rows[name],'installed':[r['target'] for r in forge_rows[name]],'mode':'TWO_SLOT_'+name.upper(),'baselineId':baseline['baselineId'],'runtimeTransaction':str(config)}
            be.record_json(folder/'INSTALL_TRANSACTION.json',forge_doc)
        for name,folder in slots.items():
            final=base/name
            if final.exists():
                retired[name]=base/(name+'.retired-'+uuid.uuid4().hex[:6]);rename(final,retired[name])
            rename(folder,final);published.append(name)
        anchor=base/'Baseline/INSTALL_TRANSACTION.json'
        # Retained certificates need the current recovery receipt, not a deleted
        # historical directory. Their original C# content proofs are unchanged.
        stage=Path(manifest or cfg.get('last_stage_manifest') or '')
        if not runtime_only and stage.is_file() and be.within(work,stage):
            for file in stage.parent.rglob('BATCH_RESULT.json'):
                doc=mm.load(file,{});changed=False
                if doc.get('writer')!='FORGE_LAYERED_BATCH_V20':continue
                for target,row in doc.get('outputs',{}).items():
                    if row.get('previousSha256') and target in live_hashes:
                        row.update(previousTransaction=str(anchor),previousSha256=live_hashes[target]);row.pop('previousFoundationOnly',None);row.pop('previousDiagnosticTransaction',None);changed=True
                if changed:rewrites[file]=mm.load(file,{});be.record_json(file,doc)
            lp.inspect_stage(stage,game,verify_backups=True)
        cfg.update(last_transaction=str(anchor),last_actual_transaction=str(base/'LastWorking/INSTALL_TRANSACTION.json'),last_config_transaction=str(base/'LastWorking/CONFIG_TRANSACTION.json'),baseline_config_transaction=str(base/'Baseline/CONFIG_TRANSACTION.json'),backup_retention='BASELINE_AND_LAST_WORKING')
        if manifest:cfg['last_stage_manifest']=str(stage)
        for key,value in list(cfg.items()):
            if 'transaction' in key and isinstance(value,str) and be.within(base,value) and not any(be.within(base/name,value) for name in slots):
                cfg[key]=str(base/('LastWorking/CONFIG_TRANSACTION.json' if 'config' in key or 'runtime' in key else 'Baseline/INSTALL_TRANSACTION.json'))
        mm.save(mm.CONFIG,cfg)
    except Exception:
        for file,doc in rewrites.items():be.record_json(file,doc)
        mm.save(mm.CONFIG,original_cfg)
        for name in reversed(published):
            failure=slots[name];rename(base/name,failure)
            if name in retired:rename(retired[name],base/name);retired.pop(name)
        for name,folder in retired.items():
            if not (base/name).exists():rename(folder,base/name)
        raise
    finally:
        for folder in slots.values():
            if folder.exists() and folder.resolve().parent==base:shutil.rmtree(ws.native(folder))
    # Only committed transaction directories are disposable. Unknown/incomplete
    # recovery directories are left for explicit recovery, never guessed away.
    history_folder=work/'diagnostics/backup_history';history_folder.mkdir(parents=True,exist_ok=True)
    for folder in list(base.iterdir()):
        if not folder.is_dir() or folder.name in slots or folder.is_symlink() or folder.resolve()!=folder.absolute() or folder.resolve().parent!=base:continue
        docs=list(folder.glob('*TRANSACTION.json'))
        if not docs or any(mm.load(p,{}).get('status') not in ('INSTALLED','RESTORED','ROLLED_BACK','ROLLED_BACK_AFTER_FAILURE') for p in docs):continue
        if any(Path(mm.load(p,{})['gameRoot']).resolve()!=game for p in docs):continue
        for p in docs:be.record_json(history_folder/(folder.name+'_'+p.name),mm.load(p,{}))
        shutil.rmtree(ws.native(folder))
    return base/'Baseline/INSTALL_TRANSACTION.json'
