"""Independent, author-supplied gameplay selections layered over WABO difficulty."""
from pathlib import Path
import re

AMMO_NAMES={'12,7x55','300blk','4.6x30','410','44_magnum','45_acp_pistol','5,45x39','5,7_pistol','5,7x28','50ae','50bmg','7,62x39','7,62x54','9mm_pistol','9x39','dbammotype_12g','dbammotype_308','dbammotype_45','dbammotype_5_56','dbammotype_7_62','dbammotype_9mm','dbperklih_ammomax1','dbperklih_ammomax2'}
SUPPRESSOR_PRESETS={'Vanilla-like':'1 Easy (Vanilla)','Normal':'2 Normal','Hard':'3 Hard','Realistic':'4 Realistic'}

def logical_name(member):
    return re.sub(r'^\d+_-_', '',Path(member.replace('\\','/')).name).rsplit('.',1)[0].casefold()


def validate_suppressor_package(members,cfg):
    profile=cfg.get('suppressor_profile','Difficulty preset')
    if profile=='Difficulty preset':return
    if profile not in SUPPRESSOR_PRESETS:raise ValueError('Unknown WABO suppressor preset: '+str(profile))
    paths=['/'+m.replace('\\','/').casefold() for m in members]
    required=('/suppressors/0 core/','/suppressors/'+SUPPRESSOR_PRESETS[profile].casefold()+'/')
    if not all(any(folder in path for path in paths) for folder in required):
        raise ValueError('WABO suppressor preset is incomplete: '+profile+' requires both the author core and its matching preset. Choose an available suppressor preset in Conflicts & new assets.')

def wabo_variant(member,cfg):
    """None means selected; a reason means excluded. No difficulty-wide downgrade."""
    folder='/'+member.replace('\\','/').casefold()
    name=logical_name(member)
    ammo=cfg.get('ammo_capacity_profile')=='Vanilla'
    profile=cfg.get('suppressor_profile','Difficulty preset')
    if profile!='Difficulty preset' and profile not in SUPPRESSOR_PRESETS:raise ValueError('Unknown WABO suppressor preset: '+str(profile))
    suppress=profile in SUPPRESSOR_PRESETS
    custom='/5_custom/' in folder
    if custom:
        if ammo and ('/ammo presets/easy (vanilla)/' in folder or '/ammo multipliers/vanilla_x1,25/' in folder):return None
        if suppress and '/suppressors/' in folder:
            if '/suppressors/0 core/' in folder or '/suppressors/'+SUPPRESSOR_PRESETS[profile].casefold()+'/' in folder:return None
            return 'Different WABO suppressor preset'
        return 'Optional WABO variant not selected'
    parts=member.replace('\\','/').split('/')
    difficulty=cfg.get('wabo_difficulty')
    if difficulty and len(parts)>1 and parts[1] not in ('0_Required for all',difficulty):return 'Different WABO difficulty'
    if ammo and name in AMMO_NAMES:return 'Replaced by author vanilla ammo preset and vanilla ammo perk multiplier'
    if suppress and name.startswith(('muzzle_sil_','muzzle_std_','dbweaponattachmentuiconfig_silencer_')):return 'Replaced by author suppressor core and '+profile+' preset'
    return None
