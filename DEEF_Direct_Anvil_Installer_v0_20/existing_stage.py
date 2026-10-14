"""Read-only validation and reuse of previously verified streaming-stage batches.

Intentionally never rebuilds, remaps, downloads, or modifies the installed game.
The mutating operation is exclusively batch_engine.install_staged with its existing
SHA-256, backup, disk-capacity, process, and atomic-replacement checks.
"""
import collections
import json
from pathlib import Path
import batch_engine as be

REQUIRED_RECEIPT_FLAGS = (
    'verified', 'originalSourceUnchanged', 'prefetchBytesPreserved',
    'untouchedBytesVerified', 'independentAnvilIndexVerified',
    'allRequestedEditsVerified',
)


def _verify_combined_receipt(record, path, source, entry, route_count, game):
    """Validate an addition receipt chain rooted in a separately verified batch."""
    if record.get('schema')!='DEEF.combined_stage_receipt.1':return False
    addition=record.get('additionChain')
    base=addition.get('baseManifest') if isinstance(addition,dict) else None
    receipts=addition.get('receipts') if isinstance(addition,dict) else None
    if not base or not isinstance(receipts,list) or not receipts:return False
    base_path=Path(base).resolve()
    if base_path==path or not be.within(path.parent,base_path):return False
    base_summary=inspect_existing_stage(base_path,expected_game=game)
    base_state=json.loads(base_path.read_text(encoding='utf-8'))
    base_entry=base_state.get('outputs',{}).get(str(source))
    if not base_entry:return False
    expected_routes=sum(1 for route in base_state.get('finishedRoutes',[])
                        if str(Path(route.get('forge','')).resolve()).casefold()==str(source).casefold())
    if route_count!=expected_routes:return False
    current=Path(base_entry['staged']).resolve()
    current_hash=str(base_entry['stagedSha256']).casefold()
    pending=list(receipts);used=[]
    while pending:
        found=None
        for index,receipt_path in enumerate(pending):
            receipt_file=Path(receipt_path).resolve()
            if not be.within(path.parent,receipt_file) or not receipt_file.is_file():continue
            try:step=json.loads(receipt_file.read_text(encoding='utf-8'))
            except Exception:continue
            if (str(Path(step.get('sourceForge','')).resolve()).casefold()==str(current).casefold()
                and str(step.get('sourceSha256','')).casefold()==current_hash):
                found=(index,receipt_file,step);break
        if found is None:return False
        index,receipt_file,step=found
        if (step.get('verified') is not True or step.get('eligibleForDeployment') is not True
            or step.get('independentAnvilIndexVerified') is not True
            or step.get('sourceUnchanged') is not True or step.get('gameModified') is not False
            or not isinstance(step.get('addedEntries'),int) or step['addedEntries']<1):return False
        output=Path(step.get('output','')).resolve()
        output_hash=str(step.get('outputSha256','')).casefold()
        if not be.within(path.parent,output) or len(output_hash)!=64:return False
        if output.is_file():
            if be.sha256(output).casefold()!=output_hash:return False
        else:
            # The staging pipeline may remove an intermediate private Forge
            # after the next verified step consumes it. Preserve chain proof by
            # requiring the next receipt to name this exact path and hash.
            consumed=any(
                str(Path(json.loads(Path(p).read_text(encoding='utf-8')).get('sourceForge','')).resolve()).casefold()==str(output).casefold()
                and str(json.loads(Path(p).read_text(encoding='utf-8')).get('sourceSha256','')).casefold()==output_hash
                for p in pending if Path(p).is_file()
            )
            if not consumed:return False
        current,current_hash=output,output_hash
        used.append(str(receipt_file));pending.pop(index)
        if str(current).casefold()==str(Path(entry['staged']).resolve()).casefold():break
    return (not pending and len(used)==len(receipts)
            and str(current).casefold()==str(Path(entry['staged']).resolve()).casefold()
            and current_hash==str(entry.get('stagedSha256','')).casefold()
            and record.get('requestedEdits')==route_count
            and record.get('stagedForgeSha256','').casefold()==current_hash
            and all(record.get(k) is True for k in REQUIRED_RECEIPT_FLAGS))

def inspect_existing_stage(manifest, expected_game=None):
    """Validate receipt provenance and accounting without rehashing 25+GiB.

    install_staged() independently SHA-256 hashes the original and staged bytes
    immediately prior to copying, and also verifies each backup and pending file.
    """
    path=Path(manifest).resolve()
    state=json.loads(path.read_text(encoding='utf-8'))
    if state.get('writer')=='FORGE_LAYERED_BATCH_V20':
        import layered_pipeline
        return layered_pipeline.inspect_stage(path,expected_game)
    if state.get('schema')!=3 or state.get('writer')!='FORGE_STREAM_BATCH_V27':
        raise ValueError('This is not a v0.19 streaming Forge batch')
    if state.get('status')!='VERIFIED_STAGED':
        raise ValueError('Batch is not VERIFIED_STAGED; refusing installation')
    plan=state.get('plan')
    if not isinstance(plan,dict) or not plan.get('gameRoot'):
        raise ValueError('Batch is missing its recorded game root')
    game=Path(plan['gameRoot']).resolve()
    if expected_game is not None and game!=Path(expected_game).resolve():
        raise ValueError('Batch belongs to a different game folder')
    if not (game/'GRB.exe').is_file():
        raise ValueError('Original game folder does not contain GRB.exe')
    outputs=state.get('outputs')
    finished=state.get('finishedRoutes')
    excluded=state.get('excludedRoutes')
    if not isinstance(outputs,dict) or not outputs or not isinstance(finished,list) or not finished:
        raise ValueError('Incomplete or empty staging manifest')
    if not isinstance(excluded,list) or len(outputs)>100 or len(finished)>10000:
        raise ValueError('Invalid batch route counts')
    if len(finished)+len(excluded)!=len(plan.get('routes',[])):
        raise ValueError('Batch route accounting does not match the approved plan')
    routes_by_forge=collections.Counter(str(Path(x['forge']).resolve()) for x in finished)
    if set(outputs)!=set(routes_by_forge):
        raise ValueError('Batch outputs do not match its accepted routes')
    details=[]
    for source,entry in outputs.items():
        source_file=Path(source).resolve()
        if source_file.parent!=game or source_file.suffix.lower()!='.forge' or not source_file.is_file():
            raise ValueError('Unsafe or missing game Forge: '+source)
        if not isinstance(entry,dict) or str(Path(entry.get('relative','')).name)!=source_file.name:
            raise ValueError('Invalid Forge mapping in batch: '+source)
        staged=Path(entry['staged']).resolve()
        receipt=Path(entry['streamReceipt']).resolve()
        if not staged.is_file() or not be.within(path.parent,staged):
            raise ValueError('Missing or relocated staged Forge: '+str(staged))
        if not receipt.is_file() or not be.within(path.parent,receipt):
            raise ValueError('Missing or relocated verification receipt: '+str(receipt))
        record=json.loads(receipt.read_text(encoding='utf-8'))
        if record.get('schema')=='DEEF.combined_stage_receipt.1':
            if not _verify_combined_receipt(record,path,source,entry,routes_by_forge[source],game):
                raise ValueError('Combined addition chain failed verification: '+str(receipt))
        elif any(record.get(k) is not True for k in REQUIRED_RECEIPT_FLAGS):
            raise ValueError('A streaming verification proof is missing: '+str(receipt))
        if record.get('requestedEdits')!=routes_by_forge[source]:
            raise ValueError('Receipt does not contain expected edit count: '+str(receipt))
        if str(record.get('stagedForgeSha256','')).lower()!=str(entry['stagedSha256']).lower():
            raise ValueError('Staged hash in receipt does not match batch manifest')
        if str(entry.get('sourceSha256','')).lower()!=str(plan.get('sourceHashes',{}).get(source,'')).lower():
            raise ValueError('Original source hash differs from characterization')
        if not all(len(str(entry[k]))==64 and all(c in '0123456789abcdefABCDEF' for c in str(entry[k]))
                   for k in ('sourceSha256','stagedSha256')):
            raise ValueError('Invalid SHA-256 fingerprint in staging manifest')
        details.append({'forge':source_file.name,'routes':routes_by_forge[source],
                        'sourceBytes':source_file.stat().st_size,
                        'stagedBytes':staged.stat().st_size})
    return {'manifest':str(path),'gameRoot':str(game), 'acceptedEdits':len(finished),
            'excludedEdits':len(excluded),'forges':details,
            'note':'Receipt metadata checked. Full source/staged SHA-256 rechecked by install_staged before writing.'}


def install_existing(manifest, backup_root, *, expected_game=None,
                     game_check=be.game_processes_running):
    summary=inspect_existing_stage(manifest,expected_game=expected_game)
    transaction=be.install_staged(summary['manifest'],backup_root,game_check=game_check)
    state=json.loads(Path(manifest).read_text())
    if state.get('runtimeManifest'):
        import runtime_stage
        try:
            receipt=runtime_stage.install(state['runtimeManifest'],summary['gameRoot'],Path(manifest).parent,backup_root,game_check=game_check)
            tx=json.loads(Path(transaction).read_text());tx['runtimeTransaction']=str(receipt);be.record_json(transaction,tx)
        except Exception:
            be.restore_install(transaction,game_check=game_check);raise
    return summary,transaction


def find_existing_stages(current_root, *, expected_game=None):
    """Locate *valid* prior verified streaming batches; do not auto-install one.

    Search only known installer layouts near this project, on the Desktop, and
    in previous DEEF project folders. No recursive whole-disk traversal.
    Each candidate must pass inspect_existing_stage before being offered.
    """
    root=Path(current_root).resolve()
    parents=[root,root.parent,root.parent.parent,Path.home()/'Desktop',
             Path.home()/'Desktop'/'GRB_MODDING',Path.home()/'Desktop'/'GRBmod']
    manifests=set()
    patterns=('workspace/auto_runs/*/batch/BATCH_RESULT.json',
              'workspace/batch/BATCH_RESULT.json')
    for base in parents:
        if not base.is_dir():continue
        for pattern in patterns:
            manifests.update(base.glob(pattern))
        for installer in base.glob('DEEF_Direct_Anvil_Installer_v*'):
            if installer.is_dir():
                for pattern in patterns:
                    manifests.update(installer.glob(pattern))
    candidates=[]
    for path in sorted(manifests,key=lambda p:p.stat().st_mtime,reverse=True)[:100]:
        try:
            summary=inspect_existing_stage(path,expected_game=expected_game)
        except (OSError,ValueError,KeyError,TypeError,IndexError,RuntimeError,UnicodeError):
            continue
        candidates.append(summary)
    return candidates
