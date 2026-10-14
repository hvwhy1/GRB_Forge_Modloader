"""Persistent, conflict-aware first-person/ReShade/weapon-variant selections.

Selections alone do NOT install third-party DLLs or claim a preset is present.
Unsafe graphics proxy collisions remain manual and explicitly warned about.
"""
from __future__ import annotations
import json
from pathlib import Path
from collections import defaultdict
import route_convention_audit as routes

ROOT=Path(__file__).resolve().parent
PREFS=ROOT/'workspace'/'additional_options.json'
DEFAULTS={
    'first_person_enabled':True,
    'first_person_variant':'Easy First Person v3 / Native First Person 0.2.10',
    'reshade_enabled':True,
    'reshade_variant':"Deef's LoFidelity Reshade",
    'weapon_variants':{},
}
FIRST_PERSON=[
    'Easy First Person v3 / Native First Person 0.2.10',
    'First Person View (legacy)',
    'First Person View (Ubisoft+ alternative)',
]
RESHADE=[
    'Nommad ReShade preset',
    "Deef's LoFidelity Reshade",
    'Tier 1 Imports Wraith Ops ReShade',
    'Other user-provided ReShade preset',
]


def load():
    try:
        data=json.loads(PREFS.read_text(encoding='utf-8'))
        if not isinstance(data,dict):return dict(DEFAULTS)
    except (OSError,ValueError):return dict(DEFAULTS)
    cfg=dict(DEFAULTS,**{k:data[k] for k in DEFAULTS if k in data})
    if cfg['first_person_variant'] not in FIRST_PERSON:cfg['first_person_variant']=FIRST_PERSON[0]
    if cfg['reshade_variant'] not in RESHADE:cfg['reshade_variant']=RESHADE[0]
    if not isinstance(cfg['weapon_variants'],dict):cfg['weapon_variants']={}
    return cfg


def save(cfg):
    copy=dict(DEFAULTS,**cfg)
    if copy['first_person_variant'] not in FIRST_PERSON or copy['reshade_variant'] not in RESHADE:
        raise ValueError('Unknown first person or ReShade selection')
    if not isinstance(copy['weapon_variants'],dict):raise ValueError('Invalid weapon variant choices')
    PREFS.parent.mkdir(parents=True,exist_ok=True)
    temp=PREFS.with_suffix('.tmp')
    temp.write_text(json.dumps(copy,indent=2),encoding='utf-8')
    temp.replace(PREFS)
    return copy


def target_key(member):
    """Exact physical Forge+parent+object identity, ignoring displayed ordinals."""
    import re
    parent=routes.rc.parse_explicit_parent(member)
    if not parent:return None
    logical=lambda n:re.sub(r'^\d+_-_','',n,flags=re.I).casefold()
    if parent['kind']!='inner':return None
    return '|'.join([parent['forge'].casefold(),logical(parent['data']),logical(parent['object'])])


def variant_groups(archive_dir):
    candidates=list(Path(archive_dir).glob('Weapon Emporium Weapon Tweaks v2.2*.zip')) if Path(archive_dir).is_dir() else []
    if not candidates:return {},None
    _,alternatives=routes.inspect_zip(candidates[0])
    # Only the *same physical object* appearing in multiple variants is
    # exclusive. Separate parts/modifications of the same gun may coexist.
    result={item['target']:item['variants'] for item in alternatives}
    return dict(sorted(result.items())),candidates[0]


def export_review(cfg,game_root,mods_dir):
    out=ROOT/'workspace'/'additional_options_review';out.mkdir(parents=True,exist_ok=True)
    game=Path(game_root)
    installed={
        'nativeFirstPersonConfigPresent':(game/'NativeFirstPersonOptions').is_dir(),
        'graphicsProxyPresent':(game/'d3d11.dll').is_file(),
        'reshadeIniPresent':(game/'ReShade.ini').is_file(),
        'reshadeShaderFolderPresent':(game/'reshade-shaders').is_dir(),
    }
    options,archive=variant_groups(mods_dir)
    chosen={w:v for w,v in cfg['weapon_variants'].items() if w in options and v in options[w]}
    result={
        'schema':'DEEF.additional_options.1','selectionOnly':True,
        'firstPerson':{'enabled':bool(cfg['first_person_enabled']),'mainSelector':cfg['first_person_variant'],
                       'installedOrAvailableNotProvenBySelection':True},
        'reshade':{'enabled':bool(cfg['reshade_enabled']),'mainSelector':cfg['reshade_variant'],
                   'installedOrAvailableNotProvenBySelection':True},
        'availableWeaponVariantGroups':options,'selectedWeaponVariants':chosen,
        'weaponPackage':str(archive) if archive else None,
        'detectedGameFiles':installed,
        'warning':('Selection does not copy mods to the game. ReShade and native first person can compete for d3d11.dll; '
                   'do not overwrite graphics proxies. Weapon options are exclusive; unselected options are not approved.'),
        'outerForgeInsertionEnabledForDeployment':False,
    }
    (out/'ADDITIONAL_OPTIONS_REVIEW.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return out/'ADDITIONAL_OPTIONS_REVIEW.json'


def show_gui(parent=None,game_root=None,mods_dir=None,embedded=False,installer_owner=None):
    import tkinter as tk
    from tkinter import ttk,messagebox
    cfg=load()
    own_window=parent is None or not embedded
    win=(tk.Toplevel(parent) if parent is not None else tk.Tk()) if own_window else parent
    if own_window:
        win.title('DEEF Additional Options - First Person / ReShade / Weapon Variants')
        win.geometry('900x675');win.minsize(760,560)
    if game_root is None:game_root=r'C:\Program Files (x86)\Ubisoft\Ubisoft Game Launcher\games\Ghost Recon Breakpoint'
    if mods_dir is None:mods_dir=r'C:\Users\Hurt\Desktop\GRB_MODDING\DEEF_Mods'
    def current_path(value):
        if callable(value):return value()
        if hasattr(value,'get'):return value.get()
        return value
    fp_enabled=tk.BooleanVar(value=cfg['first_person_enabled']);rs_enabled=tk.BooleanVar(value=cfg['reshade_enabled'])
    fp=tk.StringVar(value=cfg['first_person_variant']);rs=tk.StringVar(value=cfg['reshade_variant'])
    book=ttk.Notebook(win);book.pack(fill='both',expand=True,padx=12,pady=10)
    camera=ttk.Frame(book,padding=14);shade=ttk.Frame(book,padding=14);weapons=ttk.Frame(book,padding=14)
    book.add(camera,text='First Person');book.add(shade,text='ReShade');book.add(weapons,text='Weapon variants')
    if installer_owner is not None:
        import config_widgets
        merge_options=ttk.Frame(book);book.add(merge_options,text='Merge priorities')
        installer_owner.merge_controls=config_widgets.merge_panel(merge_options,installer_owner)
    if installer_owner is not None:
        native_book=ttk.Notebook(camera);native_book.pack(fill='both',expand=True)
        profile_camera=ttk.Frame(native_book,padding=8);native=ttk.Frame(native_book,padding=8);ini_finder=ttk.Frame(native_book,padding=8)
        native_book.add(native,text='Native setup');native_book.add(ini_finder,text='Config / INI finder');native_book.add(profile_camera,text='Profile selection')
        import config_widgets
        installer_owner.native_controls=config_widgets.native_panel(native,installer_owner)
        installer_owner.native_ini=config_widgets.ini_panel(ini_finder,installer_owner)
        camera=profile_camera
        shade_book=ttk.Notebook(shade);shade_book.pack(fill='both',expand=True)
        installation=ttk.Frame(shade_book);profile_shade=ttk.Frame(shade_book,padding=8)
        shade_book.add(installation,text='Installation');shade_book.add(profile_shade,text='Profile selection')
        installer_owner.reshade_controls=config_widgets.reshade_panel(installation,installer_owner)
        shade=profile_shade
    ttk.Checkbutton(camera,text='Enable modded first-person profile by default',variable=fp_enabled).pack(anchor='w',pady=10)
    ttk.Label(camera,text='Main selector - exactly one first-person stack:').pack(anchor='w',pady=8)
    for c in FIRST_PERSON:ttk.Radiobutton(camera,text=c,value=c,variable=fp).pack(anchor='w',padx=20,pady=5)
    ttk.Label(camera,text='The modern Native First Person v0.2.10 variant is the default. Legacy variants are alternatives, not additive patches.',wraplength=790).pack(anchor='w',pady=12)
    ttk.Label(camera,text='Use Config / INI finder or Mod library > INI workspace for editing and installation. Profile selections are saved separately.',wraplength=790).pack(anchor='w',pady=8)
    ttk.Checkbutton(shade,text='Enable modded ReShade profile by default',variable=rs_enabled).pack(anchor='w',pady=10)
    ttk.Label(shade,text='Main selector - choose one active preset:').pack(anchor='w',pady=8)
    for c in RESHADE:ttk.Radiobutton(shade,text=c,value=c,variable=rs).pack(anchor='w',padx=20,pady=5)
    ttk.Label(shade,text='LoFidelity requires the author-provided RECORDED or NOT shader/textures. Wraith Ops is an alternative. Presets can coexist on disk, but only one is selected here.',wraplength=780).pack(anchor='w',pady=12)
    ttk.Label(shade,text='ReShade and Native First Person may both use graphics proxy DLLs. Native setup checks conflicts and can preserve an existing DX11 proxy using the chaining option.',wraplength=780).pack(anchor='w',pady=8)
    ttk.Label(weapons,text='Choose one modded variant only where two variants affect the SAME resource. Other weapon upgrades remain included.',wraplength=780).pack(anchor='w')
    groups,archive=variant_groups(current_path(mods_dir))
    variants={}
    area=ttk.Frame(weapons);area.pack(fill='both',expand=True,pady=8)
    canvas=tk.Canvas(area,highlightthickness=0,bg='#111827');scroll=ttk.Scrollbar(area,orient='vertical',command=canvas.yview)
    inside=ttk.Frame(canvas);inside.bind('<Configure>',lambda _:canvas.configure(scrollregion=canvas.bbox('all')))
    canvas.create_window((0,0),window=inside,anchor='nw');canvas.configure(yscrollcommand=scroll.set)
    canvas.pack(side='left',fill='both',expand=True);scroll.pack(side='right',fill='y')
    def draw_variants():
        nonlocal groups,archive
        selected={k:v.get() for k,v in variants.items()}
        groups,archive=variant_groups(current_path(mods_dir))
        variants.clear()
        for child in inside.winfo_children():child.destroy()
        if not groups:
            ttk.Label(inside,text='No overlapping Weapon Emporium alternatives found in the selected mods folder.').grid(row=0,column=0,sticky='w')
        for i,(weapon,variants_for_weapon) in enumerate(groups.items()):
            ttk.Label(inside,text=weapon.split('|')[-1][:40],width=30).grid(row=i,column=0,sticky='w',pady=4)
            default=selected.get(weapon,cfg['weapon_variants'].get(weapon))
            if default not in variants_for_weapon:
                default=next((v for v in variants_for_weapon if 'standalone' in v.casefold()),variants_for_weapon[0])
            var=tk.StringVar(value=default);variants[weapon]=var
            ttk.Combobox(inside,values=variants_for_weapon,textvariable=var,state='readonly',width=44).grid(row=i,column=1,sticky='ew',pady=4)
    draw_variants()
    status=tk.StringVar(value='Save profile records choices. Native setup and the INI finder have separate install buttons with backups.')
    ttk.Label(win,textvariable=status,wraplength=850).pack(anchor='w',padx=14,pady=5)
    def apply():
        selections={w:v.get() for w,v in variants.items()}
        for w,v in selections.items():
            if v not in groups[w]:messagebox.showerror('Variant unavailable',w);return
        new={'first_person_enabled':fp_enabled.get(),'first_person_variant':fp.get(),
             'reshade_enabled':rs_enabled.get(),'reshade_variant':rs.get(),
             'weapon_variants':selections}
        try:
            save(new);report=export_review(new,current_path(game_root),current_path(mods_dir))
            status.set('Saved profile and installation-review report: '+str(report))
        except Exception as e:messagebox.showerror('Options not saved',str(e))
    panel=ttk.Frame(win,padding=10);panel.pack(fill='x')
    ttk.Button(panel,text='Save profile',command=apply).pack(side='left',padx=5)
    ttk.Button(panel,text='Refresh weapon options',command=draw_variants).pack(side='left',padx=5)
    if own_window:ttk.Button(panel,text='Close',command=win.destroy).pack(side='right',padx=5)
    if parent is None:win.mainloop()
    return win

if __name__=='__main__':show_gui()
