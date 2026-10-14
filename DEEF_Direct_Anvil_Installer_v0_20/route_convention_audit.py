"""Review explicit weapon/variant hierarchies and historical instruction paths.

Read-only; NO new write routes become deployable. Generates evidence to guide
future Forge entry insertion, and flags mutually exclusive package variants.
"""
from __future__ import annotations
from collections import Counter, defaultdict
import argparse
import csv
import json
import re
import zipfile
from pathlib import Path
import route_conventions as rc
import new_resource_audit as nra

ROOT=Path(__file__).resolve().parent
DEFAULT_MODS=Path(r'C:\Users\Hurt\Desktop\GRB_MODDING\DEEF_Mods')
WEAPON_ZIP_NAME='Weapon Emporium Weapon Tweaks v2.2-327-V2-2-1704838836.zip'
SUPPORTED={'.entitybuilder','.buildtable','.gr_weapondbentry',
           '.dbweaponsmith_attachmentperweaponsetting','.compiledmip',
           '.texturemap','.mesh','.lodselector','.textureset','.material',
           '.skeleton','.dependency','.dbentryweaponshootsound',
           '.railattachmentdbentry','.magazineattachmentdbentry'}


def load_existing_audit(path):
    d=json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(d.get('candidates'),list):raise ValueError('Invalid previous new-resource audit')
    output=[]
    for c in d['candidates']:
        r={'blockReasonCode':'RESOURCE_NOT_PRESENT_IN_GAME_FORGE',
           'member':c['member'],'explanation':c.get('priorFailure',''),
           'proposed':[{'forge':str(c['parentForge']).replace('\\','/').rsplit('/',1)[-1],
                        'data':c['originalParentLabel'] if 'originalParentLabel' in c else c['parentData'],
                        'object':c.get('newObject',''),'kind':c['kind']}]}
        classification=nra.classify(r)
        canonical, instruction=rc.parent_data_label(c['parentData'])
        weapon=rc.weapon_variant(c['member'])
        cc=dict(c,classification=classification,parentData=canonical,
                originalParentLabel=c.get('originalParentLabel',c['parentData']),
                instructionFolderNormalized=instruction,
                weapon=weapon['weapon'] if weapon else '',
                variant=weapon['variant'] if weapon else '',
                explicitPackageParent=bool(rc.parse_explicit_parent(c['member'])))
        output.append(cc)
    return output


def inspect_zip(zip_path):
    members=[];families=defaultdict(lambda:defaultdict(list))
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            if info.is_dir():continue
            item=info.filename
            weapon=rc.weapon_variant(item)
            if not weapon:continue
            ext=Path(item).suffix.casefold()
            parent=rc.parse_explicit_parent(item)
            if ext not in SUPPORTED:continue
            detail={'member':item,'weapon':weapon['weapon'],'variant':weapon['variant'],
                    'sourceBytes':info.file_size,
                    'targetForge':parent['forge'] if parent else '',
                    'targetData':parent['data'] if parent else '',
                    'object':parent['object'] if parent else '',
                    'hasExplicitForgeParent':bool(parent)}
            members.append(detail)
            # An object can occur in more than one mutually exclusive variant.
            if parent and parent['kind']=='inner':
                obj=re.sub(r'^\d+_-_','',parent['object'],flags=re.I).casefold()
                key='|'.join([parent['forge'].casefold(),
                              re.sub(r'^\d+_-_','',parent['data'],flags=re.I).casefold(),obj])
                families[key][weapon['variant']].append(detail)
    alternatives=[]
    for target, variants in families.items():
        if len(variants)>1:
            alternatives.append({'target':target,'variants':sorted(variants),
                'members':sum(len(v) for v in variants.values()),
                'weapons':sorted({x['weapon'] for v in variants.values() for x in v}),
                'status':'EXCLUSIVE_ALTERNATIVES_NEED_SELECTION'})
    return members, alternatives


def build(audit_path,weapon_zip=None):
    candidates=load_existing_audit(audit_path)
    counts=Counter(x['classification'] for x in candidates)
    found=[x for x in candidates if x['weapon']]
    mods=Counter(x['weapon'] for x in found)
    result={'schema':'DEEF.route_convention_review.1','mode':'READ_ONLY_NO_WRITE',
            'sourceAudit':str(audit_path),'candidateCount':len(candidates),
            'updatedClassificationCounts':dict(counts),
            'weaponMissingCount':len(found),
            'weaponGroups':dict(mods),
            'instructionCanonicalizationCount':sum(bool(x['instructionFolderNormalized']) for x in candidates),
            'candidates':candidates,
            'newInsertionsApprovedForInstall':0,
            'warnings':['A declared Forge/parent path is evidence, NOT proof that its parent exists in the running game.',
                'New outer .data entries are NOT supported by the live streaming replacement writer.',
                'Do not install mutually exclusive weapon variants together.',
                'Do not treat XML and .bak authoring files as live resource payloads.']}
    if weapon_zip:
        members, alternatives=inspect_zip(weapon_zip)
        result.update(weaponArchive=str(weapon_zip),weaponPayloadMembers=len(members),
                      explicitForgeParentMembers=sum(x['hasExplicitForgeParent'] for x in members),
                      alternativeTargetGroups=len(alternatives),variantConflicts=alternatives,
                      weaponArchiveMembers=members)
    return result


def write_report(result,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    (output/'ROUTE_CONVENTION_REVIEW.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    cols=['classification','sourceArchive','weapon','variant','member','parentForge',
          'parentData','originalParentLabel','newObject','instructionFolderNormalized']
    with (output/'PARENT_ROUTE_REVIEW.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=cols,extrasaction='ignore');writer.writeheader();writer.writerows(result['candidates'])
    variant_cols=['target','weapons','variants','members','status']
    with (output/'WEAPON_VARIANT_ALTERNATIVES.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=variant_cols);writer.writeheader()
        for group in result.get('variantConflicts',[]):
            writer.writerow({k:('/'.join(v) if isinstance(v,list) else v) for k,v in group.items() if k in variant_cols})
    text=['DEEF Route Conventions — Read-only Audit',
          'Previously absent resource candidates: {}'.format(result['candidateCount']),
          'Weapon Emporium missing subresources: {}'.format(result['weaponMissingCount']),
          'Instruction folders canonicalized: {}'.format(result['instructionCanonicalizationCount'])]
    text+=['{}: {}'.format(k,v) for k,v in result['updatedClassificationCounts'].items()]
    if result.get('weaponArchive'):
        text+=['','Weapon package payload files: {}'.format(result['weaponPayloadMembers']),
               'Explicit .forge/.data parents: {}'.format(result['explicitForgeParentMembers']),
               'Targets shared across variant folders: {}'.format(result['alternativeTargetGroups'])]
    text+=['','Actions:',
      '1. Use normalized parent names to recharacterize Put in ... entries. Verify each inner object independently.',
      '2. Treat weapon parent .data names as explicit ADD-ENTRY candidates, not unknown routes.',
      '3. Verify parent existence on the installed game; new outer entries require a separate validated Forge-table writer.',
      '4. Select one variant per conflicting weapon target; do NOT merge variant folders automatically.',
      '5. Preserve the existing 275-edit staged batch; audit does not change game or saved stages.']
    (output/'PARENT_ROUTE_ACTION_PLAN.txt').write_text('\n'.join(text)+'\n',encoding='utf-8')
    return '\n'.join(text)


def locate_audit():
    # Prefer a fresh audit from this installer's own characterization run.
    choices=list((ROOT/'workspace'/'auto_runs').glob('*/additions/NEW_RESOURCE_AUDIT.json'))
    choices.append(ROOT/'workspace'/'new_resource_audit'/'NEW_RESOURCE_AUDIT.json')
    parents=[ROOT.parent,ROOT.parent.parent,Path.home()/'Desktop',
             Path.home()/'Desktop'/'GRB_MODDING',Path.home()/'Desktop'/'GRBmod']
    for p in parents:
        if not p.exists():continue
        for folder in p.glob('DEEF_Direct_Anvil_Installer_v0_19_*'):
            choices.append(folder/'workspace'/'new_resource_audit'/'NEW_RESOURCE_AUDIT.json')
            choices.extend(folder.glob('workspace/auto_runs/*/additions/NEW_RESOURCE_AUDIT.json'))
    choices=[c for c in set(choices) if c.is_file()]
    return max(choices,key=lambda c:c.stat().st_mtime) if choices else None


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--audit');ap.add_argument('--weapon-zip');ap.add_argument('--output')
    args=ap.parse_args()
    audit=Path(args.audit) if args.audit else locate_audit()
    if not audit:raise FileNotFoundError('Supply --audit path/to/NEW_RESOURCE_AUDIT.json from your existing v0.19.5 run')
    weapon=Path(args.weapon_zip) if args.weapon_zip else DEFAULT_MODS/WEAPON_ZIP_NAME
    if not weapon.is_file():
        print('Weapon archive not found at default path; reviewing existing audit only:',weapon)
        weapon=None
    result=build(audit,weapon)
    print(write_report(result,args.output or ROOT/'workspace'/'route_convention_review'))

if __name__=='__main__':main()
