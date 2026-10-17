"""Destination hints transcribed from the user's manual installation guides.

Hints never bypass filename resolution, binary IDs, duplicate checks or proof.
"""
from pathlib import Path
import re


def candidates(archive,member,forges,inspect):
    name=Path(archive).name.casefold();leaf=Path(member.replace('\\','/')).name
    targets=[]
    if ('-1102-' in name and ('nightmare' in name or 'fear' in name)):
        if leaf.lower().endswith('.wavespawner'):
            targets=[('DataPC_TGT_WorldMap_Bootstrap_Split_patch_01.forge','TGT_WorldMap')]
        else:targets=[('DataPC_patch_01.forge','DBContainerEntry_')]
    elif '-1204-' in name and leaf.lower().endswith('.gr_weapondbentry'):
        targets=[('DataPC_patch_01.forge','DBContainerEntry_')]
    elif '-1585-' in name:
        if leaf.lower().endswith('.data') and '/0_required for all/' in '/'+member.casefold():
            return [{'kind':'data','forge':'DataPC_patch_01.forge','data':leaf,'object':'','evidence':'WABO author guide: localization containers belong directly in patch Forge'}]
        if not leaf.lower().endswith(('.txt','.md','.pdf','.data')):targets=[('DataPC_patch_01.forge','DBContainerEntry_')]
    elif '-714-' in name and leaf.lower().endswith('.buildtable'):
        targets=[('DataPC_patch_01.forge','TEAMMATE_Template')]
    elif ('-1159-' in name or '-489-' in name) and re.sub(r'^\d+_-_', '',leaf).split('.')[0].casefold() in {
        'dbbooleanstatdescriptor_canusewhitehot','dbperklih_nightvision','dbperklih_thermalvision'}:
        targets=[('DataPC_patch_01.forge','DBContainerEntry_')]
    elif '-1246-' in name and leaf.lower().endswith('.data'):
        # Author's primary destination, followed by documented fallback. Actual
        # index matches decide; multiple eligible matches require user review.
        return [{'kind':'data','forge':f,'data':leaf,'object':'','evidence':'User-provided tracer removal guide (primary/fallback)'} for f in ('DataPC_Resources_patch_01.forge','DataPC_Resources.forge')]
    result=[]
    for forge,prefix in targets:
        matches=forges.get(forge.casefold(),[])
        if len(matches)!=1:continue
        inventory=inspect(matches[0],'AUTO')
        for data in inventory.get('nestedDataEntries',[]):
            logical=re.sub(r'^\d+_-_', '',data).rsplit('.',1)[0]
            if (logical.casefold().startswith(prefix.casefold()) if prefix.endswith('_') else logical.casefold()==prefix.casefold()):
                result.append({'kind':'inner','forge':forge,'data':data,'object':leaf,
                    'evidence':'User-provided DBContainer/WorldMap installation guide; actual parent from Forge index'})
    return result


def excluded_variant(archive,member,cfg=None):
    """Explicit user choice for the two overlapping caller resources."""
    name=Path(archive).name.casefold();leaf=Path(member.replace('\\','/')).name
    logical=re.sub(r'^\d+_-_', '',leaf).casefold()
    import mod_manager
    if cfg is None:cfg=mod_manager.load(mod_manager.CONFIG,{})
    folder='/'+member.replace('\\','/').strip('/').casefold()
    if '-1807-' in name and '/'+('FDE' if cfg.get('bcm_handguard','Black')=='Black' else 'Black').casefold()+' handguard/' in folder:
        return 'Selected BCM handguard: '+cfg.get('bcm_handguard','Black')
    if ('-1159-' in name or '-489-' in name) and cfg.get('thermal_priority')=='Standard night vision and thermal':
        return 'User selected standard night vision and thermal; vision replacement/disable files are alternatives'
    if '-1159-' in name and logical=='dbperklih_nightvision.dbperk' and cfg.get('preserve_night_vision',True):
        return 'Preserve normal night vision alongside white-hot vision; author disabling perk is an alternative'
    if name.startswith('various tweaks-537-') and '/no ads zoom/' in folder and cfg.get('use_updated_ads_zoom'):
        return 'Superseded by the selected author No ADS Zoom Updated package'
    if '-489-' in name and cfg.get('thermal_priority')=='Pathfinder white thermal vision':
        return 'User selected Pathfinder white thermal instead of disabling thermal'
    if name.startswith('various tweaks-537-') and '/item wheel swap/' in folder:
        import deployment_policy
        held='Item wheel swap/Syringe and Binoculars/Put in 0_-_Game Bootstrap Settings.data/1_-_DBItemSetting_HealingItemEssence_Bandages.DBToolSetting'
        if deployment_policy.held_route(archive,held):return 'Coupled item-wheel group held: never install only its syringe replacement'
    if '-537-' in name and '/single trigger for npc/' in folder and cfg.get('npc_trigger_priority')=='Vanilla automatic fire':
        return 'Vanilla automatic-fire profile replaces the coupled NPC/vehicle trigger changes'
    if '-537-' in name and '/single trigger for npc/' in folder and cfg.get('preserve_player_automatic_fire',True) and logical.endswith('.triggerattachmentdbentry'):
        return 'Shared trigger definition preserved for player and vehicle automatic fire'
    if '-537-' in name and '/item wheel swap/' in folder and cfg.get('item_wheel_variant'):
        choice='/'+cfg['item_wheel_variant'].casefold()+'/'
        if choice not in folder:return 'User selected item wheel: '+cfg['item_wheel_variant']
    if '-1585-' in name and cfg.get('wabo_difficulty'):
        from gameplay_options import wabo_variant
        return wabo_variant(member,cfg)
    if not ('-1102-' in name and ('nightmare' in name or 'fear' in name)):return None
    if logical not in {'sc_tgt_caller.dbsoldierconfig','sc_tgt_caller_raid.dbsoldierconfig'}:return None
    priority=cfg.get('nightmare_caller_priority','New Reinforcements')
    folder='/'+member.replace('\\','/').strip('/').casefold()
    if priority=='New Reinforcements' and '/traffic improvements/' in folder:
        return 'User selected New Reinforcements for the overlapping caller resource'
    if priority=='Traffic Improvements' and '/new reinforcements/' in folder:
        return 'User selected Traffic Improvements for the overlapping caller resource'
    return None
