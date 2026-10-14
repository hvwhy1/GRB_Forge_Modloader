"""DEEF v0.17 integrated per-run path characterization and validated automatic mapping.

Paths from historical mod installers are HINTS, never trusted installation routes.
The user chooses only a mod directory, game root and toolkit. Every candidate
is checked against the installed game's actual extracted Forge/.data members.
Forge and data extraction use the proven read-only AnvilBridge inspector.
Unsupported entries are reported and never silently installed.
"""
from __future__ import annotations
import collections
import json
import os
from pathlib import Path
import re
import time

import batch_engine as be
import mod_manager as mm
import route_conventions as rc

ROOT = Path(__file__).resolve().parent
HINTS_FILE = ROOT/'data'/'v06_route_hints.json'
LIST_FILE = ROOT/'data'/'deef_modlist_v3_2_5.json'


def normal(s):
    return str(s).replace('\\','/').strip('/').casefold()


def base(s):
    return normal(s).rsplit('/', 1)[-1]


def deordinal(s):
    return be.logical(str(s).replace('\\','/').rsplit('/',1)[-1]).casefold()


def valid_name(s):
    return isinstance(s,str) and bool(s) and '/' not in s and '\\' not in s and '..' not in s and '\x00' not in s


def initial_characterize():
    """Load bundled prior path evidence *automatically*, not an external input."""
    d=json.loads(HINTS_FILE.read_text(encoding='utf-8'))
    by_archive=collections.defaultdict(lambda: collections.defaultdict(list))
    for rec in d['records']:
        by_archive[base(rec['archive'])][normal(rec['file'])].append(rec)
    return by_archive


def infer_member_route(member):
    """Recognize .forge or a legacy DataPC_* directory, then .data/object.

    Weapon names and option folders are ignored for destination *discovery*;
    the remaining exact payload identity is separately checked by the writer.
    """
    p=rc.parse_explicit_parent(member)
    if not p:return None
    if p['kind']=='inner' and not valid_name(p['object']):return None
    evidence=('explicit legacy DataPC Forge folder' if p.get('legacyForgeFolderAlias')
              else 'explicit archive Forge/.data path')
    return {'kind':p['kind'],'forge':p['forge'],'data':p['data'],
            'object':p['object'],'evidence':evidence}


def from_parts(parts, evidence):
    if not isinstance(parts,list) or len(parts)<2:return None
    parts=[x for x in parts if str(x).casefold()!='extracted']
    if len(parts)==2 and parts[0].lower().endswith('.forge') and parts[1].lower().endswith('.data'):
        return {'kind':'data','forge':parts[0],'data':parts[1],'object':'','evidence':evidence}
    if len(parts)==3 and parts[0].lower().endswith('.forge') and parts[1].lower().endswith('.data') and valid_name(parts[2]):
        return {'kind':'inner','forge':parts[0],'data':parts[1],'object':parts[2],'evidence':evidence}
    return None


def candidates_for_member(archive, member, historic):
    if Path(archive).name.lower().startswith('wabo compatibility tool-'):
        return [],'INSTALLER_HELPER','IGNORED_SUPPORT_FILE'
    result=[];seen=set()
    # Strip install-instruction prefixes only from recognized .data basenames.
    # Weapon/option directory segments are not physical Forge entries.
    hint=historic.get(base(archive), {}).get(normal(member), [])
    historical_state='NOT_IN_V06_REPORT'
    if hint:
        states={r['state'] for r in hint}
        historical_state=','.join(sorted(states))
        if states & {'exclude'}:
            return [],historical_state,'IGNORED_SUPPORT_FILE'
        if states & {'manual_foundation'}:
            return [],historical_state,'REQUIRES_BASEPACK_SETUP'
        if states & {'separate_install'}:
            return [],historical_state,'UNSUPPORTED_LOOSE_INSTALL'
        for rec in hint:
            paths=[]
            if rec.get('destination'):paths.append((rec['destination'], 'historic '+rec['state']))
            for alt in rec.get('candidates',[]):paths.append((alt, 'historic candidate'))
            for parts,ev in paths:
                c=from_parts(parts,ev)
                if c:
                    c=rc.normalize_route(c)
                    k=(c['kind'],normal(c['forge']),deordinal(c['data']),deordinal(c['object']))
                    if k not in seen:seen.add(k);result.append(c)
    inferred=infer_member_route(member)
    if inferred:
        inferred=rc.normalize_route(inferred)
        k=(inferred['kind'],normal(inferred['forge']),deordinal(inferred['data']),deordinal(inferred['object']))
        if k not in seen:result.append(inferred)
    return [rc.normalize_route(c) for c in result],historical_state,''


def game_forges(game_root):
    root=Path(game_root).resolve()
    if not (root/'GRB.exe').is_file():raise ValueError('Game folder must contain GRB.exe')
    # The installed game resolves .forge files from the game root. Previous
    # versions recursively included Backups\*.forge, producing multiple paths
    # with the same basename and preventing ANY route from being resolved.
    # Never use a backup/old package as an eligible destination, even if it is
    # the only copy with a matching name. No recursive fallback is permitted.
    result=collections.defaultdict(list)
    for p in root.iterdir():
        if p.is_file() and p.suffix.casefold()=='.forge' and p.resolve().parent==root:
            result[p.name.casefold()].append(p.resolve())
    return result


def unique(names, query, *, obj=False):
    if not valid_name(query):return None,'INVALID_RESOURCE_NAME'
    # Entry ordinals are NOT stable resource IDs: Forge serialization may
    # renumber them. If two game entries have the same logical name, even a
    # coincidentally matching ordinal is insufficient to select one safely.
    logical_matches=[x for x in names if (be.object_stem(x)==be.object_stem(query)
                                        if obj else deordinal(x)==deordinal(query))]
    if len(logical_matches)>1:
        return None,'DUPLICATE_LOGICAL_OBJECT_NEEDS_STABLE_ID' if obj else 'DUPLICATE_LOGICAL_DATA_NEEDS_STABLE_ID'
    if len(logical_matches)==1:return logical_matches[0],''
    return None,'NOT_IN_INSTALLED_GAME'


def analyze(mods, game_root, toolkit, *, run_dir, inspect=None, log=lambda s:None,
            full_game_scan=False):
    """Build verified exact resource routes; no external characterization file required.

    Inspect function signature (toolkit, forge, data, work, log) matches v0.16.
    All stages write progressive reports, including failure diagnostics.
    """
    game=Path(game_root).resolve();kit=Path(toolkit).resolve();run_dir=Path(run_dir).resolve()
    if be.within(game,run_dir) or be.within(kit,run_dir):
        raise ValueError('Workspace must be outside game and toolkit folders')
    run_dir.mkdir(parents=True,exist_ok=False)
    if not (kit/'AnvilToolkit.dll').is_file() or not (kit/'Libs').is_dir():
        raise ValueError('Invalid AnvilToolkit directory')
    forges=game_forges(game)
    # Report the recursive copies excluded from the active index; these must
    # NEVER be silently selected as install targets. This is information only.
    excluded_copies=[]
    for f in game.rglob('*.forge'):
        if f.is_file() and f.parent.resolve()!=game:
            excluded_copies.append(str(f.resolve()))
    excluded_by_name=collections.defaultdict(list)
    for f in excluded_copies:
        excluded_by_name[Path(f).name.casefold()].append(f)
    log('Active game-root Forges: {}. Excluded nested/backup copies: {}.\n'.format(
        sum(len(v) for v in forges.values()),len(excluded_copies)))
    limit=128*1024*1024  # STAGING limit; read-only metadata indexing has a separate 64 GiB cap
    # Inventory every Forge name and size BEFORE extraction, even if it cannot be
    # inspected. This distinguishes a completed scan loop from a successful scan.
    census=[]
    for matches in sorted(forges.values(), key=lambda group:group[0].name.casefold()):
        for forge in matches:
            size=forge.stat().st_size
            census.append({'forge':str(forge),'name':forge.name,'bytes':size,
                           'mib':round(size/1048576,2),
                           'withinBridgeSizeLimit':size<=limit,
                           'readOnlyIndexEligible':size<=64*1024**3,
                           'indexStatus':'NOT_ATTEMPTED','error':''})
    census_by_path={str(Path(x['forge']).resolve()).casefold():x for x in census}
    be.record_json(run_dir/'GAME_FORGE_CENSUS.json',
        {'schema':'DEEF.game_forge_census.1','validatedWriteLimitMiB':128,'readOnlyIndexLimitGiB':64,'archives':census})
    oversized=[e for e in census if not e['withinBridgeSizeLimit']]
    log('Game Forge census: {} archives, {} exceed 128 MiB validated WRITE limit; attempting metadata-only indexing.\n'.format(
        len(census),len(oversized)))
    seed=initial_characterize()
    inspect=inspect or be.bridge_inspect
    forge_cache={};data_cache={};failed_cache={}
    # Persistent index is an acceleration only. Actual staging independently re-reads
    # the target Forge and checks the payload hashes, never trusting this cache.
    cachefile=ROOT/'workspace'/'GAME_ASSET_CACHE.json'
    try:
        disk_cache=json.loads(cachefile.read_text(encoding='utf-8'))
        if disk_cache.get('schema')!=1:disk_cache={'schema':1,'entries':{}}
    except (OSError,ValueError):disk_cache={'schema':1,'entries':{}}
    def do_inspect(forge,selector):
        census_row=census_by_path.get(str(Path(forge).resolve()).casefold())
        k=(str(forge).casefold(),selector.casefold())
        cache=forge_cache if selector=='AUTO' else data_cache
        if k in cache:return cache[k]
        if k in failed_cache:raise RuntimeError(failed_cache[k])
        cachekey=str(forge).casefold()+'|'+selector.casefold()
        st=forge.stat()
        cached=disk_cache['entries'].get(cachekey)
        if cached and cached.get('size')==st.st_size and cached.get('mtimeNs')==st.st_mtime_ns:
            if cached.get('verified'):
                result=cached['inventory']
                cache[k]=result
                if census_row and selector=='AUTO':census_row.update(indexStatus=('CACHED_METADATA_ONLY' if st.st_size>limit else 'CACHED_OK'),error='')
                return result
        if forge.stat().st_size>64*1024**3:
            msg='Read-only metadata index exceeds 64 GiB limit';failed_cache[k]=msg
            if census_row:census_row.update(indexStatus='SKIPPED_OVER_64_GIB',error=msg)
            raise RuntimeError(msg)
        try:
            result=inspect(kit,forge,selector,run_dir,log)
            cache[k]=result
            if census_row and selector=='AUTO':census_row.update(indexStatus=('INDEXED_METADATA_ONLY' if st.st_size>limit else 'INSPECTED_OK'),error='')
            disk_cache['entries'][cachekey]={'size':st.st_size,'mtimeNs':st.st_mtime_ns,
                'verified':True,'inventory':{'verified':True,
                    'nestedDataEntries':result.get('nestedDataEntries',[]),
                    'eligibleForDeployment':False,
                    'writerCapability':result.get('writerCapability',''),
                    'nameEvidence':result.get('nameEvidence','FULL_EXTRACTED_READBACK'),
                    'nestedObjects':result.get('nestedObjects',[])}}
            be.record_json(cachefile,disk_cache)
            return result
        except Exception as e:
            failed_cache[k]=str(e)
            if census_row and selector=='AUTO':census_row.update(indexStatus='INSPECTION_FAILED',error=str(e)[:600])
            raise
    docs={'state':'RUNNING','schema':'DEEF.auto_characterization.2','game':str(game),
          'toolkit':str(kit),'fullGameScan':bool(full_game_scan),
          'dateUTC':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
          'entries':[], 'statistics':{}, 'routes':[], 'issues':[]}
    out=run_dir/'AUTO_CHARACTERIZATION.json'
    def persist():
        docs['statistics']=dict(collections.Counter(row['status'] for row in docs['entries']))
        be.record_json(out,docs)
    # Optional extended index: locate payloads whose mod archive does not say which
    # Forge they belong to. Query only immutable local game archives, never guess.
    global_index=None
    def discover_data(name):
        nonlocal global_index
        if global_index is None:
            global_index=collections.defaultdict(list)
            all_forges=[p for matches in forges.values() for p in matches]
            log('Extended local Forge index: {} game archives. This can take substantial time.\n'.format(len(all_forges)))
            for n,forge in enumerate(all_forges,1):
                try:
                    inventory=do_inspect(forge,'AUTO')
                    for label in inventory.get('nestedDataEntries',[]):
                        global_index[deordinal(label)].append((forge,label))
                except Exception as e:
                    failed_cache[(str(forge).casefold(),'INDEX')] = str(e)
                if n%10==0:
                    log('Indexed {} of {} game Forges.\n'.format(n,len(all_forges)))
        return global_index.get(deordinal(name),[])
    # Exclude mutually exclusive Weapon Emporium variant folders before route
    # inspection. Only explicit, persisted selections may choose a variant;
    # defaults favour Standalone when available. This is *not* new-entry approval.
    import additional_options as _opts
    _profile=_opts.load()
    _moddirs={str(Path(m['archive']).parent) for m in mods.values() if m.get('archive')}
    _variant_groups={}
    for _moddir in _moddirs:
        found,_=_opts.variant_groups(_moddir)
        _variant_groups.update(found)
    _selected={}
    for _gun,_variants in _variant_groups.items():
        choice=_profile['weapon_variants'].get(_gun)
        if choice not in _variants:
            choice=next((v for v in _variants if 'standalone' in v.lower()),_variants[0])
        _selected[_gun]=choice
    docs['selectedWeaponVariants']=_selected
    # Read all intended members, not only entries that happen to appear in the old characterization.
    for key,mod in sorted(mods.items()):
        if not mod.get('enabled',True):continue
        for member in mod.get('members',[]):
            row={'archive':key,'member':member,'status':'UNCLASSIFIED', 'historicalState':'',
                 'explanation':'','proposed':[]}
            docs['entries'].append(row)
            import deployment_policy
            compatibility_hold=deployment_policy.held_route(mod['archive'],member)
            if compatibility_hold:
                row.update(status='COMPATIBILITY_HELD',explanation=compatibility_hold,
                           blockReasonCode='GAME_STARTUP_COMPATIBILITY_HOLD')
                continue
            import manual_install_rules
            guide_exclusion=manual_install_rules.excluded_variant(mod['archive'],member)
            if guide_exclusion:
                row.update(status='USER_VARIANT_EXCLUDED',explanation=guide_exclusion)
                continue
            _weapon=rc.weapon_variant(member)
            _target=_opts.target_key(member) if _weapon else None
            if _target in _selected and _weapon['variant'] != _selected[_target]:
                row['status']='USER_VARIANT_EXCLUDED'
                row['explanation']='Main target selector chose '+_selected[_target]
                continue
            if mod.get('status')!='INDEXED':
                row['status']='UNREADABLE_ARCHIVE';continue
            variants,old_state,excluded=candidates_for_member(mod['archive'],member,seed)
            if not variants and not excluded:
                import manual_install_rules
                try:variants=manual_install_rules.candidates(mod['archive'],member,forges,do_inspect)
                except Exception as guide_error:
                    row['guideHintError']=str(guide_error)[:350]
            row['historicalState']=old_state
            if excluded:
                row['status']=excluded;row['explanation']='Classified by bundled path characterization';continue
            if not variants and member.rsplit('/',1)[-1].lower().endswith(
                ('.txt','.md','.pdf','.png','.jpg','.jpeg','.bak','.url','.lnk')):
                row['status']='IGNORED_SUPPORT_FILE';continue
            if not variants and full_game_scan and member.lower().endswith('.data'):
                matches=discover_data(member.rsplit('/',1)[-1].rsplit('\\',1)[-1])
                if len(matches)==1:
                    forge,label=matches[0]
                    variants=[{'kind':'data','forge':forge.name,'data':label,'object':'',
                        'evidence':'unique read-only full-game Forge index match'}]
                elif len(matches)>1:
                    row['status']='AMBIGUOUS_TARGET';row['explanation']='Data name appears in {} installed Forges'.format(len(matches))
                    continue
            if not variants:
                row['status']='NO_TRUSTWORTHY_ROUTE';continue
            row['proposed']=variants
            resolved=[];errors=[]
            for variant in variants:
                possibilities=forges.get(variant['forge'].casefold(),[])
                if len(possibilities)!=1:
                    if not possibilities:
                        if excluded_by_name.get(variant['forge'].casefold()):
                            errors.append('ACTIVE_FORGE_MISSING_BACKUP_ONLY: '+variant['forge']+
                                          ' (backup copies are NOT eligible destinations)')
                        else:
                            errors.append('ACTIVE_FORGE_NOT_FOUND: '+variant['forge'])
                    else:
                        errors.append('ACTIVE_FORGE_DUPLICATE: '+variant['forge'])
                    continue
                forge=possibilities[0]
                try:
                    inventory=do_inspect(forge,'AUTO')
                    selected_data,why=unique(inventory.get('nestedDataEntries',[]),variant['data'])
                    if not selected_data and why=='NOT_IN_INSTALLED_GAME' and full_game_scan:
                        # A patch-folder hint can refer to a parent in the base
                        # Forge. Modify the full existing parent, never synthesize
                        # a partial override from the author's changed files.
                        matches=discover_data(variant['data'])
                        if len(matches)==1:
                            forge,selected_data=matches[0]
                            inventory=do_inspect(forge,'AUTO')
                            row['redirectedParent']={'hintForge':variant['forge'],
                                'actualForge':str(forge),'data':selected_data,
                                'reason':'EXISTING_COMPLETE_PARENT_IN_OTHER_FORGE'}
                        elif len(matches)>1:
                            errors.append('EXISTING_BASE_PARENT_AMBIGUOUS: '+variant['data']);continue
                    if not selected_data:
                        errors.append(forge.name+': '+why+' '+variant['data']);continue
                    selected_inner=''
                    if forge.stat().st_size>limit and inventory.get('writerCapability')!='VERIFIED_STREAM_REPLACEMENT_V27':
                        errors.append('LARGE_FORGE_METADATA_MATCH_NEEDS_PRIVATE_ROUND_TRIP: '+forge.name+'/'+selected_data)
                        row.setdefault('readOnlyCandidateMatches',[]).append({'forge':str(forge),'data':selected_data,'kind':variant['kind']})
                        continue
                    # A metadata-only index is useful for discovery but cannot
                    # independently validate repacking, compression, or game load.
                    # Never turn this into a route eligible for LIVE installation.
                    if variant['kind']=='inner':
                        nested=do_inspect(forge,selected_data)
                        selected_inner,why=unique(nested.get('nestedObjects',[]),variant['object'],obj=True)
                        if not selected_inner:
                            errors.append(forge.name+'/'+selected_data+': '+why+' '+variant['object']);continue
                    if variant['kind']=='data' and not member.lower().endswith('.data'):
                        errors.append('Whole-data mod payload is not a .data member');continue
                    resolved.append({'kind':variant['kind'],'forge':str(forge),'data':selected_data,
                                     'object':selected_inner,'member':member,'sourceArchive':mod['archive'],
                                     'evidence':variant['evidence']})
                except Exception as e:errors.append(variant['forge']+': '+str(e)[:350])
            routekeys={(v['kind'],normal(v['forge']),normal(v['data']),normal(v['object'])) for v in resolved}
            if len(routekeys)==1:
                row['status']='VERIFIED_LOCAL_TARGET';route=resolved[0];row['resolved']=route
            else:
                row['status']='AMBIGUOUS_TARGET' if len(routekeys)>1 else 'UNRESOLVED_TARGET'
                row['explanation']=' | '.join(errors[:5]) or 'No eligible route matched'
                if resolved:row['resolvedAlternatives']=resolved
            if len(docs['entries'])%25==0:
                log('Characterized {} resources; {} verified targets.\n'.format(
                    len(docs['entries']),sum(x['status']=='VERIFIED_LOCAL_TARGET' for x in docs['entries'])))
                persist()
    # Optional explicit user selections between ALREADY verified resources with
    # exactly the same destination. Never approves guesses, new resource names,
    # new entries, or stale decisions. Binary checks remain enforced in C#.
    from resolver import decisions_load, apply_conflict_decisions
    decision_document = decisions_load()
    chosen_count, invalid_choices = apply_conflict_decisions(docs['entries'], decision_document)
    docs['userConflictSelectionsExcluded']=chosen_count
    docs['staleConflictSelections']=invalid_choices
    if chosen_count:
        log('Conflict resolver: excluded {} competing verified routes by explicit user choice.\n'.format(chosen_count))
    for issue in invalid_choices:
        log('Conflict resolver: stale/invalid selection ignored: '+issue+'\n')
    # Known conflicts: only one mod may replace each target. No implicit last-wins order.
    destinations=collections.defaultdict(list)
    for row in docs['entries']:
        if row['status']!='VERIFIED_LOCAL_TARGET':continue
        r=row['resolved'];k=(normal(r['forge']),deordinal(r['data']),
                be.object_stem(r['object']) if r['kind']=='inner' else 'WHOLE_DATA')
        destinations[k].append(row)
    for key,group in destinations.items():
        if len(group)>1:
            for row in group:
                row['status']='CONFLICT';row['explanation']='Multiple enabled mod entries target '+str(key)
    # A whole-data replacement always conflicts with every nested-object replacement within that container.
    containers=collections.defaultdict(list)
    for row in docs['entries']:
        if row['status']=='VERIFIED_LOCAL_TARGET':
            r=row['resolved'];containers[(normal(r['forge']),deordinal(r['data']))].append(row)
    for group in containers.values():
        if any(x['resolved']['kind']=='data' for x in group) and len(group)>1:
            for row in group:
                row['status']='CONFLICT';row['explanation']='Whole .data and/or multiple edits in the same container'
    import mod_coverage
    docs['incompleteWeaponPacks']=mod_coverage.hold_incomplete_weapon_packs(docs['entries'])
    updated={k:dict(v, routes=[]) for k,v in mods.items()}
    for row in docs['entries']:
        if row['status']!='VERIFIED_LOCAL_TARGET':continue
        updated[row['archive']]['routes'].append(row['resolved'])
        docs['routes'].append(row['resolved'])
    # Enrich the report with actionable blocking reasons rather than only counts.
    reason_counts=collections.Counter()
    for row in docs['entries']:
        if row['status'] in ('VERIFIED_LOCAL_TARGET','IGNORED_SUPPORT_FILE','USER_VARIANT_EXCLUDED'):continue
        why=row.get('explanation','')
        if row['status']=='COMPATIBILITY_HELD':code='GAME_STARTUP_COMPATIBILITY_HOLD'
        elif row['status']=='INCOMPLETE_WEAPON_PACK_HELD':code='INCOMPLETE_WEAPON_ASSET_PACK'
        elif 'DUPLICATE_LOGICAL_' in why or 'EXISTING_BASE_PARENT_AMBIGUOUS' in why:code='DUPLICATE_LOGICAL_RESOURCE_REQUIRES_STABLE_ID'
        elif 'LARGE_FORGE_METADATA_MATCH_NEEDS_PRIVATE_ROUND_TRIP' in why:code='LARGE_FORGE_INDEXED_WRITE_NOT_VALIDATED'
        elif 'Read-only metadata index exceeds' in why:code='LARGE_FORGE_INDEXING_SIZE_LIMIT'
        elif 'ACTIVE_FORGE_MISSING_BACKUP_ONLY' in why:code='ACTIVE_FORGE_MISSING_BACKUP_ONLY'
        elif 'ACTIVE_FORGE_NOT_FOUND' in why:code='ACTIVE_FORGE_NOT_FOUND'
        elif 'ACTIVE_FORGE_DUPLICATE' in why:code='ACTIVE_FORGE_DUPLICATE'
        elif 'NOT_IN_INSTALLED_GAME' in why:code='NEW_RESOURCE_INSERTION_CANDIDATE'
        elif 'Inspection failed for' in why:code='ANVIL_TOOLKIT_INSPECTION_FAILED'
        elif row['status']=='NO_TRUSTWORTHY_ROUTE':code='NO_ROUTE_EVIDENCE'
        elif row['status']=='REQUIRES_BASEPACK_SETUP':code='FOUNDATION_PACK_REQUIRES_SEPARATE_HANDLING'
        elif row['status']=='UNSUPPORTED_LOOSE_INSTALL':code='LOOSE_FILE_INSTALL_UNSUPPORTED'
        elif row['status']=='USER_CONFLICT_EXCLUDED':code='USER_EXCLUDED_COMPETING_MOD'
        elif row['status']=='USER_VARIANT_EXCLUDED':code='USER_EXCLUDED_WEAPON_VARIANT'
        elif row['status']=='CONFLICT':code='CONFLICT'
        elif row['status']=='AMBIGUOUS_TARGET':code='AMBIGUOUS_TARGET'
        else:code='OTHER_UNRESOLVED'
        row['blockReasonCode']=code
        reason_counts[code]+=1
    stats=collections.Counter(x['indexStatus'] for x in census)
    docs['gameForgeDiagnostics']={'total':len(census),'validatedWriteLimitMiB':128,'readOnlyIndexLimitGiB':64,
        'overSizeLimit':len(oversized),'byInspectionStatus':dict(stats),
        'censusFile':str(run_dir/'GAME_FORGE_CENSUS.json'),
        'forges':[dict(item) for item in census],
        'excludedNonRootForgeCopies':excluded_copies,
        'excludedNonRootForgeCount':len(excluded_copies)}
    docs['blockReasonStatistics']=dict(reason_counts)
    docs['state']='COMPLETE';docs['unmappedCount']=sum(x['status']!='VERIFIED_LOCAL_TARGET' for x in docs['entries'])
    docs['blockingUnmappedCount']=sum(x['status'] not in ('VERIFIED_LOCAL_TARGET','IGNORED_SUPPORT_FILE','USER_VARIANT_EXCLUDED')
        for x in docs['entries'])
    docs['ignoredSupportCount']=sum(x['status']=='IGNORED_SUPPORT_FILE' for x in docs['entries'])
    docs['verifiedCount']=len(docs['routes'])
    docs['issueCount']=sum(x['status'] in ('CONFLICT','AMBIGUOUS_TARGET','UNRESOLVED_TARGET') for x in docs['entries'])
    persist()
    be.record_json(run_dir/'GAME_FORGE_CENSUS.json',
        {'schema':'DEEF.game_forge_census.1','validatedWriteLimitMiB':128,'readOnlyIndexLimitGiB':64,'archives':census})
    lines=['DEEF v0.17.3 Large-Forge Mapping Diagnostics',
           '=========================================',
           'Active game-root Forges: {}'.format(len(census)),
           'Excluded nested/backup Forge copies: {}'.format(len(excluded_copies)),
           'Above 128 MiB tested WRITE limit (metadata indexing attempted): {}'.format(len(oversized)),
           'Successfully inspected or cached (including metadata-only): {}'.format(sum(x['indexStatus'] in ('INSPECTED_OK','CACHED_OK','INDEXED_METADATA_ONLY','CACHED_METADATA_ONLY') for x in census)),
           'Verified STAGEABLE resource routes: {}'.format(docs['verifiedCount']),
           'Read-only large-Forge candidate matches (not stageable): {}'.format(sum(len(e.get('readOnlyCandidateMatches',[])) for e in docs['entries'])),
           'Blocked mod members: {}'.format(docs['blockingUnmappedCount']),
           '', 'FORGE INSPECTION STATUS:']
    lines.extend('  {}: {}'.format(k,v) for k,v in sorted(stats.items()))
    lines.extend(['', 'BLOCKING REASONS:'])
    lines.extend('  {}: {}'.format(k,v) for k,v in reason_counts.most_common())
    lines.extend(['','LARGEST GAME FORGES (names, sizes, inspection status):'])
    lines.extend('  {} | {} MiB | {}'.format(x['name'],x['mib'],x['indexStatus'])
                 for x in sorted(census,key=lambda x:-x['bytes'])[:20])
    target_refs=collections.Counter(v['forge'].casefold() for r in docs['entries']
        for v in r.get('proposed',[]) if isinstance(v.get('forge'),str))
    lines.extend(['','DESTINATION FORGE REFERENCES (top 20):'])
    for name,n in target_refs.most_common(20):
        sources=forges.get(name,[])
        if not sources:
            state='MISSING ACTIVE (backup exists)' if excluded_by_name.get(name) else 'MISSING ACTIVE'
        elif len(sources)>1:
            state='AMBIGUOUS ACTIVE'
        elif sources[0].stat().st_size>limit:
            state='ACTIVE / METADATA-ONLY INDEX / NOT VALIDATED FOR WRITE'
        else:
            state='ACTIVE / WITHIN INPUT LIMIT'
        lines.append('  {} : {} proposed members | {}'.format(name,n,state))
    lines.extend(['','SAMPLE UNRESOLVED MOD MEMBERS:'])
    for row in [x for x in docs['entries'] if x.get('blockReasonCode')][:12]:
        lines.append('  {} / {} | {} | {}'.format(row['archive'],row['member'],
            row['blockReasonCode'],row.get('explanation','')[:160]))
    lines.extend(['', 'Game files were not modified by characterization.',
        'Only top-level game Forges are eligible for matching and deployment.',
        'Nested/Backups folders are diagnostic-only, NEVER eligible install targets.',
        'A completed index loop is NOT proof that the toolkit successfully inspected every Forge.',
        'For Forges over 128 MiB, a read-only name match is NOT installation validation. Run the separate private round-trip first.',
        'For debugging, upload AUTO_CHARACTERIZATION.json, GAME_FORGE_CENSUS.json and this summary.', ''])
    (run_dir/'AUTO_CHARACTERIZATION_SUMMARY.txt').write_text('\n'.join(lines),encoding='utf-8')
    log('Detailed mapping diagnostic summary: {}\n'.format(run_dir/'AUTO_CHARACTERIZATION_SUMMARY.txt'))
    log('Game Forge results: {}. Above 128-MiB validated-write threshold: {}. Verified write routes: {}.\n'.format(
        dict(stats),len(oversized),docs['verifiedCount']))
    return updated,docs,out

