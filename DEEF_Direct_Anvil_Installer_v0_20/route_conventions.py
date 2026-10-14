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


def parse_explicit_parent(member):
    """Locate the declared Forge/data/object path inside optional folder nesting."""
    parts=[p for p in str(member).replace('\\','/').split('/') if p]
    for i, name in enumerate(parts):
        is_legacy=bool(LEGACY_FORGE.fullmatch(name))
        if not (name.casefold().endswith('.forge') or is_legacy):continue
        forge_name=name+'.forge' if is_legacy else name
        rest=[p for p in parts[i+1:] if p.casefold()!='extracted']
        if not rest:continue
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
