"""Shared first-person setup and INI finder/editor panels for the v20 window."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from tkinter.scrolledtext import ScrolledText
import config_install as ci
import mod_manager as mm


def roots(owner,extra=''):
    return [extra,owner.paths['game_root'].get(),owner.paths['archive_dir'].get(),Path.home()/'Downloads',Path.home()/'Desktop'/'Def GOOd']


def native_panel(parent,owner):
    container=ttk.Frame(parent);container.pack(fill='both',expand=True,pady=8)
    fixed=ttk.Frame(container);fixed.pack(fill='x',pady=(0,6))
    canvas=tk.Canvas(container,bg='#111827',highlightthickness=0);scroll=ttk.Scrollbar(container,orient='vertical',command=canvas.yview)
    scroll.pack(side='right',fill='y');canvas.pack(fill='both',expand=True);canvas.configure(yscrollcommand=scroll.set)
    body=ttk.Frame(canvas,padding=8);window=canvas.create_window((0,0),window=body,anchor='nw')
    body.bind('<Configure>',lambda _:canvas.configure(scrollregion=canvas.bbox('all')));canvas.bind('<Configure>',lambda e:canvas.itemconfigure(window,width=e.width))
    body.columnconfigure(1,weight=1)
    cfg=mm.load(mm.CONFIG,{});source=tk.StringVar(value=cfg.get('native_package',''))
    ttk.Label(body,text='Native First Person package (core + options)').grid(row=0,column=0,columnspan=2,sticky='w')
    entry=ttk.Combobox(body,textvariable=source,width=65);entry.grid(row=1,column=0,columnspan=2,sticky='ew',pady=6)
    bar=ttk.Frame(body);bar.grid(row=2,column=0,columnspan=2,sticky='w',pady=6)
    def detected(values):
        entry.configure(values=[str(p) for p in values])
        if values and not Path(source.get(),'core/NativeFirstPerson.ini').is_file():source.set(str(values[0]))
        owner.status.set(f'Found {len(values)} Native First Person packages')
    owner.button(bar,'Find first-person package',lambda:owner.task(lambda:ci.find_native_packages(roots(owner,source.get())),detected)).pack(side='left',padx=(0,8))
    def browse():
        value=filedialog.askdirectory(title='Select folder containing core and options',initialdir=source.get() or str(Path.home()/'Downloads'),parent=owner.root)
        if value:source.set(value)
    owner.button(bar,'Browse',browse).pack(side='left')
    choices={};widgets={};values={'renderer':['dx11','vulkan'],'startup':['manual','enabled'],'input':['Numpad0','F9','Home','F10','Insert','PageUp'],'fov':[str(v) for v in range(70,141,10)],
        'adsfov':['vanilla']+[str(v) for v in range(40,121,10)],'camera':['minimal','standard','extra'],
        'interactions':['vanilla','expanded'],'bodyfollow':['disabled','enabled']}
    if cfg.get('fpv_dx11_only'):values['renderer']=['dx11']
    labels={'renderer':'Renderer','startup':'Startup','fov':'Non-ADS FOV','adsfov':'ADS FOV','camera':'Helmet / mask clearance','interactions':'Interaction camera','bodyfollow':'Body follow','input':'FPV toggle key (choose or type)'}
    saved=cfg.get('native_preset_choices',ci.PRESETS)
    for row,(role,options) in enumerate(values.items(),3):
        value=cfg.get('native_toggle_key',saved.get(role,ci.PRESETS[role])) if role=='input' else saved.get(role,ci.PRESETS[role]);choices[role]=tk.StringVar(value=value if role in ('fov','adsfov','input') or value in options else ci.PRESETS[role])
        ttk.Label(body,text=labels[role]).grid(row=row,column=0,sticky='w',pady=4,padx=(0,10))
        widgets[role]=ttk.Combobox(body,textvariable=choices[role],values=options,state='normal' if role in ('fov','adsfov','input') else 'readonly',width=25)
        widgets[role].grid(row=row,column=1,sticky='ew',pady=4)
    numeric={}
    saved_numbers=cfg.get('native_numeric',{})
    numeric['forward_offset']=tk.StringVar(value=saved_numbers.get('forward_offset',{'minimal':'0.04','standard':'0.10','extra':'0.18'}[choices['camera'].get()]))
    numeric['clip_distance']=tk.StringVar(value=saved_numbers.get('clip_distance','0.005'))
    camera=ttk.LabelFrame(body,text='Camera distance ahead of your head',padding=10);camera.grid(row=11,column=0,columnspan=2,sticky='ew',pady=10);camera.columnconfigure(0,weight=1)
    position=tk.DoubleVar(value=0.10);distance_text=tk.StringVar()
    ttk.Scale(camera,from_=0,to=0.45,variable=position,command=lambda value:numeric['forward_offset'].set(format(float(value),'.3f'))).grid(row=0,column=0,sticky='ew',padx=(0,10))
    ttk.Entry(camera,textvariable=numeric['forward_offset'],width=10).grid(row=0,column=1)
    ttk.Label(camera,text='metres (0 to 0.45)').grid(row=0,column=2,padx=8)
    def show_distance(*_):
        try:
            value=float(numeric['forward_offset'].get())
            if not 0<=value<=0.45:raise ValueError()
            position.set(value);distance_text.set(f'{value*100:g} cm ahead of the head. Larger values give helmets and masks more clearance.')
        except ValueError:distance_text.set('Enter a distance from 0 to 0.45 metres.')
    numeric['forward_offset'].trace_add('write',show_distance);show_distance()
    ttk.Label(camera,textvariable=distance_text,wraplength=680).grid(row=1,column=0,columnspan=3,sticky='w',pady=6)
    presets=ttk.Frame(camera);presets.grid(row=2,column=0,columnspan=3,sticky='w')
    for label,value in [('Near: 4 cm','0.04'),('Default: 10 cm','0.10'),('Helmet: 18 cm','0.18'),('More clearance: 22 cm','0.22')]:
        ttk.Button(presets,text=label,command=lambda v=value:numeric['forward_offset'].set(v)).pack(side='left',padx=(0,6))
    ttk.Label(body,text='Hide nearby geometry (near clip, m)').grid(row=12,column=0,sticky='w',pady=4)
    clipping=ttk.Frame(body);clipping.grid(row=12,column=1,sticky='ew',pady=4)
    ttk.Entry(clipping,textvariable=numeric['clip_distance'],width=10).pack(side='left',padx=(0,8))
    for value,label in [('0.01','1 cm'),('0.04','4 cm'),('0.06','6 cm')]:
        owner.button(clipping,label,lambda v=value:numeric['clip_distance'].set(v)).pack(side='left',padx=3)
    widgets['camera'].bind('<<ComboboxSelected>>',lambda _:numeric['forward_offset'].set({'minimal':'0.04','standard':'0.10','extra':'0.18'}[choices['camera'].get()]))
    chain=tk.BooleanVar(value=cfg.get('native_chain_proxy',True))
    ttk.Checkbutton(body,text='Chain a foreign d3d11.dll wrapper (DXGI ReShade stays separate)',variable=chain).grid(row=13,column=0,columnspan=2,sticky='w',pady=6)
    graphics=tk.BooleanVar(value=cfg.get('native_graphics_compatibility',False))
    ttk.Checkbutton(body,text='Graphics compatibility: keep game blur instead of FPV suppression',variable=graphics).grid(row=14,column=0,columnspan=2,sticky='w',pady=6)
    ttk.Label(body,text='Type FOV 70-140; ADS FOV 0 (vanilla) or 20-140; forward offset 0-0.45 m; near clip 0.001-0.1 m. Game settings in Merge options preserves the game optic FOV instead of the ADS FOV override. Graphics compatibility leaves the game blur behavior unchanged while retaining FPV camera controls. Close the game before installing. Existing files are backed up.',wraplength=760).grid(row=15,column=0,columnspan=2,sticky='w',pady=6)
    def install():
        package=source.get();selected={k:v.get() for k,v in choices.items()};game=owner.paths['game_root'].get();backup=owner.paths['backup_root'].get();chaining=chain.get()
        numbers={k:v.get() for k,v in numeric.items()}
        key=ci.validate_toggle_key(selected['input'])
        compat=graphics.get()
        cfg=mm.load(mm.CONFIG,{});cfg.update(native_package=package,native_preset_choices=selected,native_chain_proxy=chaining,native_numeric=numbers,native_toggle_key=key,native_graphics_compatibility=compat);mm.save(mm.CONFIG,cfg)
        single=cfg.get('native_dx11_single_loader',False)
        effective=dict(selected)
        if cfg.get('preserve_game_sensitivity',False):effective['adsfov']='vanilla'
        def work():return ci.install_files(ci.native_plan(package,game,effective,chaining,numbers,toggle_key=key,graphics_compatibility=compat,dx11_single_loader=single),game,backup)
        def done(receipt):
            cfg=mm.load(mm.CONFIG,{});cfg['last_config_transaction']=str(receipt);mm.save(mm.CONFIG,cfg)
            owner.status.set('Native First Person installed - '+str(receipt))
        owner.task(work,done)
    owner.button(fixed,'Install FPV',install,True).pack(side='left')
    def apply_camera():
        game=owner.paths['game_root'].get();backup=owner.paths['backup_root'].get();numbers={k:v.get() for k,v in numeric.items()};key=ci.validate_toggle_key(choices['input'].get())
        rows=ci.native_camera_plan(game,numbers,key)
        cfg=mm.load(mm.CONFIG,{});cfg.update(native_numeric={**cfg.get('native_numeric',{}),**ci.validate_native_numbers(numbers)},native_toggle_key=key);mm.save(mm.CONFIG,cfg)
        def done(receipt):
            cfg=mm.load(mm.CONFIG,{});cfg['last_config_transaction']=str(receipt);mm.save(mm.CONFIG,cfg);owner.status.set('FPV camera distance and toggle key applied - '+str(receipt))
        owner.task(lambda:ci.install_files(rows,game,backup),done)
    def load_installed():
        settings=ci.installed_native_controls(owner.paths['game_root'].get())
        for key in numeric:
            if key in settings:numeric[key].set(settings[key])
        if 'toggle_key' in settings:choices['input'].set(settings['toggle_key'])
        owner.status.set('Loaded effective installed FPV distance and toggle key')
    owner.button(fixed,'Apply distance & toggle key',apply_camera,True).pack(side='left',padx=8)
    owner.button(fixed,'Load installed settings',load_installed).pack(side='left')
    return {'source':source,'choices':choices,'numeric':numeric}


def ini_panel(parent,owner):
    frame=ttk.Frame(parent);frame.pack(fill='both',expand=True)
    folder=tk.StringVar(value=mm.load(mm.CONFIG,{}).get('native_package',''));source=tk.StringVar();destination=tk.StringVar();encoding=['utf-8']
    top=ttk.Frame(frame);top.pack(fill='x',pady=6);ttk.Label(top,text='Search folder').pack(side='left',padx=(0,8))
    ttk.Entry(top,textvariable=folder).pack(side='left',fill='x',expand=True)
    def browse():
        value=filedialog.askdirectory(parent=owner.root)
        if value:folder.set(value)
    owner.button(top,'Browse',browse).pack(side='left',padx=6)
    selected=ttk.Combobox(frame,textvariable=source,state='readonly');selected.pack(fill='x',pady=6)
    info=tk.StringVar(value='Find or open an INI, edit it, then review its destination before installing.')
    ttk.Label(frame,textvariable=info,wraplength=900).pack(anchor='w')
    def load_file(path):
        text,codec=ci.read_ini(path);encoding[0]=codec
        editor.delete('1.0','end');editor.insert('1.0',text);source.set(str(path))
        destination.set(str(ci.ini_destination(path,owner.paths['game_root'].get())))
        info.set('Editing '+str(path))
    def found(paths):
        selected.configure(values=[str(p) for p in paths]);info.set(f'Found {len(paths)} INI files. Choose one to edit.')
        if paths and not source.get():load_file(paths[0])
    def search():
        search_roots=roots(owner,folder.get());owner.task(lambda:ci.find_inis(search_roots),found)
    buttons=ttk.Frame(frame);buttons.pack(fill='x',pady=6)
    owner.button(buttons,'Find INI files',search).pack(side='left',padx=(0,8))
    def open_ini():
        path=filedialog.askopenfilename(title='Open configuration INI',initialdir=folder.get() or owner.paths['game_root'].get(),filetypes=[('INI files','*.ini')],parent=owner.root)
        if path:load_file(path)
    owner.button(buttons,'Open INI',open_ini).pack(side='left')
    editor=ScrolledText(frame,bg='#0b1220',fg='#cbd5e1',insertbackground='white',font=('Consolas',10),wrap='none',undo=True,height=10);editor.pack(fill='both',expand=True,pady=6)
    selected.bind('<<ComboboxSelected>>',lambda _:load_file(source.get()))
    destbar=ttk.Frame(frame);destbar.pack(fill='x',pady=6);ttk.Label(destbar,text='Install to').pack(side='left',padx=(0,8))
    ttk.Entry(destbar,textvariable=destination).pack(side='left',fill='x',expand=True)
    def choose_target():
        path=filedialog.asksaveasfilename(title='Choose INI destination inside the game folder',initialdir=owner.paths['game_root'].get(),initialfile=Path(source.get()).name,defaultextension='.ini',filetypes=[('INI','*.ini')],parent=owner.root)
        if path:destination.set(path)
    owner.button(destbar,'Choose destination',choose_target).pack(side='left',padx=6)
    ttk.Label(frame,text='Native option INIs override the master INI. Installing an edited NativeFirstPerson.ini also updates its matching option values.',wraplength=900).pack(anchor='w',pady=4)
    actions=ttk.Frame(frame);actions.pack(fill='x',pady=6)
    def export():
        if not source.get():raise ValueError('Open an INI first')
        import time
        path=mm.WORK/'config_overrides'/(time.strftime('%Y%m%d_%H%M%S')+'_'+Path(source.get()).name);path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(editor.get('1.0','end-1c').encode(encoding[0]));owner.status.set('Staged INI saved - '+str(path))
    def install():
        if not source.get():messagebox.showinfo('Open an INI','Find or open an INI first.',parent=owner.root);return
        rows=ci.ini_plan(source.get(),destination.get(),editor.get('1.0','end-1c'),encoding[0]);game=owner.paths['game_root'].get();backup=owner.paths['backup_root'].get()
        def done(receipt):
            cfg=mm.load(mm.CONFIG,{});cfg['last_config_transaction']=str(receipt);mm.save(mm.CONFIG,cfg)
            owner.status.set('Edited INI installed - '+str(receipt))
        owner.task(lambda:ci.install_files(rows,game,backup),done)
    owner.button(actions,'Save staged copy',export).pack(side='left',padx=(0,8))
    owner.button(actions,'Install edited INI',install,True).pack(side='left')
    return {'source':source,'destination':destination,'editor':editor}


def reshade_panel(parent,owner):
    import reshade_install as ri
    frame=ttk.Frame(parent,padding=8);frame.pack(fill='both',expand=True)
    top=ttk.Frame(frame);top.pack(fill='x',pady=(0,12))
    cfg=mm.load(mm.CONFIG,{})
    setup=tk.StringVar(value=cfg.get('reshade_setup',str(mm.ROOT.parent/'GRB_recs/ReShade_Setup_6.8.0.exe')))
    preset=tk.StringVar(value=cfg.get('reshade_preset',''))
    shaders=tk.StringVar(value=cfg.get('reshade_shader_folder',''))
    runtime=tk.StringVar(value=cfg.get('reshade_runtime_override',''))
    renderer=tk.StringVar(value=cfg.get('reshade_renderer','dx11'))
    fetch=tk.BooleanVar(value=cfg.get('reshade_download_effects',True))
    def install():
        exe=setup.get();game=owner.paths['game_root'].get();backup=owner.paths['backup_root'].get()
        chosen=preset.get();local=shaders.get();network=fetch.get();api=renderer.get();override=runtime.get()
        values=mm.load(mm.CONFIG,{});values.update(reshade_setup=exe,reshade_preset=chosen,reshade_shader_folder=local,reshade_renderer=api,reshade_download_effects=network,reshade_runtime_override=override);mm.save(mm.CONFIG,values)
        if api=='vulkan':
            owner.task(lambda:ri.open_vulkan_setup(exe,game),lambda _:owner.status.set('Official Vulkan setup opened - complete installation in that window'))
            return
        def done(receipt):
            values=mm.load(mm.CONFIG,{});values['last_config_transaction']=str(receipt);mm.save(mm.CONFIG,values)
            owner.status.set('ReShade installed - '+str(receipt))
        owner.task(lambda:ri.install(exe,game,backup,chosen,local,network,owner.notify,runtime_override=override),done)
    owner.button(top,'Install ReShade',install,True).pack(side='left')
    ttk.Label(top,text='DX11 automatic; Vulkan opens the official setup').pack(side='left',padx=12)
    body=ttk.Frame(frame);body.pack(fill='x');body.columnconfigure(1,weight=1)
    for row,label,var,kind in [(0,'Official ReShade setup EXE',setup,'exe'),(1,'Preset INI or archive',preset,'preset'),(2,'Shader / texture folder (optional)',shaders,'folder'),(3,'Full add-on runtime DLL (optional)',runtime,'dll')]:
        ttk.Label(body,text=label).grid(row=row,column=0,sticky='w',padx=(0,10),pady=6)
        ttk.Entry(body,textvariable=var).grid(row=row,column=1,sticky='ew',pady=6)
        def browse(v=var,k=kind):
            path=filedialog.askdirectory(parent=owner.root) if k=='folder' else filedialog.askopenfilename(parent=owner.root,filetypes=[('Setup','*.exe')] if k=='exe' else [('Runtime DLL','*.dll')] if k=='dll' else [('Preset','*.ini *.zip *.rar *.7z')])
            if path:v.set(path)
        owner.button(body,'Browse',browse).grid(row=row,column=2,padx=6)
    ttk.Label(body,text='Renderer').grid(row=4,column=0,sticky='w',pady=6)
    ttk.Combobox(body,textvariable=renderer,values=['dx11','vulkan'],state='readonly').grid(row=4,column=1,sticky='ew')
    ttk.Checkbutton(frame,text='Download required effects and textures from the official ReShade package list',variable=fetch).pack(anchor='w',pady=12)
    def find():
        def scan():
            files=list(ci.walk([mm.ROOT.parent/'GRB_recs',Path.home()/'Downloads']))
            return ([p for p in files if p.name.lower().startswith('reshade_setup') and p.suffix.lower()=='.exe'],[p for p in files if 'reshade' in p.name.lower() and p.suffix.lower() in ('.ini','.zip','.rar','.7z')])
        def done(result):
            exes,presets=result
            if exes:setup.set(str(exes[0]))
            if presets and not preset.get():preset.set(str(presets[0]))
            owner.status.set(f'Found {len(exes)} ReShade setups and {len(presets)} preset candidates; review the selected paths')
        owner.task(scan,done)
    owner.button(frame,'Find ReShade setup / preset',find).pack(anchor='w',pady=6)
    ttk.Label(frame,text='Choose the actual author preset file. DX11 installation stages the runtime and checks active shader dependencies before writing. ReShade uses DXGI alongside the verified Native FPV loader. Close the game first. Config installations can be restored in Diagnostics & recovery.',wraplength=740).pack(anchor='w',pady=12)
    return {'setup':setup,'preset':preset,'shaders':shaders,'renderer':renderer,'runtime':runtime}


def merge_panel(parent,owner):
    container=ttk.Frame(parent);container.pack(fill='both',expand=True)
    fixed=ttk.Frame(container,padding=(14,8));fixed.pack(fill='x')
    canvas=tk.Canvas(container,bg='#111827',highlightthickness=0)
    scroll=ttk.Scrollbar(container,orient='vertical',command=canvas.yview)
    scroll.pack(side='right',fill='y');canvas.pack(fill='both',expand=True);canvas.configure(yscrollcommand=scroll.set)
    frame=ttk.Frame(canvas,padding=14);window=canvas.create_window((0,0),window=frame,anchor='nw');frame.columnconfigure(1,weight=1)
    frame.bind('<Configure>',lambda _:canvas.configure(scrollregion=canvas.bbox('all')))
    canvas.bind('<Configure>',lambda event:canvas.itemconfigure(window,width=event.width))
    cfg=mm.load(mm.CONFIG,{})
    options=[('weapon_damage_priority','Weapon damage in merged files',['Damage Balance','WABO'],'Damage Balance'),
        ('wabo_difficulty','WABO difficulty',['1_Easy','2_Normal','3_Hard','4_Realistic (Hardcore)'],'4_Realistic (Hardcore)'),
        ('thermal_priority','Thermal vision',['Standard night vision and thermal','Pathfinder white thermal vision','Disable thermal vision'],'Standard night vision and thermal'),
        ('item_wheel_variant','Item wheel',['Vanilla item wheel','Syringe and Binoculars','Syringe and Breaching torch'],'Vanilla item wheel'),
        ('nightmare_caller_priority','Nightmare caller resources',['New Reinforcements','Traffic Improvements'],'New Reinforcements'),
        ('preserve_game_sensitivity','Sensitivity / optic FOV',['Game settings','Mod settings'],'Game settings'),
        ('foliage_profile','Enemy vision / concealment',['Mod settings','Vanilla concealment'],'Vanilla concealment'),
        ('npc_trigger_priority','Vehicle / NPC trigger rules',['Vanilla automatic fire','Various Tweaks single trigger'],'Vanilla automatic fire'),
        ('bcm_handguard','BCM Koblin handguard',['Black','FDE'],'Black'),
        ('ammo_capacity_profile','Ammo reserve',['Difficulty preset','Vanilla'],'Vanilla'),
        ('suppressor_profile','Suppressed detection',['Difficulty preset','Vanilla-like'],'Vanilla-like'),
        ('lmg_accuracy_profile','LMG firing accuracy',['Original','Precision'],'Original'),
        ('shotgun_damage_profile','Player shotgun damage',['Author values','100','125','150','175','200'],'150')]
    controls={}
    ttk.Label(frame,text='Merge priorities for future builds',font=('Segoe UI Semibold',14)).grid(row=0,column=0,columnspan=2,sticky='w',pady=(0,14))
    for row,(key,label,values,default) in enumerate(options,1):
        controls[key]=tk.StringVar(value=('Game settings' if cfg.get(key,True) else 'Mod settings') if key=='preserve_game_sensitivity' else cfg.get(key,default))
        ttk.Label(frame,text=label).grid(row=row,column=0,sticky='w',padx=(0,12),pady=8)
        ttk.Combobox(frame,textvariable=controls[key],values=values,state='readonly',width=35).grid(row=row,column=1,sticky='ew',pady=8)
    controls['preserve_night_vision']=tk.BooleanVar(value=cfg.get('preserve_night_vision',True))
    ttk.Checkbutton(frame,text='Keep normal night vision alongside white-hot vision',variable=controls['preserve_night_vision']).grid(row=len(options)+1,column=0,columnspan=2,sticky='w',pady=8)
    def save():
        config=mm.load(mm.CONFIG,{});config.update({k:v.get() for k,v in controls.items()});config['preserve_game_sensitivity']=controls['preserve_game_sensitivity'].get()=='Game settings';mm.save(mm.CONFIG,config)
        owner.status.set('Merge choices saved - scan and stage a new build to apply them')
    owner.button(fixed,'Save merge options',save,True).pack(side='left')
    def priorities():
        import priorities_ui
        priorities_ui.show(parent,owner)
    owner.button(fixed,'Conflict priorities',priorities).pack(side='left',padx=8)
    ttk.Label(fixed,text='Save, then scan and stage to apply these choices.').pack(side='left',padx=12)
    ttk.Label(frame,text='White-hot vision: after installation, switch to Pathfinder once and activate its vision, then switch back to your preferred class. The author recommends this if white-hot does not appear. The white-hot-only files disable ordinary thermal and, unless checked above, green night vision.',wraplength=740).grid(row=len(options)+3,column=0,columnspan=2,sticky='w',pady=8)
    ttk.Label(frame,text='Damage Balance preserves its damage values while WABO supplies ballistics and handling. Precision LMGs use the existing HTI spread pattern and sway 0.2, preserving recoil, damage, models and attachments. The unsupported T95 bivouac definition is preserved. Vanilla ammo/suppressors use the author optional presets without changing Hardcore health. Vanilla concealment restores the original vision and bush definitions; gameplay still determines which foliage occludes sight. Incompatible wheel swaps are held as a complete group. Saved choices affect a new scan/stage, not an already installed or staged build.',wraplength=740).grid(row=len(options)+4,column=0,columnspan=2,sticky='w',pady=8)
    return controls
