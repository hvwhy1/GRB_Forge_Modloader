"""Player-only shotgun damage overlay, preserving cosmetic and ammo bytes."""
from pathlib import Path
import re
from weapon_stats_merge import edit_damage


def player_shotgun(name):
    name=re.sub(r'^\d+_-_', '', Path(name).name).casefold()
    return (name.endswith('.gr_weapondbentry')
            and name.startswith(('stg_', 'unique_stg_', 'y1e1_stg_', 'y1e3_stg_', 'yee2_stg_'))
            and not any(token in name for token in ('_npc', '_ia.', '_ia_', 'prog_test', 'silverback')))


def apply(reader,base,output):
    if not player_shotgun(base):raise ValueError('Not a player shotgun')
    return edit_damage(reader,base,200,output)
