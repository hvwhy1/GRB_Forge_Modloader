"""Independent, author-supplied gameplay selections layered over WABO difficulty."""
from pathlib import Path
import re

AMMO_NAMES={'12,7x55','300blk','4.6x30','410','44_magnum','45_acp_pistol','5,45x39','5,7_pistol','5,7x28','50ae','50bmg','7,62x39','7,62x54','9mm_pistol','9x39','dbammotype_12g','dbammotype_308','dbammotype_45','dbammotype_5_56','dbammotype_7_62','dbammotype_9mm','dbperklih_ammomax1','dbperklih_ammomax2'}

def logical_name(member):
    return re.sub(r'^\d+_-_', '',Path(member.replace('\\','/')).name).rsplit('.',1)[0].casefold()

def wabo_variant(member,cfg):
    """None means selected; a reason means excluded. No difficulty-wide downgrade."""
    folder='/'+member.replace('\\','/').casefold()
    name=logical_name(member)
    ammo=cfg.get('ammo_capacity_profile')=='Vanilla'
    suppress=cfg.get('suppressor_profile')=='Vanilla-like'
    custom='/5_custom/' in folder
    if custom:
        if ammo and ('/ammo presets/easy (vanilla)/' in folder or '/ammo multipliers/vanilla_x1,25/' in folder):return None
        if suppress and '/suppressors/1 easy (vanilla)/' in folder:return None
        return 'Optional WABO variant not selected'
    parts=member.replace('\\','/').split('/')
    difficulty=cfg.get('wabo_difficulty')
    if difficulty and len(parts)>1 and parts[1] not in ('0_Required for all',difficulty):return 'Different WABO difficulty'
    if ammo and name in AMMO_NAMES:return 'Replaced by author vanilla ammo preset and vanilla ammo perk multiplier'
    if suppress and name.startswith('muzzle_sil_'):return 'Replaced by author vanilla-like suppressor preset'
    if suppress and name.startswith('muzzle_std_'):return 'Keep vanilla/cosmetic standard muzzle behavior with the vanilla-like suppressor profile'
    return None
