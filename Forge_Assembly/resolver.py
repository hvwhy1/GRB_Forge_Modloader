"""DEEF exclusion review and conflict-choice utility.

Read-only until an explicit user saves route-winning choices. Does not open,
extract, repack or modify game archives. Historical mapping and fuzzy matches
are suggestions only. Decisions affect a FUTURE characterization, never a
previously staged batch.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
from difflib import get_close_matches
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT=Path(__file__).resolve().parent
DECISIONS=ROOT/'workspace'/'resolver_decisions.json'


def deordinal(s):
    name=str(s).replace('\\','/').rsplit('/',1)[-1]
    return re.sub(r'^\d+_-_', '', name).casefold()


def target_key(route):
    if not isinstance(route,dict):return None
    forge=Path(str(route.get('forge',''))).name.casefold()
    if not forge:return None
    data=deordinal(route.get('data',''))
    if not data:return None
    kind=route.get('kind')
    if kind=='data': object_id='WHOLE_DATA'
    elif kind=='inner': object_id=deordinal(route.get('object','')).rsplit('.',1)[0]
    else:return None
    if not object_id:return None
    return '|'.join((forge,data,kind,object_id))


def group_id(key):return hashlib.sha256(key.encode('utf-8')).hexdigest()[:20]


def row_identity(row):
    return {'archive':row.get('archive',''),'member':row.get('member','')}


def same_row(a,b):
    return str(a.get('archive','')).casefold()==str(b.get('archive','')).casefold() and str(a.get('member',''))==str(b.get('member',''))


def conflict_groups(entries):
    d=defaultdict(list)
    for row in entries:
        if row.get('status') not in ('CONFLICT','VERIFIED_LOCAL_TARGET','USER_CONFLICT_EXCLUDED'):continue
        k=target_key(row.get('resolved'))
        if k:d[k].append(row)
    result={}
    for k,rows in d.items():
        # Conflict only for distinct archive-member pairs; exclude structural
        # multi-edit conflicts that require explicit binary compatibility review.
        identities={(r.get('archive'),r.get('member')) for r in rows}
        if len(identities)>1:
            result[group_id(k)]={'targetKey':k,'rows':rows,'key':group_id(k)}
    return result


def decisions_load(path=DECISIONS):
    path=Path(path)
    if not path.exists():return {'schema':'DEEF.resolver_decisions.1','winners':{}}
    d=json.loads(path.read_text(encoding='utf-8'))
    if d.get('schema')!='DEEF.resolver_decisions.1' or not isinstance(d.get('winners'),dict):
        raise ValueError('Invalid resolver decisions schema')
    return d


def decisions_save(choices,path=DECISIONS):
    # Only save chosen winners, never unverified replacement destination guesses.
    from batch_engine import record_json
    clean={}
    for gid,row in choices.items():
        if not re.fullmatch('[0-9a-f]{20}',str(gid)):raise ValueError('Invalid conflict group ID')
        if not isinstance(row,dict) or not all(isinstance(row.get(k),str) and row.get(k) for k in ('archive','member')):
            raise ValueError('Invalid winner selection')
        clean[gid]=row_identity(row)
    document={'schema':'DEEF.resolver_decisions.1','winners':clean,
              'note':'Only chooses between previously verified routes targeting the SAME binary resource. Re-characterization and C# identity validation remain required.'}
    record_json(path,document)
    return document


def apply_conflict_decisions(entries, decisions):
    """Mutate only verified conflict rows; invalid/stale selections fail closed.

    Called after the original route resolution and BEFORE the original conflict
    rejection. Never creates a route, never changes a resource identity.
    """
    groups=conflict_groups(entries)
    count=0; invalid=[]
    for gid,winner in decisions.get('winners',{}).items():
        g=groups.get(gid)
        if not g:
            invalid.append(gid+': target not present in current characterization')
            continue
        candidates=[r for r in g['rows'] if same_row(r,winner)]
        if len(candidates)!=1:
            invalid.append(gid+': chosen archive member not uniquely present')
            continue
        # Guard whole-data-versus-inner overlap: target_key separates kinds,
        # and the pre-existing container-conflict check will still reject it.
        for r in g['rows']:
            if r is candidates[0]:continue
            if r['status']!='VERIFIED_LOCAL_TARGET':continue
            r['status']='USER_CONFLICT_EXCLUDED'
            r['explanation']='Excluded because user explicitly selected another verified replacement for this identical destination'
            count+=1
    return count,invalid


def find_characters():
    # Only examine expected locations; do not recursively inspect game files.
    places=[ROOT, ROOT.parent,Path.home()/'Desktop',Path.home()/'Desktop'/'GRB_MODDING']
    found=[]
    for base in places:
        if not base.exists():continue
        workspaces=[base/'workspace']
        try:
            workspaces.extend(p/'workspace' for p in base.glob('DEEF_Direct_Anvil_Installer_v0_19*') if p.is_dir())
        except OSError:pass
        for workspace in workspaces:
            runroot=workspace/'auto_runs'
            if not runroot.is_dir():continue
            for run in runroot.glob('*/characterization/AUTO_CHARACTERIZATION.json'):
                try:found.append((run.stat().st_mtime,run))
                except OSError:pass
    return [str(p) for _,p in sorted(set(found),reverse=True)]


def read_characterization(path):
    p=Path(path).resolve()
    if p.name!='AUTO_CHARACTERIZATION.json':raise ValueError('Choose AUTO_CHARACTERIZATION.json from a characterization folder')
    if p.stat().st_size>80*1024*1024:raise ValueError('Unexpectedly large characterization report')
    d=json.loads(p.read_text(encoding='utf-8'))
    if not isinstance(d.get('entries'),list) or len(d['entries'])>50000:
        raise ValueError('Not a supported Forge Assembly characterization report')
    return p,d


def find_cache(charpath):
    p=Path(charpath)
    for a in [ROOT]+list(p.parents):
        x=a/'workspace'/'GAME_ASSET_CACHE.json'
        if x.is_file():return x
        x=a/'GAME_ASSET_CACHE.json'
        if x.is_file():return x
    return None


def load_asset_index(cache_path,game_root):
    """Collect only immutable cached .data names. Validate cache's size/mtime
    against active game files where available; never treat suggestions as routes.
    """
    if not cache_path:return {},'No previously indexed Forge cache was found'
    cache_path=Path(cache_path)
    try:d=json.loads(cache_path.read_text(encoding='utf-8'))
    except (OSError,ValueError) as e:return {},'Forge cache could not be read: '+str(e)
    result=defaultdict(list)
    game=Path(game_root) if game_root else None
    checked=0; rejected=0
    for composite, entry in d.get('entries',{}).items():
        parts=composite.rsplit('|',1)
        if len(parts)!=2 or parts[1].casefold()!='auto':continue
        source=Path(parts[0]);is_active=bool(game and source.parent.resolve()==game.resolve()) if game and game.exists() else True
        if not is_active or not source.is_file():rejected+=1;continue
        st=source.stat()
        if st.st_size!=entry.get('size') or st.st_mtime_ns!=entry.get('mtimeNs'):
            rejected+=1;continue
        for name in entry.get('inventory',{}).get('nestedDataEntries',[]):
            if deordinal(name).endswith('.data'):result[deordinal(name)].append({'forge':str(source),'data':name})
        checked+=1
    return dict(result),'Read-only Forge cache: {} active current files checked, {} stale/missing ignored'.format(checked,rejected)


def propose(row, index, keys_by_prefix):
    """Never changes an existing route; provides manual investigation clues."""
    targets=[]
    for route in row.get('proposed',[]):
        if route.get('data'):targets.append(route['data'])
    m=str(row.get('member','')).replace('\\','/').split('/')[-1]
    if m.lower().endswith('.data'):targets.append(m)
    seen=set();proposals=[]
    for target in targets[:6]:
        logical=deordinal(target)
        exact=index.get(logical,[])
        for hit in exact[:6]:
            label=(hit['forge'],hit['data'])
            if label not in seen:
                proposals.append({'type':'EXACT_DATA_NAME_OTHER_FORGE_OR_ROUTE','forge':hit['forge'],
                                  'data':hit['data'],'confidence':'NAME_ONLY_NOT_VERIFIED'})
                seen.add(label)
        if exact:continue
        # Only propose fuzzy entries that are close AND share an initial
        # technical resource-family prefix; otherwise the suggestions are noise.
        prefix=logical[:min(6,len(logical))]
        near=keys_by_prefix.get(prefix,[])
        if 1<=len(near)<=2500:
            names=get_close_matches(logical,near,n=3,cutoff=0.90)
            for name in names:
                for hit in index[name][:2]:
                    label=(hit['forge'],hit['data'])
                    if label not in seen:
                        proposals.append({'type':'SIMILAR_NAME_NEEDS_MANUAL_REVIEW','forge':hit['forge'],
                                          'data':hit['data'],'confidence':'FUZZY_NAME_ONLY'})
                        seen.add(label)
    return proposals[:8]


def action_for_reason(reason):
    actions={
       'CONFLICT':'Choose a winning mod in Conflict winners; rebuild stage after saving',
       'RESOURCE_NOT_PRESENT_IN_GAME_FORGE':'Likely intentional NEW resource: check parent Forge/.data; insert new object or Forge entry after binary-ID and readback validation, never overwrite another name',
       'NEW_RESOURCE_INSERTION_CANDIDATE':'Potential intentional new Forge or nested entry: inspect intended parent, validate ID/type, and private-test addition; not a replacement',
       'NO_ROUTE_EVIDENCE':'Obtain explicit .forge/.data path from mod documentation or verified manifest',
       'ACTIVE_FORGE_NOT_FOUND':'Confirm game archive name/version; do not substitute backup Forge',
       'DUPLICATE_LOGICAL_RESOURCE_REQUIRES_STABLE_ID':'Extract and compare stable internal IDs; never rely on entry ordinal alone',
       'FOUNDATION_PACK_REQUIRES_SEPARATE_HANDLING':'Separate foundation-pack deployment with dedicated manifest, backup, and compatibility checks',
       'LOOSE_FILE_INSTALL_UNSUPPORTED':'Separate loose-file installer; require exact supported destination and backup',
       'USER_EXCLUDED_COMPETING_MOD':'Already excluded by your saved winner choice; will not be staged',
       'GAME_STARTUP_COMPATIBILITY_HOLD':'This exact archived payload is held after a failed startup comparison; retain the original object',
    }
    return actions.get(reason,'Inspect target data and embedded identity before proposing a new route')


def build_report(charpath, outfile=None, cache_path=None):
    path,d=read_characterization(charpath)
    entries=d['entries'];counts=Counter(r.get('status','UNKNOWN') for r in entries)
    groups=conflict_groups(entries)
    cache_path=Path(cache_path) if cache_path else find_cache(path)
    index,cache_note=load_asset_index(cache_path,d.get('game',''))
    byprefix=defaultdict(list)
    for name in index:byprefix[name[:min(6,len(name))]].append(name)
    rows=[];byarchive=defaultdict(Counter)
    for row in entries:
        status=row.get('status','UNKNOWN');byarchive[row.get('archive','UNKNOWN')][status]+=1
        if status in ('VERIFIED_LOCAL_TARGET','IGNORED_SUPPORT_FILE'):continue
        suggestions=propose(row,index,byprefix) if status in ('UNRESOLVED_TARGET','NO_TRUSTWORTHY_ROUTE','AMBIGUOUS_TARGET') else []
        rows.append({'archive':row.get('archive',''), 'member':row.get('member',''),
                     'status':status,'reason':row.get('blockReasonCode',status),
                     'recommendedAction':action_for_reason(row.get('blockReasonCode',status)),
                     'explanation':row.get('explanation',''), 'proposed':row.get('proposed',[]),
                     'possibleDataNames':suggestions})
    wins=decisions_load()['winners']
    conflicts=[]
    for gid,g in sorted(groups.items()):
        candidates=[{'archive':r.get('archive'),'member':r.get('member'),'status':r.get('status')} for r in g['rows']]
        selected=next((c for c in candidates if same_row(c,wins.get(gid,{}))),None)
        conflicts.append({'id':gid,'target':g['targetKey'],'candidates':candidates,
                          'selectedWinner':selected})
    coverage=[]
    for archive,cc in sorted(byarchive.items()):
        relevant=sum(cc.values())-cc.get('IGNORED_SUPPORT_FILE',0)
        good=cc.get('VERIFIED_LOCAL_TARGET',0)
        coverage.append({'archive':archive,'totalMembers':sum(cc.values()), 'relevantMembers':relevant,
            'verifiedRoutes':good,'blockedMembers':relevant-good,
            'statuses':dict(cc),'fullyCovered':relevant>0 and good==relevant})
    result={'schema':'DEEF.exclusion_review.1','characterization':str(path),
            'sourceState':d.get('state'),'timestampUTC':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
            'statusCounts':dict(counts),'blockedResources':len(rows),
            'conflictGroups':conflicts,'reviewEntries':rows,'archiveCoverage':coverage,
            'indexNote':cache_note,'suggestionsAreApprovedRoutes':False,
            'existingStagesChanged':False,'gameModified':False,
            'note':'A suggested Forge/data name is NOT a verified route. Only explicit winners among previously verified identical destinations can be replayed by the mapper.'}
    out=Path(outfile) if outfile else ROOT/'workspace'/'resolver_reports'/time.strftime('%Y%m%d_%H%M%S')
    out.mkdir(parents=True,exist_ok=True)
    from batch_engine import record_json
    record_json(out/'EXCLUSION_REVIEW.json',result)
    with (out/'EXCLUDED_RESOURCES.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.writer(f);writer.writerow(['Archive','Member','Status','Reason','Recommended action','Explanation','Candidate destinations (UNVERIFIED)'])
        for r in rows:writer.writerow([r['archive'],r['member'],r['status'],r['reason'],r['recommendedAction'],r['explanation'],
            ' ; '.join(Path(p['forge']).name+'/'+p['data'] for p in r['possibleDataNames'])])
    with (out/'MOD_COMPLETENESS.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.writer(f);writer.writerow(['Archive','All members','Relevant members','Verified resource routes','Blocked','All relevant verified?'])
        for r in coverage:writer.writerow([r['archive'],r['totalMembers'],r['relevantMembers'],r['verifiedRoutes'],r['blockedMembers'],r['fullyCovered']])
    reasoncounts=Counter(r['reason'] for r in rows)
    lines=['Forge Assembly - Exclusion Resolution Priorities',
           '==========================================',
           'Characterization: '+str(path),
           'Source status: '+str(d.get('state')),
           'Unresolved records: '+str(len(rows)),
           'Conflict groups requiring a single explicit winner: '+str(len(conflicts)),
           'Game index: '+cache_note,
           '', 'BLOCKERS / ACTIONS:']
    lines.extend('  {}: {} - {}'.format(reason,qty,action_for_reason(reason)) for reason,qty in reasoncounts.most_common())
    lines.extend(['','ARCHIVE COVERAGE (not game-play compatibility):'])
    lines.extend('  {}: {}/{} verified, {} blocked'.format(
        x['archive'],x['verifiedRoutes'],x['relevantMembers'],x['blockedMembers']) for x in sorted(coverage,key=lambda a:(-a['blockedMembers'],a['archive'])) )
    lines.extend(['','The pre-existing staged batch is unchanged. Review EXCLUDED_RESOURCES.csv and select conflict winners using START_EXCLUSION_RESOLVER.cmd.',
                  'Candidate names are UNVERIFIED suggestions and never create installation routes.'])
    (out/'RESOLUTION_ACTION_PLAN.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return out,result


def show_gui(parent=None):
    import tkinter as tk
    from tkinter import ttk,filedialog,messagebox
    embedded=parent is not None
    win=parent if embedded else tk.Tk()
    if not embedded:
        win.title('Forge Assembly - route discovery')
        win.geometry('1220x770');win.minsize(940,600)
    var=tk.StringVar(value=find_characters()[0] if find_characters() else '')
    status=tk.StringVar(value='Select the latest AUTO_CHARACTERIZATION.json; this tool does not modify game files.')
    choices={};current={};groups={};rows=[]
    outer=ttk.Frame(win,padding=10);outer.pack(fill='both',expand=True)
    top=ttk.Frame(outer);top.pack(fill='x')
    ttk.Entry(top,textvariable=var).pack(side='left',fill='x',expand=True,padx=(0,6))
    def browse():
        p=filedialog.askopenfilename(title='Choose AUTO_CHARACTERIZATION.json',filetypes=[('JSON','*.json')])
        if p:var.set(p)
    ttk.Button(top,text='Browse',command=browse).pack(side='left')
    tabs=ttk.Notebook(outer);tabs.pack(fill='both',expand=True,pady=8)
    tab1=ttk.Frame(tabs);tabs.add(tab1,text='Conflict winners')
    tab2=ttk.Frame(tabs);tabs.add(tab2,text='Unresolved resources')
    tab3=ttk.Frame(tabs);tabs.add(tab3,text='Mod coverage')
    tab4=ttk.Frame(tabs);tabs.add(tab4,text='New mod assets')
    cols=('Target','Archive','Member','Decision')
    table1=ttk.Frame(tab1);table1.pack(fill='both',expand=True)
    tr=ttk.Treeview(table1,columns=cols,show='headings',selectmode='browse')
    sx=ttk.Scrollbar(table1,orient='horizontal',command=tr.xview);sx.pack(side='bottom',fill='x')
    tr.configure(xscrollcommand=sx.set)
    for x,w in [('Target',430),('Archive',270),('Member',390),('Decision',120)]:tr.heading(x,text=x);tr.column(x,width=w)
    y=ttk.Scrollbar(table1,orient='vertical',command=tr.yview);tr.configure(yscrollcommand=y.set)
    tr.pack(side='left',fill='both',expand=True);y.pack(side='right',fill='y')
    frame2=ttk.Frame(tab2);frame2.pack(fill='both',expand=True)
    cols2=('Archive','File','Reason','Potential game destination - NOT APPROVED')
    t2=ttk.Treeview(frame2,columns=cols2,show='headings')
    sx2=ttk.Scrollbar(frame2,orient='horizontal',command=t2.xview);sx2.pack(side='bottom',fill='x');t2.configure(xscrollcommand=sx2.set)
    for x,w in [('Archive',250),('File',300),('Reason',240),('Potential game destination - NOT APPROVED',360)]:
        t2.heading(x,text=x);t2.column(x,width=w)
    ys=ttk.Scrollbar(frame2,orient='vertical',command=t2.yview);t2.configure(yscrollcommand=ys.set)
    t2.pack(side='left',fill='both',expand=True);ys.pack(side='right',fill='y')
    frame3=ttk.Frame(tab3);frame3.pack(fill='both',expand=True)
    cols3=('Archive','Relevant members','Verified','Blocked','Fully covered')
    t3=ttk.Treeview(frame3,columns=cols3,show='headings')
    sx3=ttk.Scrollbar(frame3,orient='horizontal',command=t3.xview);sx3.pack(side='bottom',fill='x');t3.configure(xscrollcommand=sx3.set)
    for x,w in [('Archive',550),('Relevant members',120),('Verified',90),('Blocked',90),('Fully covered',110)]:
        t3.heading(x,text=x);t3.column(x,width=w)
    sy=ttk.Scrollbar(frame3,orient='vertical',command=t3.yview);t3.configure(yscrollcommand=sy.set)
    t3.pack(side='left',fill='both',expand=True);sy.pack(side='right',fill='y')
    frame4=ttk.Frame(tab4);frame4.pack(fill='both',expand=True)
    cols4=('Finding','Source mod','New mod resource','Target Forge','Target .data','Evidence / last check')
    t4=ttk.Treeview(frame4,columns=cols4,show='headings')
    sx4=ttk.Scrollbar(frame4,orient='horizontal',command=t4.xview);sx4.pack(side='bottom',fill='x');t4.configure(xscrollcommand=sx4.set)
    for x,w in [('Finding',230),('Source mod',235),('New mod resource',315),('Target Forge',205),('Target .data',200),('Evidence / last check',280)]:
        t4.heading(x,text=x);t4.column(x,width=w)
    sb4=ttk.Scrollbar(frame4,orient='vertical',command=t4.yview);t4.configure(yscrollcommand=sb4.set)
    t4.pack(side='left',fill='both',expand=True);sb4.pack(side='right',fill='y')
    bottom=ttk.Frame(outer);bottom.pack(fill='x',pady=5)
    def load_it():
        nonlocal choices,current,groups,rows
        try:
            out,report=build_report(var.get())
            groups={g['id']:g for g in report['conflictGroups']}
            choices={gid:g['selectedWinner'] for gid,g in groups.items() if g['selectedWinner']}
            current={};rows=[]
            for t in (tr,t2,t3,t4):
                for item in t.get_children():t.delete(item)
            for g in report['conflictGroups']:
                for candidate in g['candidates']:
                    iid=tr.insert('','end',values=(g['target'],candidate['archive'],candidate['member'],
                        'KEEP' if same_row(candidate,choices.get(g['id'],{})) else ''))
                    current[iid]=(g['id'],candidate)
            for r in report['reviewEntries']:
                cand=r['possibleDataNames']
                desc=', '.join(Path(x['forge']).name+'/'+x['data'] for x in cand[:2])
                t2.insert('','end',values=(r['archive'],r['member'],r['reason'],desc))
            for r in report['archiveCoverage']:
                t3.insert('','end',values=(r['archive'],r['relevantMembers'],r['verifiedRoutes'],
                                            r['blockedMembers'],'YES' if r['fullyCovered'] else 'NO'))
            from new_resource_audit import build as classify_new
            _,char_doc=read_characterization(var.get())
            new=classify_new(char_doc,var.get())
            for row in new['candidates']:
                finding=row['classification'].replace('_',' ').title()
                evidence='; '.join(x for x in (str(row.get('evidence','')),str(row.get('priorFailure',''))) if x)
                t4.insert('','end',values=(finding,row['sourceArchive'],row['member'],
                    Path(row['parentForge']).name,row['parentData'],evidence))
            rows=report['reviewEntries']
            status.set('Report: {} | {} unresolved | {} conflict groups | {}'.format(
                out,len(rows),len(groups),report['indexNote']))
        except Exception as e:messagebox.showerror('Resolver',str(e))
    def keep():
        pick=tr.selection()
        if not pick:return
        gid,w=current[pick[0]]
        # Re-selecting a winner for same destination changes only that destination.
        choices[gid]=row_identity(w)
        for iid,(g,c) in current.items():
            if g==gid:
                vals=list(tr.item(iid,'values'));vals[-1]='KEEP' if same_row(c,w) else ''
                tr.item(iid,values=vals)
        status.set('Selected '+w['archive']+' / '+w['member']+' (not saved yet)')
    def save():
        try:
            d=decisions_save(choices)
            status.set('Saved {} conflict choices. They apply to the next scan and stage.'.format(len(d['winners'])))
            messagebox.showinfo('Saved','Saved {} conflict choices. To apply them, run NEW automatic staging. Your existing verified batch is unchanged.'.format(len(d['winners'])))
        except Exception as e:messagebox.showerror('Save failed',str(e))
    def export_new():
        try:
            from new_resource_audit import run as audit
            result=audit(var.get())
            status.set('{} new-resource candidates exported to workspace/new_resource_audit (read-only).'.format(result['candidateCount']))
            tabs.select(tab4)
        except Exception as e:messagebox.showerror('New resource audit',str(e))
    def export_route_conventions():
        try:
            from new_resource_audit import run as audit
            from route_convention_audit import (build as layout_review,
                write_report as save_layout, DEFAULT_MODS, WEAPON_ZIP_NAME)
            audit(var.get())  # reuse the already selected characterization; read-only
            source=ROOT/'workspace'/'new_resource_audit'/'NEW_RESOURCE_AUDIT.json'
            zip_path=DEFAULT_MODS/WEAPON_ZIP_NAME
            result=layout_review(source,zip_path if zip_path.is_file() else None)
            target=ROOT/'workspace'/'route_convention_review'
            save_layout(result,target)
            status.set('Parent/weapon review saved: {} normalized instruction labels, {} weapon route candidates; no game changes'.format(
                result['instructionCanonicalizationCount'],result['weaponMissingCount']))
            tabs.select(tab4)
        except Exception as e:messagebox.showerror('Parent / weapon route review',str(e))
    for column in range(3):bottom.columnconfigure(column,weight=1)
    for index,(label,action) in enumerate([('Load review',load_it),('Export new assets',export_new),
            ('Review parent routes',export_route_conventions),('Keep selected mod',keep),('Save conflict choices',save)]):
        ttk.Button(bottom,text=label,command=action).grid(row=index//3,column=index%3,sticky='ew',padx=4,pady=3)
    ttk.Label(outer,textvariable=status,wraplength=850,justify='left').pack(anchor='w',pady=6)
    ttk.Label(outer,text='New files are listed as mod assets, not errors. Some need an absent Forge directory or parent .data container. Conflict choices affect only verified same-target alternatives; this review does not install files.',
              wraplength=850,foreground='#d4a76a').pack(anchor='w')
    if var.get():win.after(50,load_it)
    if not embedded:win.mainloop()


def cli():
    p=argparse.ArgumentParser(description='Read-only exclusion analysis + deliberate same-target conflict choices')
    p.add_argument('--characterization');p.add_argument('--report-dir');p.add_argument('--gui',action='store_true')
    a=p.parse_args()
    if a.gui or not a.characterization:return show_gui()
    out,report=build_report(a.characterization,a.report_dir)
    print('Report:',out);print('Blocked records:',report['blockedResources']);print('Conflict groups:',len(report['conflictGroups']))

if __name__=='__main__':cli()
