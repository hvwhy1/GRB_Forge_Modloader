"""Derive smaller Forge-group trials from a complete verified stage, without rebuilding bytes."""
import copy
import json
from pathlib import Path
import batch_engine as be


def select_forges(manifest,names,label):
    manifest=Path(manifest).resolve()
    state=json.loads(manifest.read_text(encoding='utf-8'))
    if state.get('status')!='VERIFIED_STAGED' or state.get('privateOnly'):
        raise ValueError('Select a complete verified replacement stage')
    selected={k for k in state['outputs'] if Path(k).name in set(names)}
    if not selected:raise ValueError('No selected Forge groups found in this stage')
    trial=copy.deepcopy(state)
    trial['outputs']={k:v for k,v in state['outputs'].items() if k in selected}
    trial['finishedRoutes']=[r for r in state['finishedRoutes'] if r['forge'] in selected]
    trial['excludedRoutes']=[r for r in state.get('excludedRoutes',[]) if r['forge'] in selected]
    trial['plan']['routes']=[r for r in state['plan']['routes'] if r['forge'] in selected]
    trial['plan']['sourceHashes']={k:v for k,v in state['plan']['sourceHashes'].items() if k in selected}
    trial['diagnosticGroup']=label
    trial['parentManifest']=str(manifest)
    target=manifest.parent/('DIAGNOSTIC_'+label+'.json')
    be.record_json(target,trial)
    return target
