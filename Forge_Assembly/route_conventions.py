"""Conservative, reusable path rules for Forge mod package hierarchies.

An instruction folder is not an actual file. A weapon name/variant is a mod
selection, not a physical Forge path. These helpers DO NOT approve installation.
"""
from __future__ import annotations
import re

LEGACY_FORGE = re.compile(r'^DataPC(?:_[A-Za-z0-9_]+)?$', re.I)
DATA_PATTERN = re.compile(r'^(?:\d+_-_)?[^/\\]+\.data$', re.I)
INSTRUCTION = re.compile(
    r'^\s*(?:put\s+in|place\s+in|insert\s+(?:in|into)|copy\s+(?:files\s+)?(?:to|into)|drop\s+(?:files\s+)?(?:in|into))\s+', re.I)


def parent_data_label(label):
    """Return canonical .data label and whether an instruction was stripped.

    Only strips a FULL instruction prefix when the remaining text is exactly a
    syntactically valid .data basename. Never converts directories to file paths.
    """
    if not isinstance(label, str):return label, False
    raw=label.strip()
    found=INSTRUCTION.match(raw)
    if not found:return raw, False
    candidate=raw[found.end():].strip().strip('"\'')
    if DATA_PATTERN.fullmatch(candidate):return candidate, True
    return raw, False


def normalize_route(route):
    result=dict(route)
    result['data'], changed = parent_data_label(str(result.get('data','')))
    if changed:
        result['evidence']=str(result.get('evidence',''))+'; instruction folder normalized'
    return result


FORGE_FOLDER_ALIASES={
    'datapcpatch01':'DataPC_patch_01.forge','patch01':'DataPC_patch_01.forge',
    'datapcextrapatch01':'DataPC_extra_patch_01.forge','extrapatch01':'DataPC_extra_patch_01.forge',
    'datapcresourcespatch01':'DataPC_Resources_patch_01.forge','resourcespatch01':'DataPC_Resources_patch_01.forge',
    'datapcresources':'DataPC_Resources.forge','datapcextra':'DataPC_extra.forge'}

def forge_folder_label(name):
    token=re.sub(r'[^a-z0-9]','',name.casefold().removesuffix('.forge'))
    if token in FORGE_FOLDER_ALIASES:return FORGE_FOLDER_ALIASES[token]
    if LEGACY_FORGE.fullmatch(name):return name+'.forge'
    return name if name.casefold().endswith('.forge') else None


def parse_explicit_parent(member):
    """Locate the declared Forge/data/object path inside optional folder nesting."""
    parts=[p for p in str(member).replace('\\','/').split('/') if p]
    for i, name in enumerate(parts):
        forge_name=forge_folder_label(name)
        if not forge_name:continue
        is_legacy=not name.casefold().endswith('.forge')
        rest=[p for p in parts[i+1:] if p.casefold()!='extracted']
        if not rest:continue
        db_alias=re.fullmatch(r'(?:\d+_-_)?(?:DBContainer|DBContainerEntry(?:_0X104634F921)?)',rest[0],re.I)
        if db_alias and len(rest)==2 and forge_name.casefold()=='datapc_patch_01.forge':
            rest=['DBContainerEntry_0X104634F921.data',rest[1]]
        positions=[j for j,value in enumerate(rest) if DATA_PATTERN.fullmatch(parent_data_label(value)[0])]
        if positions and len(rest)-positions[0] in (1,2):
            rest=rest[positions[0]:]
        data, _ = parent_data_label(rest[0])
        if not DATA_PATTERN.fullmatch(data):continue
        if len(rest) not in (1,2):continue
        return {'forge':forge_name,'data':data, 'object':rest[1] if len(rest)==2 else '',
                'legacyForgeFolderAlias':is_legacy,
                'kind':'inner' if len(rest)==2 else 'data',
                'precedingFolders':parts[:i], 'containerExplicit':True}
    return None


def weapon_variant(member):
    """Identify weapon and variant from an archive path; never choose a variant.

    A standalone subfolder and a stock-compatible subfolder are *alternatives*.
    """
    parts=[p for p in str(member).replace('\\','/').split('/') if p]
    if not parts or not parts[0].casefold().startswith('weapon tweaks v'):return None
    if len(parts)<3:return None
    weapon=parts[1]
    cut=next((i for i,x in enumerate(parts[2:],2) if x.lower().endswith('.forge') or x.casefold()=='dbcontainerentry'),len(parts)-1)
    options=parts[2:cut]
    return {'weapon':weapon, 'variant':' / '.join(options) if options else 'DEFAULT',
            'variantFolders':options}
