"""Validated loose sound banks, kept separate from Forge resource composition."""
from pathlib import Path
from collections import defaultdict
import mod_manager as mm
import batch_engine as be

AUDIO_FILES={'soundspc_sfx.pck','soundspc_sfx_wea_.pck'}


def target_for(member,game):
    name=Path(member.replace('\\','/')).name.casefold()
    return Path(game)/'sounddata'/'pc'/name if name in AUDIO_FILES else None


def prepare(mods,game,run,cfg,notify=print):
    """Stage complete AKPK packages; conflicting banks need explicit priority."""
    import conflict_policy as cp
    groups=defaultdict(list);proof=[];plan=[]
    for key,mod in mods.items():
        if not mod.get('enabled',True):continue
        members=[m for m in mod['members'] if target_for(m,game)]
        if not members:continue
        archive_hash=be.sha256(mod['archive'])
        for member in members:
            source=mm.extraction_target(mod['archive'],member,archive_hash=archive_hash)
            with Path(source).open('rb') as stream:
                if stream.read(4)!=b'AKPK':raise ValueError('Audio package must have a Wwise AKPK header: '+member)
            if not 0<Path(source).stat().st_size<=2*1024**3:raise ValueError('Audio package exceeds its size limit: '+member)
            target=target_for(member,game)
            groups[str(target)].append(dict(mod=key,member=member,archive=mod['archive'],archiveSha256=archive_hash,
                source=str(source),name=target.name,sha256=be.sha256(source)))
    for target,items in sorted(groups.items()):
        if len({r['sha256'] for r in items})==1:winner=items[0];credited=items
        else:
            winner=cp.inspect_group(items,cfg,'sounddata/pc/'+Path(target).name,None,run)
            if winner is None:raise ValueError('Audio package conflict needs an explicit priority: '+Path(target).name)
            credited=[r for r in items if r['sha256']==winner['sha256']]
        contributions=[{'mod':r['mod'],'member':r['member']} for r in credited]
        excluded=[{'mod':r['mod'],'member':r['member'],'reason':'Different complete audio bank excluded by configured priority'} for r in items if r not in credited]
        if excluded:
            path=Path(run)/'PRIORITY_EXCLUSIONS.json'
            be.record_json(path,mm.load(path,[])+excluded)
        plan.append(dict(source=winner['source'],target=target,audioContributions=contributions))
        proof.append(dict(target=target,sha256=winner['sha256'],selected=contributions,
            candidates=[{k:r[k] for k in ('mod','member','archiveSha256','sha256')} for r in items]))
        notify('Audio package selected: '+winner['mod']+' / '+winner['member'])
    be.record_json(Path(run)/'AUDIO_PACKAGES.json',{'packages':proof,'gameModified':False})
    return plan
