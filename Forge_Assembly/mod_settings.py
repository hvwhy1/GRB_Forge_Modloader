"""Folder-backed settings catalog. Hidden preferences are retained, never applied by UI refresh."""
import copy,json
from pathlib import Path
import mod_manager as mm


def present_mods(cfg,state=None):
    """Use the current folder, not old library entries, as the source of presence."""
    folder=Path(cfg.get('archive_dir',''))
    if not cfg.get('archive_dir') or not folder.is_dir():return {}
    state=mm.load(mm.STATE,{}) if state is None else state
    result={}
    for path in sorted(folder.rglob('*')):
        if not path.is_file() or path.suffix.lower() not in mm.MOD_EXT:continue
        key=path.relative_to(folder).as_posix();old=state.get(key,{})
        row=dict(old,archive=str(path),relativeArchive=key)
        if old.get('size')!=path.stat().st_size or ('mtimeNs' in old and old['mtimeNs']!=path.stat().st_mtime_ns):row['members']=[]
        result[key]=row
    return result


def needs_refresh(cfg,state=None):
    if not cfg.get('archive_dir') or not Path(cfg['archive_dir']).is_dir():return False
    state=mm.load(mm.STATE,{}) if state is None else state
    current=present_mods(cfg,state)
    if set(state)!=set(current):return True
    return any(state[k].get('size')!=Path(v['archive']).stat().st_size or
               state[k].get('mtimeNs')!=Path(v['archive']).stat().st_mtime_ns for k,v in current.items())


def options(mods):
    """Each profile declares the archive/content capability it needs."""
    enabled={k:v for k,v in mods.items() if v.get('enabled',True)}
    names='\n'.join(enabled).casefold()
    members='\n'.join(m for v in enabled.values() for m in v.get('members',[])).casefold()
    wabo=any('-1585-' in k.casefold() and 'compatibility' not in k.casefold() for k in enabled)
    damage='-1204-' in names
    weapons=wabo or damage or '.gr_weapondbentry' in members
    tweaks='various tweaks' in names
    result=[]
    def add(key,label,values,default):result.append((key,label,values,default))
    if wabo and damage:add('weapon_damage_priority','Weapon damage in merged files',['Damage Balance','WABO'],'Damage Balance')
    if wabo:
        difficulties=['1_Easy','2_Normal','3_Hard','4_Realistic (Hardcore)']
        available=[v for v in difficulties if '/'+v.casefold()+'/' in members]
        add('wabo_difficulty','WABO difficulty',available or difficulties,'4_Realistic (Hardcore)')
        add('ammo_capacity_profile','Ammo reserve',['Difficulty preset','Vanilla'],'Vanilla')
        from gameplay_options import SUPPRESSOR_PRESETS
        suppressors=[label for label,folder in SUPPRESSOR_PRESETS.items() if '/suppressors/'+folder.casefold()+'/' in members]
        add('suppressor_profile','Suppressor preset (independent of difficulty)',['Difficulty preset']+suppressors,'Normal' if 'Normal' in suppressors else 'Difficulty preset')
    vision=['Standard night vision and thermal']
    if '-1159-' in names:vision.append('Pathfinder white thermal vision')
    if '-489-' in names:vision.append('Disable thermal vision')
    if len(vision)>1:add('thermal_priority','Thermal vision',vision,vision[0])
    if tweaks and 'item wheel swap/' in members:
        variants=['Vanilla item wheel']+[v for v in ['Syringe and Binoculars','Syringe and Breaching torch'] if '/'+v.casefold()+'/' in members]
        add('item_wheel_variant','Item wheel (incompatible swaps remain held)',variants,'Vanilla item wheel')
    if '-1102-' in names:
        values=[v for v in ['New Reinforcements','Traffic Improvements'] if v.casefold() in members]
        if len(values)>1:add('nightmare_caller_priority','Nightmare caller resources',values,'New Reinforcements')
    if weapons or tweaks or 'no ads zoom' in names:
        add('preserve_game_sensitivity','Sensitivity / optic FOV',['Game settings','Mod settings'],'Game settings')
    if wabo or tweaks or '-1102-' in names:
        add('foliage_profile','Enemy vision / concealment',['Mod settings','Vanilla concealment'],'Vanilla concealment')
    if tweaks:add('npc_trigger_priority','Vehicle / NPC trigger rules',['Vanilla automatic fire','Various Tweaks single trigger'],'Vanilla automatic fire')
    if '-1807-' in names:
        values=[v for v in ['Black','FDE'] if '/'+v.casefold()+' handguard/' in members]
        if len(values)>1:add('bcm_handguard','BCM Koblin handguard',values,'Black')
    if weapons:
        add('lmg_accuracy_profile','LMG firing accuracy',['Original','Precision'],'Precision')
        add('shotgun_damage_profile','Player shotgun damage',['Author values','100','125','150','175','200'],'150')
    return result


def learned_groups(mods,work=None):
    """Retain discovered alternatives across builds, scoped to current archive members.

    No archive hashing on Tk's thread. The builder rechecks the saved SHA-256
    before using a choice. A replaced archive cannot inherit an unverified winner.
    """
    work=Path(work or mm.WORK)
    reports=[p/'CONFLICT_POLICY_REPORT.json' for root in (work/'auto_runs',work/'diagnostics/run_history') if root.is_dir()
             for p in root.iterdir() if p.is_dir() and (p/'CONFLICT_POLICY_REPORT.json').is_file()]
    groups={}
    for path in sorted(reports,key=lambda p:p.stat().st_mtime):
        try:doc=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):continue
        for original in doc.get('groups',[]):
            rows=[c for c in original.get('candidates',[]) if c.get('mod') in mods and mods[c['mod']].get('enabled',True)
                  and c.get('member') in mods[c['mod']].get('members',[])]
            # Keep a newer conflict's full candidate set, not a later one-candidate
            # inspection that would hide the alternatives learned from that mod.
            if len({(c['mod'],c['member']) for c in rows})<2:continue
            group=copy.deepcopy(original);group['candidates']=rows;groups[group['target']]=group
    return sorted(groups.values(),key=lambda g:g.get('label',g['target']))


def weapon_donors(mods):
    return sorted(k for k,v in mods.items() if v.get('enabled',True) and
                  any(m.casefold().endswith('.gr_weapondbentry') for m in v.get('members',[])))
