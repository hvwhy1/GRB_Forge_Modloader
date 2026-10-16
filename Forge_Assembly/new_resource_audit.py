"""Classify absent game resources as potential INSERTIONS, not failed replacements.

Read-only. A declared Forge path is not proof of destination correctness.
No candidate is eligible for automatic/live staging until insertion is verified.
"""
from __future__ import annotations
import argparse
import collections
import csv
import json
from pathlib import Path
import re
from datetime import datetime, timezone

import resolver
import route_conventions as rc

ROOT=Path(__file__).resolve().parent
DEFAULT_MODS=Path(r'C:\Users\Hurt\Desktop\GRB_MODDING\DEEF_Mods')


def _label(path):
    return re.sub(r'^\d+_-_', '', str(path).replace('\\','/').rsplit('/',1)[-1]).casefold()


def classify(row):
    reason=row.get('blockReasonCode')
    if reason not in ('RESOURCE_NOT_PRESENT_IN_GAME_FORGE','NEW_RESOURCE_INSERTION_CANDIDATE'):return None
    variants=row.get('proposed',[])
    if not isinstance(variants,list) or len(variants)!=1:return 'AMBIGUOUS_ADD_DESTINATION'
    v=variants[0]
    forge=Path(v.get('forge','')).name
    parent=v.get('data','')
    explanation=row.get('explanation','')
    if not forge.lower().endswith('.forge') or not parent.lower().endswith('.data'):
        return 'UNKNOWN_NEW_RESOURCE_TYPE'
    # The error was emitted at the *inner object* lookup, after the parent
    # .data entry had been found in the active Forge. Do not infer this solely
    # from kind='inner' -- the parent may itself be missing.
    prefix=forge+'/'
    if v.get('kind')=='inner':
        canonical, was_instruction=rc.parent_data_label(parent)
        explicit=rc.parse_explicit_parent(row.get('member',''))
        if was_instruction and re.search(re.escape(forge)+r':\s+NOT_IN_INSTALLED_GAME\b', explanation,re.I):
            return 'INSTRUCTION_PARENT_CANONICALIZED_RECHECK'
        if explicit and explicit['kind']=='inner' and \
                rc.parent_data_label(explicit['data'])[0].casefold()==canonical.casefold() and \
                re.search(re.escape(forge)+r':\s+NOT_IN_INSTALLED_GAME\b', explanation,re.I):
            return 'DECLARED_FORGE_PARENT_ABSENT_ADD_ENTRY'
        if re.search(re.escape(prefix)+r'[^|]*:\s+NOT_IN_INSTALLED_GAME\b',explanation,re.I):
            return 'NEW_INNER_OBJECT_PARENT_FOUND'
        if re.search(re.escape(forge)+r':\s+NOT_IN_INSTALLED_GAME\b',explanation,re.I):
            if rc.weapon_variant(row.get('member','')):
                return 'HISTORICAL_WEAPON_PARENT_ABSENT_ADD_ENTRY'
            return 'NEW_PARENT_DATA_REQUIRED'
        return 'INNER_PARENT_NOT_CONFIRMED'
    if v.get('kind')=='data':return 'NEW_FORGE_DATA_ENTRY_CANDIDATE'
    return 'UNKNOWN_NEW_RESOURCE_TYPE'


def build(doc,characterization):
    source=Path(characterization)
    game=Path(doc.get('game',''))
    results=[];counts=collections.Counter()
    for row in doc.get('entries',[]):
        kind=classify(row)
        if not kind:continue
        counts[kind]+=1
        v=(row.get('proposed') or [{}])[0]
        forge=Path(v.get('forge','')).name
        canonical, was_instruction=rc.parent_data_label(v.get('data',''))
        weapon=rc.weapon_variant(row.get('member',''))
        candidate={
            'index':len(results)+1,
            'classification':kind,
            'sourceArchive':row.get('archive',''),
            'member':row.get('member',''),
            'sourceClassification':'EXISTING_CHARACTERIZATION_NOT_BINARY_VERIFIED',
            'parentForge':str(game/forge) if forge else '',
            'parentData':canonical,
            'originalParentLabel':v.get('data',''),
            'instructionFolderNormalized':was_instruction,
            'weapon':weapon['weapon'] if weapon else '',
            'variant':weapon['variant'] if weapon else '',
            'explicitPackageParent':bool(rc.parse_explicit_parent(row.get('member',''))),
            'newObject':v.get('object',''),
            'kind':v.get('kind',''),
            'evidence':v.get('evidence',''),
            'priorFailure':row.get('explanation',''),
            'liveInstallApproved':False,
        }
        results.append(candidate)
    return {
        'schema':'DEEF.new_resource_audit.2',
        'mode':'READ_ONLY_ADDITION_TRIAGE',
        'characterization':str(source.resolve()),
        'game':str(game),
        'generatedUtc':datetime.now(timezone.utc).isoformat(),
        'candidateCount':len(results),
        'classificationCounts':dict(counts),
        'candidates':results,
        'automaticallyDeployableNewEntries':0,
        'warning':'New resources must be inserted, not mapped to unrelated existing assets. Existing streaming writer only supports replacements. New-entry insertion needs private-copy roundtrip and gameplay validation before live deployment.'
    }


def run(charfile=None,outdir=None):
    if charfile is None:
        found=resolver.find_characters()
        if not found:raise FileNotFoundError('No characterization found. Provide --characterization path/to/AUTO_CHARACTERIZATION.json')
        charfile=found[0]
    path,d=resolver.read_characterization(charfile)
    output=Path(outdir) if outdir else ROOT/'workspace'/'new_resource_audit'
    output.mkdir(parents=True,exist_ok=True)
    report=build(d,path)
    jsonfile=output/'NEW_RESOURCE_AUDIT.json'
    jsonfile.write_text(json.dumps(report,indent=2),encoding='utf-8')
    csvfile=output/'NEW_RESOURCE_CANDIDATES.csv'
    fields=['index','classification','sourceArchive','member','parentForge','parentData','originalParentLabel','weapon','variant','newObject','evidence','priorFailure']
    with csvfile.open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(report['candidates'])
    info=['DEEF New Resource Insertion Audit','Source: '+str(path),'','Candidates: '+str(report['candidateCount'])]
    info+=['{}: {}'.format(k,v) for k,v in report['classificationCounts'].items()]
    info+=['','A missing resource is NOT automatically a wrong destination; it may be an intentional new entry.',
           'NEW_INNER_OBJECT_PARENT_FOUND: existing Forge/.data parent confirmed, suitable for private insertion trial.',
           'NEW_FORGE_DATA_ENTRY_CANDIDATE: outer Forge table must gain an entry; existing replacement writer cannot do that.',
           'DECLARED_FORGE_PARENT_ABSENT_ADD_ENTRY: package explicitly names .forge/.data; nested edits require a NEW outer .data entry or a verified remap.',
           'INSTRUCTION_PARENT_CANONICALIZED_RECHECK: Put in ... is an install instruction, not part of .data filename; re-characterize to check actual parent and object.',
           'HISTORICAL_WEAPON_PARENT_ABSENT_ADD_ENTRY: parent is supplied by route hints but not by a literal .forge directory; verify before insertion.',
           'NEW_PARENT_DATA_REQUIRED: parent .data is absent or unconfirmed; do not auto-insert.',
           'No addition candidates are automatically enabled in existing 275-edit staging/install batches.',
           'No game or staged batch files were modified.']
    (output/'NEW_RESOURCE_ACTION_PLAN.txt').write_text('\n'.join(info)+'\n',encoding='utf-8')
    print('\n'.join(info))
    print('Reports:',jsonfile, csvfile,output/'NEW_RESOURCE_ACTION_PLAN.txt',sep='\n  ')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--characterization');p.add_argument('--report-dir')
    a=p.parse_args();run(a.characterization,a.report_dir)
