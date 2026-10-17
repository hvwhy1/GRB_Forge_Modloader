"""Restore only omitted runtime files owned by verified installer receipts."""
from pathlib import Path
import batch_engine as be


def safe_target(game,target):
    target=Path(target).resolve();game=Path(game).resolve()
    if not be.within(game,target) or target==game:return False
    parts={p.casefold() for p in target.relative_to(game).parts}
    return not (parts & {'savegames','saves','profiles'} or
        target.suffix.casefold() in ('.forge','.data','.save','.sav','.savegame') or
        target.name.casefold() in ('grb.exe','grb_vulkan.exe','gamerprofile.xml'))


def cleanup(plan,game,cfg):
    from layered_pipeline import receipt_history
    game=Path(game).resolve()
    seeds=[cfg['baseline_config_transaction']] if cfg.get('baseline_config_transaction') else [cfg.get(k) for k in
        ('last_config_transaction','last_runtime_transaction','last_transaction','last_actual_transaction','last_proven_transaction')]
    history=receipt_history(game,seeds)
    planned={Path(r['target']).resolve() for r in plan}
    records={}
    for receipt,doc in history:
        if receipt.name!='CONFIG_TRANSACTION.json' or doc.get('status')!='INSTALLED':continue
        for row in doc['files']:
            target=Path(row['target']).resolve()
            if target in planned:continue
            if not safe_target(game,target):raise ValueError('Unsafe historical runtime target: '+str(target))
            records.setdefault(target,[]).append((row,receipt))
    result=[]
    for target,rows in records.items():
        live=be.sha256(target) if target.is_file() else None
        # Missing files need no removal. A missing vanilla file is also left
        # for game verification rather than inferred from a stale receipt.
        if live is None:continue
        current=live;visited=set();chain=[];source=None
        while True:
            matches=[(row,receipt) for row,receipt in rows if row.get('installedSha256')==current and row.get('originalSha256')!=current]
            if not matches:break
            originals={row.get('originalSha256') for row,_ in matches}
            if len(originals)!=1:raise ValueError('Ambiguous runtime restoration history: '+str(target))
            if current in visited:raise ValueError('Cyclic runtime restoration history: '+str(target))
            visited.add(current);row,receipt=matches[0];current=row.get('originalSha256')
            chain.append(str(receipt))
            if current is None:source=None;break
            source=Path(row.get('backup') or '').resolve()
            if not be.within(receipt.parent,source) or not source.is_file() or be.sha256(source)!=current:
                raise ValueError('Runtime original backup missing or changed: '+str(target))
        if not chain:
            # An already restored original is harmless; unknown edits require review.
            if live in {row.get('originalSha256') for row,_ in rows}:continue
            raise ValueError('Removed runtime file changed outside recorded installs: '+str(target))
        item=dict(target=str(target),expectedSha256=live,cleanupHistory=chain)
        if source is None:item['remove']=True
        else:item.update(source=str(source),allowProxyReplace=True)
        result.append(item)
    return result
