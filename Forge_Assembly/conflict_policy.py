"""Shared, explicit priorities for composition and schema-aware weapon fields."""
from pathlib import Path
import re
import batch_engine as be

FIELD_GROUPS={
 'damage':{'damage'},
 'ballistics':{'range','projectilebreakingangle','zero','penetration1','penetration2','velocity2','ui_range','ammotype'},
 'handling':{'timetoaim','burdenfactor','walkspeedmultiplier','mobility','reloadtime','tacticalreloadthreshold','ui_weight'},
 'recoil_spread':{'sway','recoilpattern','spreadpattern'},
 'fire_rate':{'rateoffire','ui_rateoffire'},
 'sound':{'soundfile','soundrange','ui_noise'},
 'sensitivity':{'adssensitivity'}}

BUILD_KEYS=('conflict_priorities','weapon_damage_priority','wabo_difficulty','thermal_priority','item_wheel_variant',
 'nightmare_caller_priority','preserve_game_sensitivity','foliage_profile','npc_trigger_priority','bcm_handguard',
 'preserve_player_automatic_fire','preserve_night_vision','shotgun_damage_profile','lmg_accuracy_profile',
 'ammo_capacity_profile','suppressor_profile','complete_content_pipeline','use_updated_ads_zoom','native_package','native_preset_choices',
 'native_numeric','native_toggle_key','native_chain_proxy','native_graphics_compatibility','native_dx11_single_loader',
 'reshade_setup','reshade_preset','reshade_shader_folder','reshade_runtime_override','enable_shader_toggler_addon')

def build_settings(cfg):
 import additional_options
 return {**{key:cfg.get(key) for key in BUILD_KEYS},'weapon_variants':additional_options.load()['weapon_variants']}

def logical(name):return re.sub(r'^\d+_-_','',str(name).replace('\\','/').rsplit('/',1)[-1]).casefold()
def key(forge,parent,name):return '|'.join((Path(forge).name.casefold(),logical(parent or ''),logical(name)))
def settings(cfg):
 value=cfg.get('conflict_priorities',{})
 if not isinstance(value,dict):raise ValueError('Invalid conflict priorities')
 order=value.get('mod_order',[])
 if not isinstance(order,list) or any(not isinstance(v,str) or not v for v in order) or len(order)!=len(set(order)):raise ValueError('Invalid or duplicate mod priority')
 for name in ('resource_winners','weapon_fields'):
  if not isinstance(value.get(name,{}),dict):raise ValueError('Invalid priority section: '+name)
 for target,row in value.get('resource_winners',{}).items():
  if not isinstance(target,str) or target.count('|')!=2 or not isinstance(row,dict) or not all(isinstance(row.get(k),str) and row[k] for k in ('mod','member','archiveSha256')) or not re.fullmatch('[0-9a-f]{64}',row['archiveSha256']):
   raise ValueError('Invalid hash-bound resource priority')
 for category,mod in value.get('weapon_fields',{}).items():
  if category not in FIELD_GROUPS or not isinstance(mod,str):raise ValueError('Invalid weapon field priority')
 return value

def priority(item,cfg,forge,parent=None):
 policy=settings(cfg);target=key(forge,parent,item['name'])
 scope=target.rsplit('|',1)[0]
 for resource,choice in policy.get('resource_winners',{}).items():
  if resource.rsplit('|',1)[0]!=scope:continue
  if choice.get('mod')==item['mod'] and choice.get('member')==item['member']:
   digest=item.get('archiveSha256') or be.sha256(item['archive'])
   if digest!=choice.get('archiveSha256'):raise ValueError('Saved conflict choice belongs to a changed archive: '+item['mod'])
   return 1000000
 order=policy.get('mod_order',[])
 return len(order)-order.index(item['mod']) if item['mod'] in order else None

def choose(items,cfg,forge,parent=None):
 ranks=[priority(i,cfg,forge,parent) for i in items]
 ranked=[r for r in ranks if r is not None]
 if not ranked:return None
 top=max(ranked);winners=[i for i,r in zip(items,ranks) if r==top]
 if top!=1000000 and any(r is None for r in ranks):return None
 if len(winners)!=1:return None
 return winners[0]

def candidate(item):
 result={k:item[k] for k in ('mod','member','archiveSha256','name') if k in item}
 if 'archiveSha256' not in result:result['archiveSha256']=be.sha256(item['archive'])
 return result

def record(run,row):
 path=Path(run)/'CONFLICT_POLICY_REPORT.json'
 import json
 doc=json.loads(path.read_text()) if path.exists() else {'schema':'GRB_CONFLICT_POLICY_1','groups':[]}
 doc['groups']=[r for r in doc['groups'] if r['target']!=row['target']]+[row]
 be.record_json(path,doc)

def inspect_group(items,cfg,forge,parent,run):
 target=key(forge,parent,items[0]['name'])
 choice=settings(cfg).get('resource_winners',{}).get(target)
 if choice and not any(choice.get('mod')==i['mod'] and choice.get('member')==i['member'] for i in items):
  raise ValueError('Saved conflict winner is absent from current selection: '+target)
 winner=choose(items,cfg,forge,parent)
 record(run,dict(target=target,candidates=[candidate(i) for i in items],winner=candidate(winner) if winner else None,
  state='CONFIGURED_PRIORITY' if winner else 'AUTOMATIC_MERGE_OR_REVIEW',gameplayVerified=False))
 return winner

def completed(run,forge,parent,proof,exclusions):
 path=Path(run)/'CONFLICT_POLICY_REPORT.json'
 if not path.is_file():return
 import json
 doc=json.loads(path.read_text());scope=key(forge,parent,'resource').rsplit('|',1)[0]
 for group in doc['groups']:
  if group['target'].rsplit('|',1)[0]!=scope:continue
  group.update(state='VERIFIED_COMPOSED',compositionProof=str(proof),gameplayVerified=False)
  group['excludedCandidates']=[r for r in exclusions if any(c['mod']==r['mod'] and c['member']==r['member'] for c in group['candidates'])]
 be.record_json(path,doc)

def container_groups(run,forge,folder,items):
 path=Path(folder)/'CONTAINER_CONFLICT_GROUPS.json'
 if not path.is_file():return
 import json
 groups=json.loads(path.read_text())
 if not groups:return
 report=Path(run)/'CONFLICT_POLICY_REPORT.json'
 doc=json.loads(report.read_text()) if report.exists() else {'schema':'GRB_CONFLICT_POLICY_1','groups':[]}
 scope=key(forge,None,'resource').rsplit('|',1)[0]
 doc['groups']=[g for g in doc['groups'] if g['target'].rsplit('|',1)[0]!=scope]
 for group in groups:
  selected=[i for i in items if any(i['mod']==r['mod'] and i['member']==r['member'] for r in group['candidates'])]
  if len(selected)!=len(group['candidates']):raise ValueError('Container conflict report has unknown source members')
  identity=group['id']+':'+group['type']
  doc['groups'].append(dict(target=scope+'|#'+identity,label=Path(forge).name+' / '+group['name']+' ['+identity+']',
   state='VERIFIED_BINARY_CONFLICT_GROUP',candidates=[candidate(i) for i in selected],binaryIdentityVerified=True,gameplayVerified=False))
 be.record_json(report,doc)

def field_donors(group,cfg,payload):
 donors=[];report=[]
 for category,mod in settings(cfg).get('weapon_fields',{}).items():
  if category not in FIELD_GROUPS:raise ValueError('Unsupported weapon field priority: '+category)
  if not mod:continue
  found=[i for i in group if i['mod']==mod]
  if not found:continue # This mod need not provide every weapon.
  hashes={be.sha256(payload(i)) for i in found}
  if len(hashes)>1:raise ValueError('Weapon field priority has competing variants: '+mod)
  donors.append((payload(found[0]),FIELD_GROUPS[category]))
  report.append(dict(category=category,mod=mod,fields=sorted(FIELD_GROUPS[category])))
 return donors,report

def apply_route_priorities(entries,cfg,run):
 from resolver import conflict_groups
 count=0
 for group in conflict_groups(entries).values():
  rows=[r for r in group['rows'] if r['status']=='VERIFIED_LOCAL_TARGET']
  if len(rows)<2:continue
  route=rows[0]['resolved'];parent=route['data'] if route['kind']=='inner' else None
  name=route['object'] if parent else route['data'];items=[]
  for row in rows:
   archive=row['resolved']['sourceArchive']
   items.append(dict(mod=row['archive'],member=row['member'],archive=archive,name=name,archiveSha256=be.sha256(archive)))
  winner=inspect_group(items,cfg,route['forge'],parent,run)
  if winner is None:continue
  for row in rows:
   if row['archive']==winner['mod'] and row['member']==winner['member']:continue
   row.update(status='USER_CONFLICT_EXCLUDED',explanation='Saved priority chose another identity-verified resource for this destination')
   count+=1
 return count
