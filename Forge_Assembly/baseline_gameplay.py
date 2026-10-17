"""Read verified vanilla objects for installer-generated gameplay overlays."""
import storage_guard
from pathlib import Path
import json,re,subprocess
import batch_engine as be
import mod_manager as mm
import performance_trace as perf
from gameplay_options import logical_name

INPUTS=('Filter_MouseSensitivity_Aim','Filter_MouseSensitivity_default')
SHARED_TRIGGERS=('TriggerAttachment_FullAuto_Single','TriggerAttachment_FullAuto_Burst3','TriggerAttachment_Full_Burst2')
VEHICLE_TRIGGERS=tuple('DBWeaponSmith_AttachmentPerWeaponSetting_'+name for name in
    ('AAC','AGMV_MainGun','AGMV_MiniGunL','AGMV_MiniGunR','BLITZPMVCANON','EXOTURRETM2LEFT','EXOTURRETM2RIGHT','PMVSPARTAN','GATLINGHEAVYNPC','GATLINGSPARTAN'))
TRIGGERS=SHARED_TRIGGERS+VEHICLE_TRIGGERS
WHEEL=('DBItemSetting_HealingItemEssence_HealingSyringes',)
CONCEALMENT=('DBAIBushConfig_Default','DBAIBushConfig_SC_TGT_Grenadier','DBNpcPerceptionConfig_Default','DBNpcPerceptionConfig_SC_TGT_Grenadier','DBSoldierVisualDetectionConfig_Fighter','DBSoldierVisualDetectionConfig_Vantage','DBSoldierVisualDetectionConfig_Vantage_Wolves','DBSoldierVisualDetectionConfig_Wolves','DBSoldierVisualDetectionConfig_SC_TGT_Grenadier')

@perf.timed('baseline.gameplay')
def prepare(items,sources,toolkit,run,cfg,log):
    if not sources:return items,{}
    root=Path(run)/'VanillaGameplay';source=next((Path(v) for k,v in sources.items() if Path(k).name.casefold()=='datapc_patch_01.forge'),None)
    if source is None:raise ValueError('Clean main Forge required for gameplay options')
    import deployment_policy as policy
    original=next(k for k in sources if Path(k).name.casefold()=='datapc_patch_01.forge')
    if be.sha256(source)!=policy.require_ready(Path(original).parent)['hashes'][original]:
        raise ValueError('Gameplay overlay source is not the verified vanilla Forge')
    from layered_pipeline import link_copy
    pinned=root/'Source'/source.name;link_copy(source,pinned)
    dll=mm.ROOT/'bin/DEEF.AnvilBridgeProbe.dll'
    def extract(names,folder):
        request=root/(folder+'.json');be.record_json(request,names)
        with (root/'EXTRACTION_CONSOLE.txt').open('a',encoding='utf-8') as console:
            proc=storage_guard.run(['dotnet',str(dll),'cached-extract-short-existing',str(toolkit),str(pinned),'DBContainerEntry_0X104634F921.data',str(request),str(root/folder)],stdout=console,stderr=subprocess.STDOUT)
        if proc.returncode:raise ValueError('Vanilla object extraction failed: '+str(root/'EXTRACTION_CONSOLE.txt'))
        proof=json.loads((root/folder/'READ_ONLY_EXTRACTION.json').read_text())
        if not proof.get('sourceUnchanged') or proof['sourceSha256']!=be.sha256(pinned):raise ValueError('Vanilla extraction source changed')
        return proof
    full=[]
    if cfg.get('preserve_game_sensitivity',True):full+=list(INPUTS)
    if cfg.get('preserve_player_automatic_fire',True):full+=list(SHARED_TRIGGERS)
    if cfg.get('npc_trigger_priority','Vanilla automatic fire')=='Vanilla automatic fire':full+=list(VEHICLE_TRIGGERS)
    if cfg.get('item_wheel_variant')=='Vanilla item wheel':full+=list(WHEEL)
    if cfg.get('foliage_profile')=='Vanilla concealment':full+=list(CONCEALMENT)
    be.record_json(root/'GAMEPLAY_PLAN.json',{'source':str(pinned),'sourceSha256':be.sha256(pinned),'completeVanillaDefinitions':full,'liveGameModified':False})
    names={name.casefold() for name in full}
    for item in items:
        if item['name'].lower().endswith('.gr_weapondbentry') and cfg.get('preserve_game_sensitivity',True):
            names.add(logical_name(item['name']))
    proof=extract(sorted(names),'Objects')
    available={r['name'].casefold():r for r in proof['available']}
    stock={r['name'].casefold():Path(r['file']) for r in proof['rows']}
    result=list(items)
    for name in full:
        key=name.casefold()
        if key not in stock:raise ValueError('Required vanilla gameplay resource missing: '+name)
        previous=[r for r in result if logical_name(r['name'])==key]
        result=[r for r in result if r not in previous]
        contributions=[c for r in previous for c in r.get('contributions',[(r['mod'],r['member'])])]
        canonical='1_-_'+available[key]['name']+'.'+available[key]['extension']
        contributions.append(('Installer gameplay options',canonical))
        result.append(dict(mod='Installer gameplay options',archive=str(pinned),member=canonical,name=canonical,
            payload=str(stock[key]),contributions=contributions,donorArchives=[r['archive'] for r in previous],forge='DataPC_patch_01.forge',data='DBContainerEntry_0X104634F921.data'))
    log(f'Verified vanilla gameplay inputs: {len(full)} complete definitions; {len(stock)} available field donors. Live game unchanged.')
    return result,stock


def restore_sensitivity(reader,items,stock,run):
    from weapon_stats_merge import FieldReader,merge
    # The author's reader branches on names such as HDG, Maxim9 and MK23.
    # Keep that semantic name even when the private disk filename is shortened.
    if stock:
        metadata=next(iter(stock.values())).parent/'READ_ONLY_EXTRACTION.json'
        if metadata.is_file():
            entries=json.loads(metadata.read_text())['rows']
            reader.canonical_names={int(row['id'],16):'1_-_'+row['name']+'.GR_WeaponDBEntry' for row in entries
                if Path(row['file']).suffix.casefold()=='.gr_weapondbentry'}
    fields=FieldReader(reader,{'adssensitivity'});rows=[]
    for n,item in enumerate(items):
        key=logical_name(item['name']);donor=stock.get(key)
        if donor is None or not item['name'].lower().endswith('.gr_weapondbentry'):continue
        # Vehicle/NPC/custom layouts are not interpreted as player weapons.
        if not re.match(r'^(?:asr_|smg_|dmr_|snr_|lmg_|stg_|hdg_|unique_|y[0-9]|yee)',key) or any(t in key for t in ('_npc','_ia','prog_test')):continue
        base=Path(item.get('payload') or __import__('complete_content').payload(item))
        out=Path(run)/'VanillaSensitivity'/str(n)/item['name']
        try:proof=merge(fields,base,[(donor,{'adssensitivity'})],out)
        except ValueError as exc:
            rows.append(dict(name=item['name'],state='PRESERVED_UNSUPPORTED_LAYOUT',reason=str(exc)));continue
        item['payload']=str(out);rows.append(dict(name=item['name'],**proof))
    be.record_json(Path(run)/'VANILLA_SENSITIVITY.json',rows)
    return rows
