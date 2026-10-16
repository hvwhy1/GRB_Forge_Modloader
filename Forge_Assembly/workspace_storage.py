"""Bounded generated storage. Reports survive; disposable binary scratch does not."""
import contextlib,json,os,shutil,stat,threading,time,uuid,hashlib,collections,re,math
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

GIB=1024**3
DEFAULT_LIMIT_GIB=200
MANAGED={'auto_runs','content_cache','payloads','diagnostics','diagnostic_runs',
 'complete_content_trials','private_fixtures','private_validation','weapon_asset_trials',
 'weapon_composition_trials','weapon_merge_research','camera_research','reshade_stages',
 'additional_options_review','resolver_reports','runs'}
TEXT={'.json','.txt','.log','.csv','.md','.py','.cs','.ps1','.cmd','.ini','.toml'}
_lock=threading.RLock();_local=threading.local()

class StorageError(RuntimeError):pass

def inside(root,path):
    try:Path(path).resolve().relative_to(Path(root).resolve());return True
    except (ValueError,OSError):return False

def reparse(entry):
    return bool(entry.stat(follow_symlinks=False).st_file_attributes & 0x400) if os.name=='nt' else entry.is_symlink()

@contextlib.contextmanager
def lease(work):
    """Cross-process exclusion; a crashed process releases the OS lock."""
    work=Path(work).resolve();work.mkdir(parents=True,exist_ok=True)
    with _lock:
        if getattr(_local,'depth',0):
            if _local.work!=work:raise StorageError('Cannot nest operations for different workspaces.')
            _local.depth+=1
            try:yield
            finally:_local.depth-=1
            return
        handle=(work/'storage.lock').open('a+b')
        try:
            if os.fstat(handle.fileno()).st_size==0:handle.write(b'0');handle.flush()
            handle.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            handle.close();raise StorageError('Another installer operation is using this workspace. Wait for it to finish before cleaning or staging.')
        _local.depth=1;_local.work=work
        try:yield
        finally:
            _local.depth=0
            if os.name=='nt':
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle,fcntl.LOCK_UN)
            handle.close()

def native(path):
    value=os.path.abspath(path)
    return ('\\\\?\\'+value if os.name=='nt' and not value.startswith('\\\\?\\') else value)

def path_key(path):
    value=str(path)
    if value.startswith('\\\\?\\'):value=value[4:]
    return os.path.normcase(value)

def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))

def protected_files(work,cfg,extra_stages=()):
    """Keep deployable outputs, their input view and pinned archive provenance.

    Backup/saved-setup trees are outside the disposable allowlist. Historical
    receipts are kept as text even when their old staging payloads are retired.
    """
    work=Path(work).resolve();keep=set();stages=[]
    def protect(value):
        if value and isinstance(value,str) and inside(work,value):keep.add(os.path.normcase(str(Path(value).resolve())))
    def stage(value):
        if not value or value in stages:return
        path=Path(value)
        if not inside(work,path) or not path.is_file():return
        doc=read(path);stages.append(str(path.resolve()));protect(str(path))
        if doc.get('status')!='VERIFIED_STAGED':raise StorageError('A retained stage is incomplete; cleanup stopped: '+str(path))
        for row in doc.get('outputs',{}).values():protect(row.get('staged'))
        for layer in doc.get('foundationLayers',[])+doc.get('postLayers',[]):
            protect(layer.get('archive'))
            for archive in layer.get('sourceArchives',{}):protect(archive)
        for row in doc.get('plan',{}).get('routes',[]):protect(row.get('archive'))
        for row in doc.get('presentPayloads',[])+doc.get('installedPayloads',[]):protect(row.get('archive'))
        if doc.get('resourceManifest'):
            resource=read(doc['resourceManifest'])
            protect(str(Path(resource['plan']['gameRoot'])/'GRB.exe'))
            for source in resource.get('outputs',{}):protect(source)
            stage(doc['resourceManifest'])
        if doc.get('runtimeManifest'):
            for row in read(doc['runtimeManifest']).get('files',[]):
                if not row.get('remove'):protect(row.get('source'))
    # One top-level stage owns its resource/runtime dependencies. Confirmation
    # history retains receipts, not a second installable copy.
    candidates=[value for value in extra_stages if value]
    selected=candidates[-1] if candidates else (cfg.get('last_stage_manifest') or cfg.get('last_confirmed_archive_manifest'))
    stage(selected)
    # Diagnostic transactions can keep originals outside the normal backup tree.
    for key,value in cfg.items():
        if not isinstance(value,str) or not value.lower().endswith('.json') or not inside(work,value) or not Path(value).is_file():continue
        if 'transaction' not in key:continue
        doc=read(value)
        for row in doc.get('files',[]):protect(row.get('backup'))
        if doc.get('diagnosticOnly') and doc.get('proof'):
            proof=Path(doc['proof'])
            if inside(work,proof) and proof.is_file():protect(read(proof).get('output'))
    return keep,stages

def units(work):
    work=Path(work).resolve()
    for entry in os.scandir(work):
        if not entry.is_dir(follow_symlinks=False) or reparse(entry):continue
        if entry.name in MANAGED or entry.name.startswith(('package_smoke','git_source_smoke_')):
            yield Path(entry.path)

def prune(work,roots,keep,log=lambda _:None,dry_run=False,*,retire_backups=False):
    """Never follow a junction/symlink, never change a hard-linked file's bytes."""
    work=Path(work).resolve();checked=[]
    allowed=list(units(work))
    if retire_backups:allowed.append(work/'backups')
    for root in roots:
        root=Path(root).absolute()
        if not any(root==p or p in root.parents for p in allowed) or root.resolve()!=root or not inside(work,root):raise StorageError('Unsafe cleanup root: '+str(root))
        checked.append(root)
    result={'removedFiles':0,'removedLogicalBytes':0,'errors':[],'skippedLinks':0};mutex=threading.Lock()
    def walk(folder,recurse=True):
        n=size=links=0;errors=[]
        with os.scandir(native(folder)) as entries:
            for e in entries:
                try:
                    if reparse(e):links+=1;continue
                    if e.is_dir(follow_symlinks=False):
                        if not recurse:continue
                        # Originals/Backups remain recoverable even in old diagnostic trials.
                        if not retire_backups and e.name.casefold() in ('originals','backups'):continue
                        a,b,c,d=walk(e.path);n+=a;size+=b;links+=c;errors.extend(d)
                        if not dry_run:
                            try:os.rmdir(e.path)
                            except OSError:pass
                    elif path_key(e.path) not in keep and Path(e.name).suffix.casefold() not in TEXT:
                        length=e.stat(follow_symlinks=False).st_size
                        if not dry_run:os.unlink(e.path)
                        n+=1;size+=length
                        if n%25000==0:log('Cleaning generated files: '+str(folder)+' ('+str(n)+' files)')
                except OSError as exc:
                    if len(errors)<20:errors.append(str(exc))
        return n,size,links,errors
    def task(root):
        log(('Checking ' if dry_run else 'Cleaning ')+root.name)
        a,b,c,d=walk(root)
        with mutex:
            result['removedFiles']+=a;result['removedLogicalBytes']+=b;result['skippedLinks']+=c;result['errors'].extend(d)
        log(root.name+': '+str(a)+' disposable files'+(' found' if dry_run else ' removed'))
    jobs=[]
    for root in checked:
        a,b,c,d=walk(root,False)
        result['removedFiles']+=a;result['removedLogicalBytes']+=b;result['skippedLinks']+=c;result['errors'].extend(d)
        for e in os.scandir(root):
            if e.is_dir(follow_symlinks=False) and not reparse(e) and (retire_backups or e.name.casefold() not in ('originals','backups')):jobs.append(Path(e.path))
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(task,jobs))
    return result

def limit_bytes(cfg):
    value=float(cfg.get('workspace_limit_gib',DEFAULT_LIMIT_GIB))
    if not math.isfinite(value) or not 50<=value<=500:raise StorageError('Workspace storage budget must be between 50 and 500 GiB.')
    return int(value*GIB)

def backup_roots_to_retire(work,cfg,stages=()):
    """Retain referenced history plus the two newest Forge rollback sets."""
    work=Path(work).resolve();base=work/'backups';keep=set();seen=set()
    pending=[v for k,v in cfg.items() if isinstance(v,str) and ('transaction' in k or 'manifest' in k)]+list(stages)
    def links(value,key=''):
        if isinstance(value,dict):
            for k,v in value.items():yield from links(v,k)
        elif isinstance(value,list):
            for v in value:yield from links(v,key)
        elif isinstance(value,str) and key.casefold().endswith(('transaction','manifest')) and value.lower().endswith('.json'):yield value
    while pending:
        p=Path(pending.pop())
        if not inside(work,p) or not p.is_file():continue
        p=p.resolve()
        if p in seen:continue
        if len(seen)>4096:raise StorageError('Recovery history is too large to clean automatically.')
        seen.add(p);doc=read(p)
        if inside(base,p):keep.add(p.relative_to(base).parts[0])
        for row in doc.get('files',[]) if isinstance(doc,dict) else []:
            path=row.get('backup')
            if path and inside(base,path):keep.add(Path(path).resolve().relative_to(base).parts[0])
        pending.extend(links(doc))
    candidates=[];forge_sets=[]
    if not base.is_dir():return []
    for entry in os.scandir(base):
        if not entry.is_dir(follow_symlinks=False) or reparse(entry):continue
        folder=Path(entry.path);match=re.search(r'\d{8}_\d{6}',entry.name)
        receipts=list(folder.glob('*TRANSACTION.json'))
        if not match or not receipts:continue
        docs=[read(p) for p in receipts]
        if any(d.get('status') not in ('INSTALLED','RESTORED','ROLLED_BACK_AFTER_FAILURE') for d in docs):continue
        if any(str(r.get('target','')).lower().endswith('.forge') for d in docs for r in d.get('files',[])):forge_sets.append((match.group(),entry.name))
        # Preserve recent sets even if a caller has not saved its new receipt yet.
        if time.time()-max(p.stat().st_mtime for p in receipts)<86400:keep.add(entry.name)
        candidates.append(folder)
    keep.update(name for _,name in sorted(forge_sets,reverse=True)[:2])
    return [p for p in candidates if p.name not in keep]

def deduplicate_backups(work,log=lambda _:None):
    """Share only byte-identical, SHA-256 verified immutable backup files."""
    work=Path(work).resolve();base=work/'backups';groups=collections.defaultdict(set)
    if base.resolve()!=base or not inside(work,base):raise StorageError('Unsafe rollback backup directory')
    for receipt in base.glob('*/*TRANSACTION.json'):
        for row in read(receipt).get('files',[]):
            path=row.get('backup');digest=row.get('originalSha256','')
            if not path or len(digest)!=64 or not inside(base,path) or not Path(path).is_file():continue
            # No reparse-point path is eligible, even when it resolves inside.
            p=Path(path).absolute()
            if p.resolve()!=p:continue
            if os.name=='nt' and any(q.stat(follow_symlinks=False).st_file_attributes & 0x400 for q in (p,*p.parents) if inside(base,q)):continue
            groups[(digest.lower(),p.stat().st_size)].add(p)
    changed=0
    def signature(p):
        s=p.stat();return s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns
    def digest(p):
        h=hashlib.sha256()
        with p.open('rb') as f:
            for block in iter(lambda:f.read(4*1024**2),b''):h.update(block)
        return h.hexdigest()
    for (expected,size),paths in groups.items():
        identities=collections.defaultdict(list)
        for p in paths:identities[signature(p)[:2]].append(p)
        if len(identities)<2:continue
        source=next(iter(identities.values()))[0];source_sig=signature(source)
        log(f'Checking duplicate rollback backups ({size/GIB:.2f} GiB each)')
        if digest(source)!=expected:raise StorageError('Rollback backup failed verification; cleanup stopped: '+str(source))
        for identity,copies in list(identities.items())[1:]:
            original=copies[0];before=signature(original)
            if digest(original)!=expected:raise StorageError('Rollback backup failed verification; cleanup stopped: '+str(original))
            for p in copies:
                if signature(p)!=before or signature(source)!=source_sig:raise StorageError('Rollback backup changed during cleanup')
                temp=p.with_name(p.name+'.dedup-'+uuid.uuid4().hex)
                try:os.link(source,temp);os.replace(temp,p);changed+=1
                finally:temp.unlink(missing_ok=True)
    return changed

def usage(work):
    """Count each file identity once; do not confuse link counts with disk use."""
    seen=set();total=count=0;pending=[native(Path(work).resolve())]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for e in entries:
                if reparse(e):continue
                if e.is_dir(follow_symlinks=False):pending.append(e.path);continue
                # DirEntry.stat() reports zero file identities on Windows.
                # os.stat() opens the metadata handle needed for the NTFS ID.
                s=os.stat(e.path,follow_symlinks=False);key=(s.st_dev,s.st_ino) if s.st_ino else ('path',e.path)
                if key not in seen:seen.add(key);total+=s.st_size;count+=1
    return {'uniqueBytes':total,'uniqueFiles':count}

def cleanup(work,cfg,extra_stages=(),log=lambda _:None,dry_run=False):
    work=Path(work).resolve()
    with lease(work):
        keep,stages=protected_files(work,cfg,extra_stages)
        # Validate before deleting anything. These checks read receipts/runtime
        # bytes, not every multi-gigabyte Forge again.
        import existing_stage
        def validate(stage):
            try:existing_stage.inspect_existing_stage(stage);return None
            except ValueError as exc:
                # Older confirmed stages can reference a runtime DLL since
                # replaced by a newer config install. Preserve their payloads;
                # do not mistake a pre-existing live-runtime difference for
                # damage caused by cleanup.
                if str(exc).startswith('Separate installed payload changed:'):return str(exc)
                raise
        validation={stage:validate(stage) for stage in stages}
        signatures={p:(os.stat(p).st_size,os.stat(p).st_mtime_ns) for p in keep if os.path.isfile(p)}
        before=shutil.disk_usage(work).free
        result=prune(work,list(units(work)),keep,log,dry_run)
        if not dry_run:
            cache=work/'content_cache/INDEX.json'
            if cache.exists():cache.write_text('{}',encoding='utf-8')
            for p,signature in signatures.items():
                if not os.path.isfile(p) or (os.stat(p).st_size,os.stat(p).st_mtime_ns)!=signature:raise StorageError('A protected file changed during cleanup: '+p)
            for stage in stages:
                if validate(stage)!=validation[stage]:raise StorageError('Retained stage validation changed during cleanup: '+stage)
            retired=backup_roots_to_retire(work,cfg,stages)
            retired_result=prune(work,retired,set(),log,retire_backups=True) if retired else {'removedFiles':0,'removedLogicalBytes':0,'errors':[]}
            result['retiredBackupSets']=[str(p) for p in retired]
            result['removedFiles']+=retired_result['removedFiles'];result['removedLogicalBytes']+=retired_result['removedLogicalBytes'];result['errors']+=retired_result['errors']
            result['deduplicatedBackupPaths']=deduplicate_backups(work,log)
        result.update(dryRun=dry_run,retainedStages=stages,preexistingValidationNotes=validation,freeSpaceChangeBytes=shutil.disk_usage(work).free-before,**usage(work))
        report=work/'storage_reports'/('CLEANUP_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]+'.json')
        report.parent.mkdir(exist_ok=True);report.write_text(json.dumps(result,indent=2),encoding='utf-8')
        result['report']=str(report)
        log('Storage cleanup complete: '+str(result['removedFiles'])+' files; '+f"{result['uniqueBytes']/GIB:.1f} GiB of distinct workspace files remain.")
        return result
