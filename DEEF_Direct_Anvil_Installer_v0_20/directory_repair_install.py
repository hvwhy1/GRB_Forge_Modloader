"""Transactional deployment of proved, payload-preserving GRB file-set repairs."""
from pathlib import Path
import json,os,time,uuid
import batch_engine as be
import deployment_policy as policy

NAMES={'DataPC_patch_01.forge','DataPC_Resources_patch_01.forge','DataPC_extra_patch_01.forge'}

def install(proofs,game,backups,game_check=be.game_processes_running):
    game=Path(game).resolve();backups=Path(backups).resolve();policy.require_ready(game)
    if game_check():raise RuntimeError('Close Breakpoint before repairing file sets')
    if be.within(game,backups) or be.within(backups,game):raise ValueError('Backups must be outside game')
    rows=[];seen=set()
    for proof_path in proofs:
        proof_path=Path(proof_path).resolve();p=json.loads(proof_path.read_text())
        if not all(p.get(k) is True for k in ('verified','sourceUnchanged','independentAnvilIndexVerified','allPayloadAndEntryBytesPreserved','populatedGlobalIndexesConsecutive')):raise ValueError('Incomplete file-set repair proof')
        out=Path(p['output']).resolve();source=Path(p['sourceForge']).resolve()
        if not be.within(proof_path.parent,out) or source.parent!=game or source.name not in NAMES or out.name!=source.name or source.name in seen:raise ValueError('Unsafe repair mapping')
        if be.sha256(source)!=p['sourceSha256'] or be.sha256(out)!=p['outputSha256']:raise ValueError('Repair input or output changed')
        seen.add(source.name);rows.append((source,out,p))
    if not rows:raise ValueError('No repair proofs')
    be._space_check(game,max(out.stat().st_size for _,out,_ in rows)+512*1024**2,'File-set repair staging')
    be._space_check(backups,sum(src.stat().st_size for src,_,_ in rows)+512*1024**2,'File-set repair backups')
    folder=backups/('RANGES_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]);folder.mkdir(parents=True)
    tx={'schema':1,'status':'PREPARING','gameRoot':str(game),'diagnosticOnly':True,'label':'Consecutive populated file-set indexes','files':[],'installed':[],'gameplayVerified':False}
    receipt=folder/'INSTALL_TRANSACTION.json'
    for source,out,p in rows:
        backup=folder/'Originals'/source.name;be._copy_verify(source,backup,p['sourceSha256'])
        tx['files'].append({'target':str(source),'backup':str(backup),'originalSha256':p['sourceSha256'],'installedSha256':p['outputSha256']})
    tx['status']='INSTALLING';be.record_json(receipt,tx)
    try:
        for source,out,p in rows:
            pending=source.with_name(source.name+'.deef-ranges-'+uuid.uuid4().hex[:6])
            try:
                be._copy_verify(out,pending,p['outputSha256'])
                if game_check() or be.sha256(source)!=p['sourceSha256']:raise RuntimeError('Live state changed during repair')
                os.replace(pending,source);tx['installed'].append(str(source));be.record_json(receipt,tx)
            finally:pending.unlink(missing_ok=True)
        tx['status']='INSTALLED';be.record_json(receipt,tx)
    except Exception:
        be.record_json(receipt,tx);be.restore_install(receipt,game_check=game_check);raise
    return receipt
