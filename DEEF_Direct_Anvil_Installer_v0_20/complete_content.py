"""Prepare complete authored assets and merged DB content before route mapping."""
from pathlib import Path
from collections import defaultdict
import copy,json,subprocess,struct,re,hashlib,os
import mod_manager as mm
import batch_engine as be
import auto_mapping as am
import manual_install_rules as rules
import deployment_policy as policy
import forge_audit


def logical(member):return am.deordinal(member)


def inventory(mods):
    seed=am.initial_characterize();outer=defaultdict(list);inner=defaultdict(list)
    for key,mod in mods.items():
        if not mod.get('enabled',True):continue
        if Path(mod['archive']).name.lower().startswith('wabo compatibility tool-'):continue
        archive_hash=be.sha256(mod['archive'])
        for member in mod['members']:
            if rules.excluded_variant(mod['archive'],member) or policy.held_route(mod['archive'],member):continue
            leaf=Path(member.replace('\\','/')).name
            if Path(mod['archive']).name.lower().startswith('disabled cover system-537-') and leaf=='Disable Cover System.txt':
                inner[('DataPC_patch_01.forge','dbcontainerentry_0x104634f921.data')].append({'mod':key,'archive':mod['archive'],'archiveSha256':archive_hash,'member':member,'name':'DBPlayerCover.DBCoverParameters','operation':'remove','forge':'DataPC_patch_01.forge','data':'DBContainerEntry_0X104634F921.data'})
                continue
            if leaf.lower().endswith(('.txt','.md','.pdf','.png','.jpg','.jpeg','.forge','.dll','.ini','.addon64','.pck','.metafile','.exe')):continue
            route=am.infer_member_route(member)
            candidates,_,_=am.candidates_for_member(mod['archive'],member,seed)
            if route and route['forge'].casefold().startswith(('put in ','place in ','copy to ')) and candidates:route=candidates[0]
            if not route and candidates:route=candidates[0]
            if '-1585-' in key:
                route={'forge':'DataPC_patch_01.forge','data':leaf if leaf.lower().endswith('.data') else 'DBContainerEntry_0X104634F921.data','kind':'data' if leaf.lower().endswith('.data') else 'inner'}
            if '-714-' in key:
                route={'forge':'DataPC_Resources_patch_01.forge' if leaf.lower().endswith('.data') else 'DataPC_patch_01.forge','data':leaf if leaf.lower().endswith('.data') else 'TEAMMATE_Template.data','kind':'data' if leaf.lower().endswith('.data') else 'inner'}
            if '-537-' in key and leaf.lower().endswith('.fx'):route={'forge':'DataPC_patch_01.forge','data':'DBContainerEntry_0X104634F921.data','kind':'inner'}
            if Path(mod['archive']).name.lower().startswith('no ads zoom updated-537-') and leaf.lower().endswith('.cameraselectornode'):route={'forge':'DataPC_patch_01.forge','data':'Game Bootstrap Settings.data','kind':'inner'}
            if route is None:continue
            route=am.rc.normalize_route(route)
            item={'mod':key,'archive':mod['archive'],'archiveSha256':archive_hash,'member':member,'name':leaf,'forge':route['forge'],'data':route['data']}
            if route['kind']=='data':outer[route['forge']].append(item)
            else:inner[(route['forge'],logical(route['data']))].append(item)
    return outer,inner


def payload(item):
    return Path(item.get('payload') or mm.extraction_target(item['archive'],item['member'],archive_hash=item.get('archiveSha256')))


def identity(file):
    # The compatibility tool's documented GRB object header, used only for
    # unpacked DB objects. C# independently checks every request's identity.
    raw=Path(file).read_bytes()
    if len(raw)<13 or raw[0]!=0:raise ValueError('Unsupported unpacked object header: '+str(file))
    return struct.unpack_from('<QI',raw,1)


def cache_key(forge,source_hash,request,parent=None):
    rows=[{'name':r['name'],'sha256':be.sha256(r['payload'])} for r in request]
    if parent:
        for entry,row in zip(request,rows):row.update({k:entry.get(k) for k in ('allowNameAlias','allowTypeChange','npcTriggerPriority','operation')})
    revision='PACKED_CHECKSUM_AND_CONSECUTIVE_SET_LAYOUT_3' if parent else 'PACKED_CHECKSUM_AND_OVERRIDE_FIRST_SET_LAYOUT_4'
    return hashlib.sha256(json.dumps({'writerRevision':revision,'forge':forge,'source':source_hash,'parent':parent,'rows':rows},sort_keys=True).encode()).hexdigest()


def verified_loose(mods,game):
    """Credit separate installs only when the selected archive bytes are live."""
    game=Path(game);rows=[]
    for key,mod in mods.items():
        if not mod.get('enabled',True):continue
        audio='-1245-' in key;hud=key.lower().startswith('grbno-hud-no-effectsfix')
        if not (audio or hud):continue
        archive_hash=be.sha256(mod['archive'])
        for member in mod['members']:
            leaf=Path(member.replace('\\','/')).name
            if audio and leaf=='soundspc_sfx_wea_.pck':target=game/'sounddata/pc'/leaf
            elif hud and leaf in ('ShaderToggler.addon64','ShaderToggler.ini'):target=game/leaf
            elif hud and leaf=='dxgi.dll':target=game/'dxgi.dll'
            else:continue
            source=mm.extraction_target(mod['archive'],member,archive_hash=archive_hash)
            digest=be.sha256(source)
            if target.is_file() and be.sha256(target)==digest:
                rows.append({'mod':key,'member':member,'archive':mod['archive'],'archiveSha256':archive_hash,'target':str(target),'payloadSha256':digest})
    return rows


def merge_weapons(items,run,config):
    from weapon_stats_merge import AuthorReader,merge,FIELDS
    reader=AuthorReader(config['wabo_compatibility_tool'])
    grouped=defaultdict(list);others=[];proofs=[]
    for item in items:
        if item['name'].lower().endswith('.gr_weapondbentry'):grouped[identity(payload(item))].append(item)
        else:others.append(item)
    for n,(id_,group) in enumerate(sorted(grouped.items())):
        if len(group)==1:
            item=group[0]
            if logical(item['name'])=='hdg_ubstg_bailiff_clone.gr_weapondbentry' and '-1204-' in item['mod'] and any('-1585-' in i['mod'] for i in items):
                from payload_repairs import repair
                repaired=repair(payload(item),run/'PayloadRepairs'/item['name'],'bailiff_weapon_ui')
                item=dict(item,payload=str(repaired))
            others.append(item);continue
        cosmetic=[i for i in group if '-1155-' in i['mod'] or '-1125-' in i['mod']]
        wabo=[i for i in group if '-1585-' in i['mod']]
        damage=[i for i in group if '-1204-' in i['mod']]
        other=[i for i in group if i not in cosmetic+wabo+damage]
        base=(cosmetic or other or wabo or damage)[0]
        donors=[]
        if wabo and base not in wabo:donors.append((payload(wabo[0]),FIELDS))
        if damage and config.get('weapon_damage_priority','Damage Balance')=='Damage Balance':donors.append((payload(damage[0]),{'damage'}))
        if not donors:
            hashes={be.sha256(payload(i)) for i in group}
            if len(hashes)>1 and len(group)>1:raise ValueError('Unresolved weapon merge: '+base['name'])
            merged=dict(base)
        else:
            out=run/'WeaponMerges'/str(n)/base['name']
            if len(donors)==1 and donors[0][1]=={'damage'}:
                from weapon_stats_merge import merge_damage
                proof=merge_damage(reader,payload(base),donors[0][0],out)
            else:proof=merge(reader,payload(base),donors,out)
            be.record_json(out.with_suffix('.MERGE.json'),proof);proofs.append({'members':[(i['mod'],i['member']) for i in group],'proof':str(out.with_suffix('.MERGE.json'))})
            merged=dict(base,payload=str(out))
        merged['contributions']=[(i['mod'],i['member']) for i in group];others.append(merged)
        merged['donorArchives']=[i['archive'] for i in group]
    if config.get('lmg_accuracy_profile')=='Precision':
        from weapon_stats_merge import edit_fields
        precision=[]
        for item in others:
            name=logical(item['name'])
            if not name.endswith('.gr_weapondbentry') or 'lmg_' not in name:continue
            if name=='lmg_t95_bivouac.gr_weapondbentry':
                precision.append({'name':item['name'],'state':'PRESERVED_UNSUPPORTED_AUTHOR_LAYOUT'});continue
            out=run/'LMGPrecision'/item['name']
            proof=edit_fields(reader,payload(item),{'spreadpattern':reader.db.spreadpattern['SNR_HTI'],'sway':0.2},out)
            item['payload']=str(out);precision.append(dict(name=item['name'],**proof))
        be.record_json(run/'LMG_PRECISION.json',precision)
    if config.get('shotgun_damage_profile')=='200':
        from shotgun_override import player_shotgun,apply
        shotgun=[]
        for item in others:
            if not player_shotgun(item['name']):continue
            out=run/'ShotgunDamage'/item['name']
            proof=apply(reader,payload(item),out)
            item['payload']=str(out);shotgun.append(dict(name=item['name'],**proof))
        be.record_json(run/'SHOTGUN_DAMAGE.json',shotgun)
    be.record_json(run/'WEAPON_MERGES.json',proofs)
    return others


def prepare(mods,view,game,toolkit,run,current,log):
    cfg=mm.load(mm.CONFIG,{})
    if not cfg.get('complete_content_pipeline'):return mods,[]
    outer,inner=inventory(mods);updated=copy.deepcopy(mods);layers=[]
    dll=be.build_bridge(toolkit,log)
    archive_hashes={}
    seq=0
    def compose(forge,items,parent=None):
        nonlocal seq
        seq+=1;folder=run/'CompleteContents'/str(seq);folder.mkdir(parents=True)
        source=view/forge;req=[];covered=defaultdict(list);archives={}
        for item in items:
            path=payload(item)
            if '-1159-' in item['mod'] and logical(item['name'])=='dbbooleanstatdescriptor_canusewhitehot.dbbooleanstatdescriptor':
                from payload_repairs import repair
                path=repair(path,run/'PayloadRepairs'/item['name'],'white_hot_identity')
            currency_change=('-537-' in item['mod'] and logical(item['name'])=='dblootableset_goliath_exploded.dblootablecurrency') or '-1585-' in item['mod']
            req.append({'name':item['name'],'payload':str(path),'operation':item.get('operation','replace'),'allowNameAlias':'-1585-' in item['mod'],'allowTypeChange':currency_change,'npcTriggerPriority':cfg.get('npc_trigger_priority','Various Tweaks single trigger')})
            for key,member in item.get('contributions',[(item['mod'],item['member'])]):covered[key].append(member)
            for archive in [item['archive']]+item.get('donorArchives',[]):
                if archive not in archive_hashes:archive_hashes[archive]=be.sha256(archive)
                archives[archive]=archive_hashes[archive]
        be.record_json(folder/'REQUEST.json',req);proof=folder/'PROOF.json'
        target=str(game/forge);key=cache_key(forge,current[target],req,parent)
        cache_file=mm.WORK/'content_cache/INDEX.json';cache=mm.load(cache_file,{})
        cached=cache.get(key);result=None
        if cached:
            oldproof=Path(cached['proof']);oldoutput=Path(cached['output'])
            if oldproof.is_file() and oldoutput.is_file():
                candidate=json.loads(oldproof.read_text(encoding='utf-8'))
                if candidate.get('verified') is True and candidate.get('independentAnvilIndexVerified') is True and candidate.get('sourceSha256','').casefold()==current[target] and be.sha256(oldoutput)==candidate.get('outputSha256','').casefold():
                    output=folder/'Cached'/forge;output.parent.mkdir();os.link(oldoutput,output)
                    result=dict(candidate,sourceForge=str(source),output=str(output),reusedVerifiedProof=str(oldproof),requestFingerprint=key)
                    be.record_json(proof,result);log('Reused exact verified composition; output fingerprint checked: '+forge)
        command=['dotnet',str(dll),'compose-inner' if parent else 'compose-assets',str(toolkit)]
        output=folder/'Output'/forge
        if parent:
            output.parent.mkdir();command.extend([str(source),parent,str(folder/'REQUEST.json'),str(proof),str(output)])
        else:command.extend([str(view),str(source),str(folder/'REQUEST.json'),str(proof)])
        log(f'Complete content: {forge} / {parent or "authored containers"} ({len(req)} payloads)')
        if result is None:
            with (folder/'CONSOLE.txt').open('w',encoding='utf-8') as f:p=subprocess.run(command,stdout=f,stderr=subprocess.STDOUT)
            result=json.loads(proof.read_text(encoding='utf-8')) if proof.is_file() else {}
            if p.returncode or result.get('verified') is not True:raise ValueError('Complete content composition failed: '+str(proof)+' : '+str(result.get('error',''))[-450:])
            cache_file.parent.mkdir(parents=True,exist_ok=True)
            cache[key]={'proof':str(proof),'output':result['output']};mm.save(cache_file,cache)
        output=Path(result['output']);target=str(game/forge);digest=be.sha256(output)
        if result['sourceSha256'].casefold()!=current[target] or result['outputSha256'].casefold()!=digest:raise ValueError('Content composition hash chain differs')
        index=forge_audit.read_index(output)
        layer={'mod':'Complete selected content','archive':items[0]['archive'],'archiveSha256':archives[items[0]['archive']],
            'sourceArchives':archives,'member':'COMPLETE_CONTENT_'+str(seq),'forge':target,'kind':'composed_inner' if parent else 'composed_assets',
            'phase':'GUIDED_CONTENT','tier':seq,'data':parent or '', 'object':'','inputSha256':current[target],'outputSha256':digest,
            'anvilVerified':True,'indexEntries':len(index['entries']),'contentReceipt':str(proof),'coveredMembers':dict(covered)}
        source.unlink();
        from layered_pipeline import link_copy
        link_copy(output,source);current[target]=digest;layers.append(layer)
        for key,members in covered.items():updated[key]['members']=[m for m in updated[key]['members'] if m not in members]
        log(f'Complete content verified: {sum(map(len,covered.values()))} supplied payload contributions')
    for forge,items in sorted(outer.items()):
        # Reticle base/patch names may represent the same texture with two author
        # versions. Keep the explicit patch source when both share binary identity.
        if any('-42-' in i['mod'] for i in items):
            pass # Keep separate Forge destinations; author package layout matters.
        compose(forge,items)
    for (forge,data),items in sorted(inner.items()):
        if 'dbcontainerentry' in data:items=merge_weapons(items,run,cfg)
        index=forge_audit.read_index(view/forge)
        matches=[e for e in index['entries'] if logical(e['name']+'.data')==data]
        if len(matches)!=1:raise ValueError('Complete inner parent unresolved: '+forge+'/'+data)
        parent=str(index['entries'].index(matches[0]))+'_-_'+matches[0]['name']+'.data'
        compose(forge,items,parent)
    present=[]
    for key,mod in mods.items():
        if not mod.get('enabled',True):continue
        for member in mod['members']:
            if not member.lower().endswith('.metafile'):continue
            match=re.search(r'(DataPC[^/\\]*\.forge)',member,re.I)
            if not match:raise ValueError('Global metafile has no declared Forge: '+member)
            forge=match.group(1);index=forge_audit.read_index(view/forge)
            entries=[e for e in index['entries'] if e['id']=='0000000000000010' and e['type']==0]
            if len(entries)!=1:raise ValueError('Global metafile identity missing/ambiguous')
            entry=entries[0];desired=be.sha256(payload({'archive':mod['archive'],'member':member}))
            with (view/forge).open('rb') as f:f.seek(entry['offset']);actual=hashlib.sha256(f.read(entry['length'])).hexdigest()
            if actual!=desired:raise ValueError('Provided global metafile differs from composed foundation: '+member)
            present.append({'mod':key,'member':member,'forge':str(game/forge),'id':entry['id'],'type':0,'payloadSha256':desired,'archive':mod['archive'],'archiveSha256':be.sha256(mod['archive'])})
            updated[key]['members'].remove(member)
    be.record_json(run/'PRESENT_PAYLOADS.json',present)
    loose=verified_loose(mods,game)
    for row in loose:updated[row['mod']]['members'].remove(row['member'])
    be.record_json(run/'LOOSE_PAYLOADS.json',loose)
    return updated,layers
