"""Backed-up, single-patch compatibility trials; never a production batch."""
import argparse,json,os,time,uuid
from pathlib import Path
import batch_engine as be
import deployment_policy as policy

def install(proof_file,expected_live,label):
    proof_file=Path(proof_file).resolve()
    if not be.within(policy.WORK.resolve(),proof_file):raise ValueError('Proof outside workspace')
    proof=json.loads(proof_file.read_text(encoding='utf-8'))
    if not all(proof.get(k) is True for k in ('verified','sourceUnchanged','independentAnvilIndexVerified')):raise ValueError('Incomplete independent proof')
    candidate=Path(proof['output']).resolve()
    if not be.within(proof_file.parent,candidate) or candidate.name!='DataPC_patch_01.forge':raise ValueError('Not a private patch candidate')
    game=Path(policy.require_ready()['gameRoot']).resolve();target=game/candidate.name
    if be.game_processes_running():raise RuntimeError('Close the game before patch isolation')
    if be.sha256(target)!=expected_live or be.sha256(candidate)!=proof['outputSha256']:raise RuntimeError('Live patch or verified candidate changed')
    be._space_check(game,target.stat().st_size+candidate.stat().st_size+512*1024**2,'Patch isolation')
    folder=policy.WORK/'diagnostics'/('TRIAL_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]);folder.mkdir()
    backup=folder/'Originals'/target.name
    be._copy_verify(target,backup,expected_live)
    tx={'schema':1,'status':'INSTALLING','gameRoot':str(game),'diagnosticOnly':True,'label':label,'proof':str(proof_file),'gameplayVerified':False,
        'files':[{'target':str(target),'backup':str(backup),'originalSha256':expected_live,'installedSha256':proof['outputSha256']}],'installed':[]}
    receipt=folder/'INSTALL_TRANSACTION.json';be.record_json(receipt,tx)
    pending=target.with_name(target.name+'.deef-trial-'+uuid.uuid4().hex[:8])
    try:
        be._copy_verify(candidate,pending,proof['outputSha256'])
        if be.game_processes_running() or be.sha256(target)!=expected_live:raise RuntimeError('Live state changed while preparing trial')
        os.replace(pending,target);tx['installed']=[str(target)]
        if be.sha256(target)!=proof['outputSha256']:raise IOError('Installed hash mismatch')
        tx['status']='INSTALLED';be.record_json(receipt,tx)
    except Exception:
        # Keep the recoverable receipt for restore_install; never hide a failed swap.
        be.record_json(receipt,tx);raise
    finally:pending.unlink(missing_ok=True)
    import mod_manager as mm
    cfg=mm.load(mm.CONFIG,{});cfg['last_isolation_transaction']=str(receipt);mm.save(mm.CONFIG,cfg)
    return receipt

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--proof',required=True);p.add_argument('--live-sha',required=True);p.add_argument('--label',required=True)
    a=p.parse_args();print(install(a.proof,a.live_sha,a.label),flush=True)
