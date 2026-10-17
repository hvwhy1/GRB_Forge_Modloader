"""Short actionable UI failures, with full diagnostics stored separately."""
import json,re,uuid
from datetime import datetime
from pathlib import Path


def explain(error,work):
    raw=str(error);proof=None;doc={};mods=[]
    # The bridge puts the report on its own line, before the stack trace.
    for line in raw.splitlines():
        match=re.search(r'([A-Za-z]:[\\/].*?PROOF\.json)',line)
        candidate=match.group(1) if match else line.strip()
        if candidate.endswith('PROOF.json'):
            path=Path(candidate)
            try:
                path.resolve().relative_to(Path(work).resolve())
                doc=json.loads(path.read_text(encoding='utf-8'));proof=path
                rows=json.loads((path.parent/'REQUEST.json').read_text(encoding='utf-8'))
                mods=sorted({r['mod'] for r in rows if r.get('mod')})
            except (OSError,ValueError,TypeError,KeyError):pass
    technical=raw+'\n'+str(doc.get('error',''))
    title='Operation stopped';message='The operation could not finish. Open the diagnostic report for details, then retry after correcting the reported problem.'
    if any(text in technical.lower() for text in ('recovery backup missing or changed','backup rotation stopped','backups retained','backup baseline does not match')):
        title='Backup consolidation needs review'
        message='The replacement recovery sets could not be verified. Existing recovery data was retained. Check the diagnostic report before deleting any backups; it identifies the changed or missing file.'
    elif 'Retained stage bytes changed' in technical:
        title='The saved stage has changed'
        message='An existing staged Forge no longer matches its verified hash. It was not reused or installed. Select the correct untouched stage, or rebuild from the mod archives.'
    elif 'WABO suppressor preset is incomplete:' in technical:
        title='The suppressor preset needs its core files'
        message='The selected WABO suppressor preset requires both its author-supplied core and preset files. Check the WABO archive, then choose an available Suppressor preset in Conflicts & new assets and scan again.'
    elif 'FPV_PACKAGE_CHOICE:' in technical:
        title='Choose an FPV package'
        message='More than one complete Native First Person package was found. In First person & ReShade, use Find first-person package and select the intended package from the dropdown, then click Install FPV. Multiple copies are never mixed together.'
    elif 'FPV_PACKAGE_INCOMPLETE:' in technical:
        title='FPV package is missing files'
        message='Extract the full Native First Person package, keeping core and options together. Select that package in First person & ReShade and retry Install FPV. The diagnostic details list the missing files.'
    elif 'FPV_PACKAGE_NOT_FOUND:' in technical:
        title='Select the FPV source package'
        message='In First person & ReShade, use Find first-person package or browse to the extracted Native First Person folder containing core and options, then click Install FPV. Selecting core or an enclosing download folder is also supported.'
    elif 'BACKUP_RENAME_BLOCKED:' in technical:
        target=technical.split('BACKUP_RENAME_BLOCKED:',1)[-1].splitlines()[0].strip()
        title='Windows blocked recovery-folder rotation'
        message='The game files were installed, but Windows kept a recovery folder unavailable after several retries. Existing recovery data was retained. Retry the installation to finish backup consolidation.\n\nRecovery folder: '+target
    elif 'RECEIPT_WRITE_BLOCKED:' in technical:
        target=technical.split('RECEIPT_WRITE_BLOCKED:',1)[-1].splitlines()[0].strip()
        title='Windows blocked an installer receipt'
        message='Windows kept the installer backup receipt unavailable after several retries. This is an installer metadata file, not a ReShade game file. Retry the operation; if it repeats, check folder access and security software.\n\nReceipt: '+target
    elif 'Storage limit:' in technical:
        title='Workspace storage limit reached'
        message='Staging stopped before it could keep growing. Open Diagnostics & recovery and use Clean generated files. The default workspace budget is 200 GiB, with at least 20 GiB kept free on the drive. Required backups are preserved; if they leave too little room, review the storage budget before retrying.'
    elif 'Another installer operation is using this workspace' in technical:
        title='Another installer operation is running';message='Wait for the other scan, installation, or cleanup to finish, then retry. Only one operation can use this workspace at a time.'
    elif 'WHOLE_DATA_ID_NAME_MISMATCH' in technical:
        resource=technical.split('WHOLE_DATA_ID_NAME_MISMATCH:',1)[-1].splitlines()[0].strip()
        title='A mod resource needs a compatibility check'
        message=f'A mod renamed an existing game resource: {resource}.\n\nThe installer stopped rather than accepting an unverified replacement. No installation was performed. Try disabling the affected mod and scan again, or share the diagnostic report for a compatibility fix.'
    elif any(code in technical for code in ('NEED_EXACT_CHOICE','NEEDS_EXACT_CHOICE','NEEDS_PRIORITY','PRIORITY_TIE','explicit priority','explicit choice')):
        title='Choose which mod should win'
        message='Two selected files change the same resource. Open Conflicts & new assets, choose the resource variant or mod priority, save it, then run Scan & stage again.'
    elif 'Saved conflict choice belongs to a changed archive' in technical or 'Saved conflict winner is absent' in technical:
        title='A saved mod choice needs updating'
        message='A mod used by a saved conflict choice was changed or removed. Refresh mod settings in Conflicts & new assets, review its resource choice, then run Scan & stage again.'
    elif 'Complete content composition failed' in technical:
        title='A mod could not be merged'
        message='Scan & stage could not combine a mod with the game files. No installation was performed. Review the affected mod below and open the diagnostic report for the exact file. You can disable that mod and scan again.'
    elif 'changed after staging' in technical or 'scan and stage again' in technical.casefold():
        title='Build a new stage before installing'
        message='The selected stage no longer matches the current settings, mod selection, or game baseline. Run Scan & stage again, review the new result, then install that batch.'
    elif any(word in technical.casefold() for word in ('game is running','game must be closed','close the game')):
        title='Close Breakpoint first';message='Save your progress and close Breakpoint, then retry this operation.'
    elif any(word in technical.casefold() for word in ('baseline','verification required','verify the game')):
        title='A verified game baseline is needed';message='Finish game-file verification in Ubisoft Connect, then use Record verified baseline in Setup before scanning again. If verification is already complete, record the baseline and retry.'
    elif any(word in technical.casefold() for word in ('permissionerror','access is denied','permission denied')):
        title='A file could not be accessed';message='Close any program using the file and check that the selected folder is writable, then retry. The diagnostic report identifies the file.'
    elif any(word in technical.casefold() for word in ('no space left','not enough space','gib free','disk full')):
        title='More free disk space is needed';message='The selected drive does not have enough space to finish. Free space or choose another workspace/backup drive, then retry. The diagnostic report lists the space required.'
    if mods:
        message+='\n\nMods in this merge: '+', '.join(mods)
    folder=Path(work)/'diagnostics'/'errors';folder.mkdir(parents=True,exist_ok=True)
    report=folder/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]+'.json')
    report.write_text(json.dumps(dict(summary=title,message=message,technicalDetails=raw,bridgeReport=str(proof) if proof else None,bridgeDetails=doc),indent=2),encoding='utf-8')
    return title,message,report
