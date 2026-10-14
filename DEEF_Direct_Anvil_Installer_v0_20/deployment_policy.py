"""V20 recovery baseline and deployment checks shared by every install entry point."""
import json
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORK = ROOT / 'workspace'
STATE = WORK / 'recovery_state.json'
VERSION = '20.0.0'
HOLDS = ROOT / 'data' / 'compatibility_holds.json'
_archive_hash_cache={}


def held_route(archive,member,archive_hash=None):
    if not HOLDS.is_file():return None
    rules=json.loads(HOLDS.read_text(encoding='utf-8')).get('routes',[])
    archive=Path(archive)
    matches=[r for r in rules if r['archiveName'].casefold()==archive.name.casefold()
             and r['member'].replace('\\','/').casefold()==str(member).replace('\\','/').casefold()]
    if not matches:return None
    if archive_hash is None:
        import batch_engine as be
        if not archive.is_file():return None
        stat=archive.stat();key=(str(archive.resolve()),stat.st_size,stat.st_mtime_ns)
        if key not in _archive_hash_cache:_archive_hash_cache[key]=be.sha256(archive)
        archive_hash=_archive_hash_cache[key]
    return next((r['reason'] for r in matches if r['archiveSha256'].casefold()==archive_hash.casefold()),None)


def recovery_state():
    if not STATE.is_file():
        return {'installationPaused': True, 'reason': 'Finish Ubisoft file verification, then record the clean baseline.'}
    return json.loads(STATE.read_text(encoding='utf-8'))


def require_ready(game=None):
    state = recovery_state()
    if state.get('installationPaused', True) or not state.get('baselineId'):
        raise RuntimeError(state.get('reason', 'Record a fresh verified-game baseline first.'))
    if game is not None and Path(state['gameRoot']).resolve() != Path(game).resolve():
        raise ValueError('Game folder differs from the recorded clean baseline.')
    return state


def record_baseline(game, notify=print):
    """Called only after the user explicitly reports verification is complete."""
    import batch_engine as be
    game = Path(game).resolve()
    if not (game / 'GRB.exe').is_file():
        raise ValueError('Game folder must contain GRB.exe.')
    if be.game_processes_running():
        raise RuntimeError('Close the game before recording the baseline.')
    files = sorted(game.glob('*.forge'))
    if not files:
        raise ValueError('No active Forge files found.')
    hashes = {}
    for index, file in enumerate(files, 1):
        before = file.stat()
        notify(f'Baseline {index}/{len(files)}: {file.name}\n')
        digest = be.sha256(file)
        after = file.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError('Game verification is still changing files; baseline was not saved.')
        hashes[str(file.resolve())] = digest
    state = {'installationPaused': False, 'baselineId': uuid.uuid4().hex,
             'gameRoot': str(game), 'hashes': hashes, 'productVersion': VERSION,
             'recordedUtc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    be.record_json(STATE, state)
    return state


def stamp(state):
    baseline = require_ready(state['plan']['gameRoot'])
    for source,digest in state['plan'].get('sourceHashes',{}).items():
        if baseline['hashes'].get(source)!=digest:
            raise ValueError('Game file changed since the verified baseline; stop and review '+source)
    state.update(productVersion=VERSION, baselineId=baseline['baselineId'])
    return state


def require_install(state, manifest):
    if state.get('buildScope') not in (None,'ENABLED_MOD_SELECTION'):
        raise ValueError('Diagnostic batches cannot be installed. Choose Enabled mod selection and Scan and stage.')
    baseline = require_ready(state.get('plan', {}).get('gameRoot'))
    manifest = Path(manifest).resolve()
    if WORK.resolve() not in manifest.parents:
        raise ValueError('V20 installs only its own fresh stages. Older crash batches cannot be reused.')
    if state.get('productVersion') != VERSION or state.get('baselineId') != baseline['baselineId']:
        raise ValueError('Stage belongs to an older build or baseline; scan and stage again in v20.')
    if state.get('writer')=='FORGE_LAYERED_BATCH_V20':
        import layered_pipeline
        layered_pipeline.inspect_stage(manifest,baseline['gameRoot'])
    elif (WORK/'mods.json').is_file():
        import layered_pipeline
        selected=json.loads((WORK/'mods.json').read_text(encoding='utf-8'))
        if layered_pipeline.has_foundations(selected):
            raise ValueError('Selected mods contain complete Forge replacements. Scan and stage a layered build first; this older resource-only batch would omit or overwrite them.')
    if state.get('privateOnly') or state.get('additionSummary', {}).get('added', 0):
        raise ValueError('New-container experiments stay private until a separate compatibility trial is prepared.')
    completed={(r.get('mod'),r.get('member')) for r in state.get('finishedRoutes',[])}
    for route in state.get('plan',{}).get('routes',[]):
        if (route.get('mod'),route.get('member')) in completed:
            hold=held_route(route.get('archive',''),route.get('member',''),route.get('archiveSha256'))
            if hold:raise ValueError('Held compatibility route: '+route.get('member','')+' — '+hold)
    quarantine=WORK/'quarantined_stages.json'
    if quarantine.is_file():
        blocked=json.loads(quarantine.read_text(encoding='utf-8'))
        fingerprint={k:v.get('stagedSha256') for k,v in state.get('outputs',{}).items()}
        if any(item.get('stagedHashes')==fingerprint for item in blocked):
            raise ValueError('This batch failed the game startup test. Use a smaller diagnostic selection.')
    for source, entry in state.get('outputs', {}).items():
        if entry.get('sourceSha256') != baseline['hashes'].get(source):
            raise ValueError('Stage source is not the verified clean baseline: ' + source)
    return baseline
