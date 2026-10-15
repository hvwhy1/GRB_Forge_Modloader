"""Package only the app and small diagnostic evidence, never game/stage/backup data."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parent


def package():
    files=set(ROOT.glob('*.py'))|set(ROOT.glob('*.md'))|{ROOT/'START_DEEF.cmd'}
    for folder,patterns in [('data',('*.json',)),('bin',('*.dll','*.exe','*.json')),
                            ('src',('*.cs','*.csproj')),('tests',('*.py',))]:
        for pattern in patterns:files.update((ROOT/folder).glob(pattern))
    files.update((ROOT/'workspace/diagnostics').glob('*.json'))
    files.update((ROOT/'workspace/private_validation').glob('*.json'))
    for name in ('settings.json','additional_options.json'):
        if (ROOT/'workspace'/name).is_file():files.add(ROOT/'workspace'/name)
    if (ROOT/'workspace/quarantined_stages.json').is_file():files.add(ROOT/'workspace/quarantined_stages.json')
    manifest={'version':'20.0.0','buildRevision':'generalized-priorities-with-fresh-checkout-defaults','launcher':'START_DEEF.cmd','gameFilesIncluded':False,
              'files':{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in sorted(files)}}
    (ROOT/'BUILD_MANIFEST.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    target=ROOT.parent/'DEEF_Direct_Anvil_Installer_v0_20.zip'
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in sorted(files|{ROOT/'BUILD_MANIFEST.json'}):z.write(p,ROOT.name+'/'+p.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(target) as z:
        assert z.testzip() is None
        assert len([n for n in z.namelist() if n.lower().endswith('.cmd')])==1
    print(json.dumps({'zip':str(target),'bytes':target.stat().st_size,'files':len(files)+1}))
    return target


if __name__=='__main__':package()
