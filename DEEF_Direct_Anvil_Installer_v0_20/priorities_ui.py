"""Priorities editor: mod order, exact variants and validated weapon fields."""
import copy,json
from pathlib import Path
import tkinter as tk
from tkinter import ttk,messagebox
import mod_manager as mm
import conflict_policy as cp


def show(parent,owner):
 cfg=mm.load(mm.CONFIG,{});policy=copy.deepcopy(cp.settings(cfg));mods=mm.load(mm.STATE,{})
 active=sorted(k for k,v in mods.items() if v.get('enabled',True))
 order=[k for k in policy.get('mod_order',[]) if k in active]+[k for k in active if k not in policy.get('mod_order',[])]
 win=tk.Toplevel(parent);win.title('Conflict priorities and table merges');win.geometry('1060x760');win.minsize(820,600)
 top=ttk.Frame(win,padding=12);top.pack(fill='x')
 ttk.Label(top,text='Choices apply to the next Scan & stage. Higher entries win unresolved object conflicts; compatible objects and validated fields are merged first.',wraplength=970).pack(anchor='w')
 book=ttk.Notebook(win);book.pack(fill='both',expand=True,padx=12,pady=8)
 general=ttk.Frame(book,padding=12);fields=ttk.Frame(book,padding=12);resources=ttk.Frame(book,padding=12)
 book.add(general,text='Mod order');book.add(fields,text='Weapon fields');book.add(resources,text='Resource / variant choices')
 enabled=tk.BooleanVar(value=bool(policy.get('mod_order')))
 ttk.Checkbutton(general,text='Use the ordered mod priorities below for unresolved conflicts',variable=enabled).pack(anchor='w')
 ttk.Label(general,text='Unlisted or tied candidates stay blocked. Known safety holds, prepatch order and selected variants remain in force.',wraplength=960).pack(anchor='w',pady=8)
 listing=tk.Listbox(general,exportselection=False);listing.pack(fill='both',expand=True)
 for key in order:listing.insert('end',key)
 def move(delta):
  selected=listing.curselection()
  if not selected:return
  old=selected[0];new=old+delta
  if 0<=new<listing.size():
   value=listing.get(old);listing.delete(old);listing.insert(new,value);listing.selection_set(new);listing.see(new)
 buttons=ttk.Frame(general);buttons.pack(fill='x',pady=8)
 ttk.Button(buttons,text='Move up',command=lambda:move(-1)).pack(side='left')
 ttk.Button(buttons,text='Move down',command=lambda:move(1)).pack(side='left',padx=8)
 ttk.Label(fields,text='Choose a donor for each supported field group. Existing rules preserve the tested WABO / Damage Balance merge. Missing donors are skipped for weapons they do not supply.',wraplength=960).grid(row=0,column=0,columnspan=2,sticky='w',pady=8)
 values=['Existing rules']+active;field_controls={}
 for row,category in enumerate(cp.FIELD_GROUPS,1):
  ttk.Label(fields,text=category.replace('_',' ').title()).grid(row=row,column=0,sticky='w',padx=8,pady=9)
  var=tk.StringVar(value=policy.get('weapon_fields',{}).get(category) or 'Existing rules');field_controls[category]=var
  ttk.Combobox(fields,textvariable=var,values=values,state='readonly',width=76).grid(row=row,column=1,sticky='ew',pady=9)
 fields.columnconfigure(1,weight=1)
 ttk.Label(fields,text='Vanilla sensitivity, shotgun damage and LMG precision controls are applied after these field merges. Unknown binary fields are never guessed.',wraplength=960).grid(row=len(cp.FIELD_GROUPS)+1,column=0,columnspan=2,sticky='w',pady=12)
 reports=[];runs=mm.WORK/'auto_runs'
 if runs.is_dir():reports=[p/'CONFLICT_POLICY_REPORT.json' for p in runs.iterdir() if p.is_dir() and (p/'CONFLICT_POLICY_REPORT.json').is_file()]
 report=max(reports,key=lambda p:p.stat().st_mtime) if reports else None
 groups=json.loads(report.read_text()).get('groups',[]) if report else []
 ttk.Label(resources,text='Choose an exact archive member when a mod contains alternatives. Choices are bound to the archive hash. A changed or removed winner requires review.',wraplength=960).pack(anchor='w')
 tree=ttk.Treeview(resources,columns=('state',),show='tree headings',height=12);tree.heading('#0',text='Resource');tree.heading('state',text='Resolution');tree.column('#0',width=660);tree.pack(fill='both',expand=True,pady=8)
 by_id={}
 for n,group in enumerate(groups):
  ident=str(n);by_id[ident]=group;tree.insert('', 'end',iid=ident,text=group.get('label',group['target']),values=(group['state'],))
 choice=tk.StringVar();combo=ttk.Combobox(resources,textvariable=choice,state='readonly',width=100);combo.pack(fill='x',pady=4)
 winners=policy.setdefault('resource_winners',{});labels={}
 def select(_=None):
  labels.clear();selected=tree.selection()
  if not selected:return
  group=by_id[selected[0]]
  for row in group['candidates']:labels[row['mod']+' | '+row['member']]=row
  combo.configure(values=['Automatic merge / mod order']+list(labels));choice.set('Automatic merge / mod order')
  saved=winners.get(group['target'])
  if saved:
   for label,row in labels.items():
    if saved.get('mod')==row['mod'] and saved.get('member')==row['member']:choice.set(label)
 tree.bind('<<TreeviewSelect>>',select)
 def choose():
  selected=tree.selection()
  if not selected:return
  group=by_id[selected[0]];row=labels.get(choice.get())
  if row:winners[group['target']]={k:row[k] for k in ('mod','member','archiveSha256')}
  else:winners.pop(group['target'],None)
  tree.set(selected[0],'state','Selected for next build' if row else 'Automatic merge / mod order')
 ttk.Button(resources,text='Set selected resource choice',command=choose).pack(anchor='w',pady=4)
 ttk.Label(resources,text=str(report) if report else 'Run Scan & stage to discover resource conflicts.',wraplength=960).pack(anchor='w',pady=8)
 def save():
  choose()
  policy['mod_order']=list(listing.get(0,'end')) if enabled.get() else []
  policy['weapon_fields']={k:v.get() for k,v in field_controls.items() if v.get()!='Existing rules'}
  updated=mm.load(mm.CONFIG,{});updated['conflict_priorities']=policy;cp.settings(updated);mm.save(mm.CONFIG,updated)
  owner.status.set('Priorities saved - run Scan & stage to apply them');win.destroy()
 bottom=ttk.Frame(win,padding=12);bottom.pack(fill='x')
 ttk.Button(bottom,text='Save priorities for next build',command=save).pack(side='left')
 ttk.Button(bottom,text='Cancel',command=win.destroy).pack(side='right')
 return win
