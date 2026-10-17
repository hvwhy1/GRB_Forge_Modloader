"""Consolidate verified baseline backups into one recovery receipt for incremental trials."""
import json
import os
from pathlib import Path
import time
import uuid
import batch_engine as be


def create(manifest,original_transaction,backups):
    manifest=Path(manifest).resolve();original_transaction=Path(original_transaction).resolve()
    state=json.loads(manifest.read_text(encoding='utf-8'))
    if state.get('status')!='VERIFIED_STAGED':raise ValueError('Recovery requires a verified stage')
    original=json.loads(original_transaction.read_text(encoding='utf-8'))
    game=Path(state['plan']['gameRoot']).resolve()
    if Path(original['gameRoot']).resolve()!=game:raise ValueError('Baseline backup belongs to another game')
    sources={r['target']:r for r in original['files']}
    checked=[]
    for target,entry in state['outputs'].items():
        file=Path(target).resolve();backup=Path(sources[target]['backup']).resolve()
        if file.parent!=game or file.suffix.lower()!='.forge' or not be.within(original_transaction.parent,backup):
            raise ValueError('Unsafe recovery mapping')
        if sources[target]['originalSha256']!=entry['sourceSha256'] or be.sha256(backup)!=entry['sourceSha256']:
            raise ValueError('Baseline backup does not match this stage')
        if be.sha256(file)!=entry['stagedSha256']:raise ValueError('Active game bytes do not match the ready batch')
        checked.append((file,backup,entry))
    folder=Path(backups).resolve()/('RECOVERY_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6])
    folder.mkdir(parents=True,exist_ok=False)
    tx={'schema':1,'status':'INSTALLED','mode':'COMPOSITE_VERIFIED_RECOVERY',
        'gameRoot':str(game),'batchManifest':str(manifest),'originalTransaction':str(original_transaction),
        'files':[],'installed':[],'gameplayVerified':False}
    for file,source,entry in checked:
        destination=folder/'Originals'/file.relative_to(game);destination.parent.mkdir(parents=True,exist_ok=True)
        # Same-volume links preserve the immutable, hash-checked baseline without
        # duplicating another 25 GiB. Restore still copies to a new pending file.
        try:os.link(source,destination)
        except OSError:be._copy_verify(source,destination,entry['sourceSha256'])
        tx['files'].append({'target':str(file),'backup':str(destination),
            'originalSha256':entry['sourceSha256'],'installedSha256':entry['stagedSha256']})
        tx['installed'].append(str(file))
    path=folder/'INSTALL_TRANSACTION.json';be.record_json(path,tx)
    return path


def create_layered(manifest,installation,backups):
    """Keep one clean-baseline rollback after replacing an earlier modded build."""
    import deployment_policy
    state=json.loads(Path(manifest).read_text(encoding='utf-8'))
    baseline=deployment_policy.require_ready(state['plan']['gameRoot'])
    txfile=Path(installation).resolve();tx=json.loads(txfile.read_text(encoding='utf-8'))
    current={r['target']:(r,txfile) for r in tx['files']}
    checked=[]
    for target,entry in state['outputs'].items():
        if be.sha256(target)!=entry['stagedSha256']:raise ValueError('Installed layered output changed before recovery recording')
        chosen=current.get(target)
        if not chosen or chosen[0]['originalSha256']!=baseline['hashes'][target]:
            prior=Path(entry.get('previousTransaction','')).resolve()
            document=json.loads(prior.read_text(encoding='utf-8'))
            matches=[r for r in document['files'] if r['target']==target and r['originalSha256']==baseline['hashes'][target]]
            if len(matches)!=1:raise ValueError('No clean baseline recovery for '+target)
            chosen=(matches[0],prior)
        row,receipt=chosen;source=Path(row['backup']).resolve()
        if not be.within(receipt.parent,source) or be.sha256(source)!=baseline['hashes'][target]:raise ValueError('Baseline recovery bytes changed')
        checked.append((target,entry,source))
    # A partial install must retain originals for untouched modified Forges.
    import layered_pipeline as lp
    import mod_manager as mm
    game=Path(state['plan']['gameRoot']).resolve();cfg=mm.load(mm.CONFIG,{})
    history=lp.receipt_history(game,[str(txfile),cfg.get('last_transaction'),cfg.get('last_actual_transaction')]+[
        row.get('previousTransaction') for row in state['outputs'].values()])
    for target,digest in baseline['hashes'].items():
        if target in state['outputs']:continue
        live=be.sha256(target)
        if live==digest:continue
        row,receipt,source=lp.clean_history_entry(history,target,digest,live)
        if row.get('installedSha256')!=live:
            raise ValueError('Untouched Forge does not match its recorded installation: '+target)
        checked.append((target,{'sourceSha256':digest,'stagedSha256':live},source))
    folder=Path(backups).resolve()/('RECOVERY_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]);folder.mkdir(parents=True)
    recovery={'schema':1,'status':'INSTALLED','mode':'LAYERED_VERIFIED_RECOVERY','gameRoot':state['plan']['gameRoot'],
              'batchManifest':str(Path(manifest).resolve()),'files':[],'installed':[],'gameplayVerified':False}
    if tx.get('runtimeTransaction'):recovery['runtimeTransaction']=tx['runtimeTransaction']
    recovery['installationTransaction']=str(txfile)
    for target,entry,source in checked:
        destination=folder/'Originals'/Path(target).name;destination.parent.mkdir(parents=True,exist_ok=True)
        try:os.link(source,destination)
        except OSError:be._copy_verify(source,destination,entry['sourceSha256'])
        recovery['files'].append({'target':target,'backup':str(destination),'originalSha256':entry['sourceSha256'],'installedSha256':entry['stagedSha256']})
        recovery['installed'].append(target)
    path=folder/'INSTALL_TRANSACTION.json';be.record_json(path,recovery)
    import backup_slots
    return backup_slots.rotate(manifest,installation,backups,path)
