"""Forge Assembly desktop app. All archive work is staged before deployment."""
import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

import mod_manager as mm
import deployment_policy as policy
from toolkit_config import DEFAULT_TOOLKIT

ROOT = Path(__file__).resolve().parent


class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.busy = False
        self.manifest = None
        self.buttons = []
        root.title('Forge Assembly - Breakpoint Mod Installer')
        root.geometry('1240x850'); root.minsize(1040, 740)
        root.configure(bg='#111827')
        style = ttk.Style(root); style.theme_use('clam')
        style.configure('.', background='#111827', foreground='#e5e7eb', font=('Segoe UI', 10))
        style.configure('TFrame', background='#111827')
        style.configure('Card.TFrame', background='#1f2937')
        style.configure('TLabel', background='#111827', foreground='#e5e7eb')
        style.configure('Title.TLabel', font=('Segoe UI Semibold', 23))
        style.configure('Muted.TLabel', foreground='#9ca3af')
        style.configure('TButton', background='#374151', padding=(14, 9), borderwidth=0)
        style.map('TButton', background=[('active', '#4b5563'), ('disabled', '#253044')],
                  foreground=[('disabled', '#7b8798')])
        style.configure('Primary.TButton', background='#2563eb', foreground='white')
        style.map('Primary.TButton', background=[('active', '#1d4ed8'), ('disabled', '#253044')])
        style.configure('TEntry', fieldbackground='#1f2937', foreground='#f9fafb', padding=7)
        style.configure('TCombobox', fieldbackground='#1f2937', foreground='#f9fafb', padding=6)
        style.map('TCombobox', fieldbackground=[('readonly', '#1f2937')], foreground=[('readonly', '#f9fafb')])
        style.configure('Treeview', background='#1f2937', fieldbackground='#1f2937', foreground='#e5e7eb', rowheight=27)
        style.configure('Treeview.Heading', background='#374151', foreground='#f9fafb')
        style.configure('TNotebook', background='#111827', borderwidth=0)
        style.configure('TNotebook.Tab', background='#253044', padding=(14,8))
        style.map('TNotebook.Tab', background=[('selected','#2563eb')], foreground=[('selected','white')])
        prefs = mm.load(mm.CONFIG, mm.DEFAULT_SETTINGS)
        self.paths = {k: tk.StringVar(value=v) for k,v in {
            'archive_dir': prefs.get('archive_dir') or str(ROOT.parent / 'DEEF_Mods'),
            'game_root': prefs.get('game_root') or r'C:\Program Files (x86)\Ubisoft\Ubisoft Game Launcher\games\Ghost Recon Breakpoint',
            'toolkit': prefs.get('toolkit') or DEFAULT_TOOLKIT,
            'backup_root': str(mm.WORK / 'backups')}.items()}
        self.status = tk.StringVar(value='Ready - game verification must finish before a new baseline is recorded')
        self.baseline_text = tk.StringVar()
        self.stage_text = tk.StringVar(value='No staged batch selected')
        self.profile = tk.StringVar(value='Enabled mod selection')
        self.single_mod = tk.StringVar()
        shell = ttk.Frame(root, padding=18); shell.pack(fill='both', expand=True)
        shell.columnconfigure(1, weight=1); shell.rowconfigure(0, weight=1)
        sidebar = ttk.Frame(shell, width=185); sidebar.grid(row=0,column=0,sticky='ns',padx=(0,20))
        ttk.Label(sidebar,text='FORGE',style='Title.TLabel').pack(anchor='w',pady=(0,2))
        ttk.Label(sidebar,text='ASSEMBLY',style='Muted.TLabel').pack(anchor='w',pady=(0,24))
        self.content = ttk.Frame(shell); self.content.grid(row=0,column=1,sticky='nsew')
        self.content.columnconfigure(0,weight=1); self.content.rowconfigure(0,weight=1)
        self.pages = {}; self.hosts={};self.scrolls={}; self.loaded = set()
        for key,label in [('setup','Setup'),('library','Mod library'),('options','First person & ReShade'),
                          ('review','Conflicts & new assets'),('diagnostics','Diagnostics & recovery')]:
            host = ttk.Frame(self.content); host.grid(row=0,column=0,sticky='nsew')
            self.hosts[key]=host
            if key in ('setup','diagnostics'):
                canvas=tk.Canvas(host,background='#111827',highlightthickness=0)
                scroll=ttk.Scrollbar(host,orient='vertical',command=canvas.yview)
                scroll.pack(side='right',fill='y');canvas.pack(fill='both',expand=True)
                canvas.configure(yscrollcommand=scroll.set)
                page=ttk.Frame(canvas);window=canvas.create_window((0,0),window=page,anchor='nw')
                page.bind('<Configure>',lambda event,c=canvas:c.configure(scrollregion=c.bbox('all')))
                canvas.bind('<Configure>',lambda event,c=canvas,w=window:c.itemconfigure(w,width=event.width))
                self.scrolls[key]=canvas
            else:
                page=host
            self.pages[key] = page
            ttk.Button(sidebar,text=label,command=lambda k=key:self.navigate(k)).pack(fill='x',pady=4)
        ttk.Label(sidebar,text='AnvilToolkit stays in its\noriginal complete folder.',style='Muted.TLabel').pack(anchor='w',pady=(30,0))
        self.setup_page(); self.diagnostics_page()
        footer = ttk.Frame(shell); footer.grid(row=1,column=0,columnspan=2,sticky='ew',pady=(15,0))
        ttk.Label(footer,textvariable=self.status,wraplength=1150).pack(anchor='w',pady=(0,6))
        self.progress = ttk.Progressbar(footer,mode='indeterminate'); self.progress.pack(fill='x',pady=(0,7))
        self.log = ScrolledText(footer,height=7,bg='#0b1220',fg='#cbd5e1',insertbackground='white',
                                relief='flat',font=('Consolas',9),wrap='word')
        self.log.pack(fill='x'); self.log.configure(state='disabled')
        self.navigate('setup'); self.refresh_state()
        previous=prefs.get('last_stage_manifest')
        if previous and Path(previous).is_file() and policy.WORK.resolve() in Path(previous).resolve().parents:
            try:
                state=self.validate_batch(previous)
                self.manifest=previous
                self.stage_text.set('Previous staged batch - '+str(len(state.get('finishedRoutes',[])))+' replacement edits - '+Path(previous).name)
                self.status.set('Previous staged batch loaded - installation and gameplay status are in the reports')
            except (ValueError,RuntimeError,KeyError,TypeError):pass
        root.bind('<MouseWheel>',lambda e:self.scrolls[self.active_page].yview_scroll(-int(e.delta/120),'units')
            if self.active_page in self.scrolls else None)
        root.after(75,self.drain); root.protocol('WM_DELETE_WINDOW',self.close)

    def button(self,parent,label,action,primary=False):
        b=ttk.Button(parent,text=label,command=action,style='Primary.TButton' if primary else 'TButton')
        self.buttons.append(b); return b

    def heading(self,page,title,subtitle):
        ttk.Label(page,text=title,style='Title.TLabel').pack(anchor='w',pady=(0,8))
        ttk.Label(page,text=subtitle,style='Muted.TLabel',wraplength=920).pack(anchor='w',pady=(0,18))

    def setup_page(self):
        page=self.pages['setup']
        self.heading(page,'Build your mod setup','Scan the selected archives and stage their complete assets, ordered foundations and compatibility repairs before installation.')
        ttk.Label(page,textvariable=self.baseline_text,wraplength=920).pack(anchor='w',pady=(0,12))
        fields=ttk.Frame(page,padding=14,style='Card.TFrame'); fields.pack(fill='x')
        fields.columnconfigure(1,weight=1)
        for row,(key,label) in enumerate([('archive_dir','Mod archives'),('game_root','Game folder'),
                                         ('toolkit','AnvilToolkit folder'),('backup_root','Backups')]):
            ttk.Label(fields,text=label,background='#1f2937').grid(row=row,column=0,sticky='w',padx=(0,12),pady=5)
            ttk.Entry(fields,textvariable=self.paths[key]).grid(row=row,column=1,sticky='ew',pady=5)
            self.button(fields,'Browse',lambda k=key:self.browse(k)).grid(row=row,column=2,padx=(10,0))
        self.button(page,'Install saved complete setup',self.install_proven,True).pack(anchor='w',pady=(14,6))
        ttk.Label(page,text='Scan & stage rebuilds from the enabled mod archives, including newly added mods. Install saved complete setup is a separate recovery shortcut.',style='Muted.TLabel',wraplength=920).pack(anchor='w',pady=(0,10))
        ttk.Separator(page).pack(fill='x',pady=10)
        profiles=ttk.Frame(page);profiles.pack(fill='x',pady=15)
        ttk.Label(profiles,text='Stage profile').pack(side='left',padx=(0,12))
        profile_choice=ttk.Combobox(profiles,textvariable=self.profile,values=['Enabled mod selection','One mod diagnostic'],
                     state='readonly',width=25)
        profile_choice.pack(side='left')
        self.mod_choice=ttk.Combobox(profiles,textvariable=self.single_mod,state='disabled',width=43)
        self.mod_choice.pack(side='left',padx=10)
        profile_choice.bind('<<ComboboxSelected>>',lambda _:self.mod_choice.configure(
            state='readonly' if self.profile.get()=='One mod diagnostic' else 'disabled'))
        ttk.Label(page,text='One mod diagnostic creates a report for troubleshooting; it cannot be installed.',
                  style='Muted.TLabel').pack(anchor='w')
        self.button(profiles,'Refresh',self.refresh_mods).pack(side='left')
        actions=ttk.Frame(page);actions.pack(fill='x',pady=5)
        self.button(actions,'1 - Record verified baseline',self.baseline,True).pack(side='left',padx=(0,8))
        self.button(actions,'2 - Scan & stage mods',self.stage,True).pack(side='left',padx=8)
        self.button(actions,'Analyze only',lambda:self.stage(analyze_only=True)).pack(side='left',padx=8)
        ttk.Separator(page).pack(fill='x',pady=18)
        ttk.Label(page,textvariable=self.stage_text,wraplength=920).pack(anchor='w',pady=(0,10))
        installbar=ttk.Frame(page);installbar.pack(fill='x')
        self.button(installbar,'Select a staged batch',self.select_batch).pack(side='left',padx=(0,8))
        self.button(installbar,'3 - Install reviewed batch',self.install).pack(side='left',padx=8)
        ttk.Label(page,text='Build order: prepatched Forge files -> complete Forge mods -> resource edits inside those results. '
                  'Competing whole-file variants are reported. New asset experiments have their own private workspace. Existing complete parents are preserved; '
                  'ambiguous choices remain in the review report. Game compatibility still needs an in-game test.',
                  style='Muted.TLabel',wraplength=920).pack(anchor='w',pady=18)
        self.refresh_mods()

    def diagnostics_page(self):
        page=self.pages['diagnostics']
        self.heading(page,'Diagnostics & recovery','Check the tool build, inspect private Forge tables, or restore a recorded installation.')
        for label,fn in [('Open reports',lambda:self.open_folder(mm.WORK)),('Run tool self-tests',self.self_tests),('Open crash investigation',lambda:self.open_folder(mm.WORK/'diagnostics')),
                         ('Observe game startup',self.startup_test),
                         ('Restore a config / first-person installation',self.restore_config),
                         ('Audit two private Forge files',self.audit_files),('Restore an installation backup',self.restore),
                         ('Stage new assets privately',self.experiments)]:
            self.button(page,label,fn).pack(anchor='w',pady=7)
        self.button(page,'Test missing KS1 / SPEAR assets privately',self.weapon_asset_test).pack(anchor='w',pady=7)
        self.button(page,'Open operation error reports',lambda:self.open_folder(mm.WORK/'diagnostics'/'errors')).pack(anchor='w',pady=7)
        ttk.Label(page,text='Self-tests exercise private temporary fixtures. A successful hash or Anvil readback does not prove '
                  'that a mod can load in the game. Recovery uses the saved hashes and atomic replacement.',
                  style='Muted.TLabel',wraplength=880).pack(anchor='w',pady=20)

    def navigate(self,key,force=False):
        if self.busy and key not in self.loaded and not force:return
        if key=='review' and not self.busy:self.save_paths()
        if key not in self.loaded:
            if key=='options':
                import additional_options
                additional_options.show_gui(parent=self.pages[key],embedded=True,
                    game_root=lambda:self.paths['game_root'].get(),mods_dir=lambda:self.paths['archive_dir'].get(),installer_owner=self)
            elif key=='library':
                self.save_paths();self.library_page()
            elif key=='review':self.conflicts_page()
            self.loaded.add(key)
        elif key=='review' and not self.busy:self.refresh_merge_settings()
        self.active_page=key;self.hosts[key].tkraise()

    def conflicts_page(self):
        page=self.pages['review'];self.heading(page,'Conflicts & new assets','Choose variants, review completeness and merge gameplay settings before installation.')
        bar=ttk.Frame(page);bar.pack(fill='x',pady=(0,8))
        def priorities():
            import priorities_ui
            priorities_ui.show(self.root,self)
        self.button(bar,'Conflict priorities',priorities).pack(side='left')
        self.button(bar,'Review mod completeness',self.review_coverage).pack(side='left',padx=10)
        book=ttk.Notebook(page);book.pack(fill='both',expand=True)
        settings=ttk.Frame(book);routes=ttk.Frame(book)
        book.add(settings,text='Gameplay conflicts');book.add(routes,text='Routes & new assets')
        import config_widgets,resolver
        self.merge_controls=config_widgets.merge_panel(settings,self)
        resolver.show_gui(parent=routes)

    def library_page(self):
        page=self.pages['library'];self.heading(page,'Mod library','Choose enabled archives and stage INI edits in this workspace.')
        book=ttk.Notebook(page);book.pack(fill='both',expand=True)
        archives=ttk.Frame(book,padding=10);ini=ttk.Frame(book,padding=10)
        book.add(archives,text='Archives');book.add(ini,text='INI workspace')
        bar=ttk.Frame(archives);bar.pack(fill='x',pady=(0,10))
        table=ttk.Frame(archives);table.pack(fill='both',expand=True)
        tree=ttk.Treeview(table,columns=('Enabled','Archive','Files','Status'),show='headings',selectmode='browse')
        for label,width in [('Enabled',75),('Archive',530),('Files',60),('Status',145)]:
            tree.heading(label,text=label);tree.column(label,width=width,minwidth=55)
        y=ttk.Scrollbar(table,orient='vertical',command=tree.yview);y.pack(side='right',fill='y')
        x=ttk.Scrollbar(table,orient='horizontal',command=tree.xview);x.pack(side='bottom',fill='x')
        tree.configure(yscrollcommand=y.set,xscrollcommand=x.set);tree.pack(fill='both',expand=True)
        current={}
        def draw(mods):
            current.clear();current.update(mods)
            tree.delete(*tree.get_children())
            for key,mod in sorted(mods.items()):
                tree.insert('','end',iid=key,values=('ON' if mod.get('enabled',True) else 'OFF',key,
                    len(mod.get('members',[])),mod.get('status','Awaiting scan')))
            if hasattr(self,'refresh_merge_settings'):self.refresh_merge_settings()
        def refresh():
            folder=self.paths['archive_dir'].get()
            def work():
                mods=mm.scan_archives(folder,mm.load(mm.STATE,{}));mm.save(mm.STATE,mods);return mods
            self.task(work,draw)
        def toggle():
            selected=tree.selection()
            if not selected:return
            key=selected[0];current[key]['enabled']=not current[key].get('enabled',True)
            current[key]['_enabled_by_user']=True;mm.save(mm.STATE,current);draw(dict(current))
            tree.selection_set(key)
        self.button(bar,'Refresh archives',refresh).pack(side='left',padx=(0,8))
        self.button(bar,'Toggle selected archive',toggle).pack(side='left',padx=8)
        tree.bind('<Double-1>',lambda _:toggle() if not self.busy else None)
        draw(mm.load(mm.STATE,{}))
        import config_widgets
        self.ini_workspace=config_widgets.ini_panel(ini,self)

    def browse(self,key):
        value=filedialog.askdirectory(initialdir=self.paths[key].get(),parent=self.root)
        if value:self.paths[key].set(value);self.save_paths();self.refresh_mods()

    def save_paths(self):
        settings=mm.load(mm.CONFIG,mm.DEFAULT_SETTINGS)
        settings.update({k:v.get() for k,v in self.paths.items()});mm.save(mm.CONFIG,settings)

    def refresh_mods(self):
        folder=Path(self.paths['archive_dir'].get())
        names=sorted(p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower() in mm.MOD_EXT) if folder.is_dir() else []
        self.mod_choice.configure(values=names)
        if self.single_mod.get() not in names:self.single_mod.set(names[0] if names else '')

    def refresh_state(self):
        state=policy.recovery_state()
        self.baseline_text.set('Baseline: waiting for completed Ubisoft verification' if state.get('installationPaused',True)
            else 'Baseline recorded - '+str(len(state.get('hashes',{})))+' Forge files - verified stages only')

    def notify(self,text):self.events.put(('log',str(text)))

    def lock_controls(self,parent):
        for widget in parent.winfo_children():
            if widget.winfo_class() in ('TButton','TEntry','TCombobox','TCheckbutton','TRadiobutton'):
                previous=widget.cget('state')
                if str(previous)!='disabled':self.locked.append((widget,previous));widget.configure(state='disabled')
            self.lock_controls(widget)

    def task(self,fn,done=None):
        if self.busy:return
        self.busy=True;self.progress.start(12);self.status.set('Working - progress appears below')
        self.locked=[]
        self.lock_controls(self.content)
        def worker():
            try:self.events.put(('done',(fn(),done)))
            except Exception as exc:
                from conflict_flow import StagingCancelled
                if isinstance(exc,StagingCancelled):self.events.put(('cancelled',str(exc)))
                else:
                    import traceback
                    self.events.put(('error',traceback.format_exc()))
        threading.Thread(target=worker,daemon=True).start()

    def drain(self):
        try:
            while True:
                kind,value=self.events.get_nowait()
                if kind=='log':
                    self.log.configure(state='normal');self.log.insert('end',value)
                    if not value.endswith('\n'):self.log.insert('end','\n')
                    self.log.see('end');self.log.configure(state='disabled')
                elif kind=='conflict':
                    import priorities_ui
                    self.navigate('review',force=True);self.lock_controls(self.content);self.progress.stop()
                    self.status.set('Build waiting - choose a resource variant to continue')
                    def resume(request=value):request.answer(True);self.progress.start(12)
                    try:
                        priorities_ui.show(self.root,self,report_path=value.report,resume=True,on_saved=resume,on_cancel=lambda request=value:request.answer(False))
                    except Exception as exc:
                        value.answer(False);self.notify('Conflict chooser could not open: '+str(exc))
                else:
                    self.busy=False;self.progress.stop()
                    for widget,previous in self.locked:
                        if widget.winfo_exists():widget.configure(state=previous)
                    if kind=='error':
                        import friendly_errors
                        try:
                            title,summary,report=friendly_errors.explain(value,mm.WORK)
                            self.status.set(title+' - details saved in Diagnostics')
                            self.notify('ERROR: '+value+'\nDiagnostic report: '+str(report))
                            messagebox.showerror(title,summary+'\n\nFull details: Diagnostics & recovery > Open operation error reports\nReport: '+report.name,parent=self.root)
                        except OSError:
                            self.status.set('Operation stopped - diagnostic report could not be saved')
                            self.notify('ERROR: '+value)
                            messagebox.showerror('Operation stopped','The operation could not finish. Full details are in the activity log below. The diagnostic report could not be saved; check free disk space and folder permissions.',parent=self.root)
                    elif kind=='cancelled':self.status.set(value);self.notify(value)
                    else:
                        result,done=value
                        self.status.set('Operation complete')
                        if done:done(result)
                        if hasattr(self,'refresh_merge_settings'):self.refresh_merge_settings()
                    self.refresh_state()
        except queue.Empty:pass
        self.root.after(75,self.drain)

    def baseline(self):
        if not messagebox.askyesno('Record fresh game baseline','Has Ubisoft finished verifying the game files?\n\n'
                'Record their hashes now so future batches can be checked against this recovered version.',parent=self.root):return
        self.save_paths();game=self.paths['game_root'].get()
        self.task(lambda:policy.record_baseline(game,self.notify),
                  lambda _:self.status.set('Clean baseline recorded - scan and stage when ready'))

    def stage(self,analyze_only=False):
        try:policy.require_ready(self.paths['game_root'].get())
        except Exception as exc:messagebox.showinfo('Baseline needed',str(exc),parent=self.root);return
        self.manifest=None
        self.save_paths();paths={k:v.get() for k,v in self.paths.items()}
        selected=self.single_mod.get() if self.profile.get()=='One mod diagnostic' else None
        if selected is not None and not selected:
            messagebox.showerror('Choose a mod','Choose one archive for the diagnostic stage.',parent=self.root);return
        def work():
            from auto_setup_gui import perform
            previous_selection=mm.load(mm.STATE,{}) if selected else None
            saved=None
            if selected:
                saved=mm.scan_archives(paths['archive_dir'],mm.load(mm.STATE,{}))
                for key,mod in saved.items():
                    mod.update(enabled=Path(key).name==selected,_enabled_by_user=True)
            try:
                return perform(paths['archive_dir'],paths['game_root'],paths['toolkit'],paths['backup_root'],
                    stage=not analyze_only,install=False,allow_subset=True,full_game_scan=True,
                    saved_mods=saved,notify=self.notify,choose_conflict=self.request_conflict_choice,
                    build_scope='DIAGNOSTIC_SINGLE_MOD' if selected else 'ENABLED_MOD_SELECTION')
            finally:
                if previous_selection is not None:mm.save(mm.STATE,previous_selection)
        def done(result):
            run,report=result
            if not report.get('installable',True):
                self.manifest=None
                self.stage_text.set('Diagnostic/analysis only - report '+str(run))
                self.status.set('Cannot install this result. Choose Enabled mod selection and Scan and stage for the complete setup.')
                return
            self.manifest=report.get('batch')
            if self.manifest:
                cfg=mm.load(mm.CONFIG,mm.DEFAULT_SETTINGS);cfg['last_stage_manifest']=self.manifest;mm.save(mm.CONFIG,cfg)
            self.stage_text.set('Stage: '+report['state']+' - '+str(report.get('selectedArchiveCount','?'))+' enabled archives, '+str(report.get('stagedForges',0))+' Forges, '+str(report.get('stagedRoutes',0))+' edits - report '+str(run))
            incomplete=report.get('incompleteMods',0)
            self.status.set(f'Stage has {incomplete} incomplete mods - review MOD_COVERAGE.txt' if incomplete else 'Stage ready for review' if self.manifest else 'Analysis complete - review the reports')
            if incomplete:self.navigate('review')
        self.task(work,done)

    def request_conflict_choice(self,report,message):
        from conflict_flow import ChoiceRequest
        request=ChoiceRequest(report,message);self.events.put(('conflict',request))
        return request.wait()

    def validate_batch(self,manifest):
        state=json.loads(Path(manifest).read_text(encoding='utf-8'))
        policy.require_install(state,manifest);return state

    def select_batch(self):
        picked=filedialog.askopenfilename(initialdir=mm.WORK,title='Select a verified staged batch',filetypes=[('Batch JSON','*.json')])
        if not picked:return
        try:
            state=self.validate_batch(picked)
            self.manifest=picked
            self.stage_text.set(f"Selected: {Path(picked).name} - {len(state.get('finishedRoutes',[]))} replacements")
        except Exception as exc:messagebox.showerror('Batch needs review',str(exc),parent=self.root)

    def review_coverage(self):
        if not self.manifest:return
        folder=Path(self.manifest).parent;source=folder/'characterization/AUTO_CHARACTERIZATION.json'
        if not source.is_file():
            messagebox.showinfo('Coverage unavailable','Choose a layered stage with its characterization report.',parent=self.root);return
        import mod_coverage
        report=mod_coverage.write(json.loads(source.read_text(encoding='utf-8')),json.loads(Path(self.manifest).read_text(encoding='utf-8')),folder)
        self.status.set(f"{report['incompleteMods']} incomplete mods - see MOD_COVERAGE.txt")
        os.startfile(folder/'MOD_COVERAGE.txt')

    def weapon_asset_test(self):
        if not self.manifest:return
        source=Path(self.manifest).parent/'characterization/AUTO_CHARACTERIZATION.json'
        if not source.is_file():return
        import weapon_asset_trial
        toolkit=self.paths['toolkit'].get()
        self.task(lambda:weapon_asset_trial.run(source,toolkit,self.notify),lambda result:self.status.set('Private weapon asset report - '+str(result[0]/'RESULT.json')))

    def install(self):
        if not self.manifest:self.select_batch()
        if not self.manifest:return
        try:
            state=self.validate_batch(self.manifest)
        except Exception as exc:messagebox.showerror('Install unavailable',str(exc),parent=self.root);return
        coverage=Path(self.manifest).parent/'MOD_COVERAGE.json'
        source=coverage.parent/'characterization/AUTO_CHARACTERIZATION.json'
        if source.is_file():
            import mod_coverage
            report=mod_coverage.write(json.loads(source.read_text(encoding='utf-8')),state,coverage.parent)
            partial_weapons=[m['mod'] for m in report['mods'] if m['weaponAssetPack'] and m['state']=='INCOMPLETE' and m['stagedPayloads']]
            if partial_weapons:
                messagebox.showerror('Incomplete weapon installation','This older stage includes only part of these weapon packs:\n'+ '\n'.join(partial_weapons)+'\n\nReview mod completeness, then scan and stage again. Updated staging holds incomplete packs instead of installing only their menus.',parent=self.root)
                return
        incomplete=json.loads(coverage.read_text(encoding='utf-8')).get('incompleteMods',0) if coverage.is_file() else 0
        note=f'{incomplete} selected mods are incomplete. Review MOD_COVERAGE.txt; this batch does not fully install those mods.\n\n' if incomplete else ''
        if not messagebox.askyesno('Install reviewed batch',note+f"Install {len(state.get('finishedRoutes',[]))} replacement edits?\n\n"
                'The original Forge files will be backed up and verified before atomic replacement. Close the game first.',parent=self.root):return
        import existing_stage
        manifest=self.manifest;backup=self.paths['backup_root'].get();game=self.paths['game_root'].get()
        def work():
            summary,transaction=existing_stage.install_existing(manifest,backup,expected_game=game)
            state=json.loads(Path(manifest).read_text(encoding='utf-8'))
            if state.get('writer')=='FORGE_LAYERED_BATCH_V20':
                import recovery_snapshot
                transaction=recovery_snapshot.create_layered(manifest,transaction,backup)
            return summary,transaction
        def done(result):
            summary,transaction=result
            cfg=mm.load(mm.CONFIG,mm.DEFAULT_SETTINGS);cfg['last_transaction']=str(transaction);mm.save(mm.CONFIG,cfg)
            self.status.set('Installation complete - '+str(transaction))
        self.task(work,done)

    def install_proven(self):
        import proven_build
        cfg=mm.load(mm.CONFIG,{})
        manifest=cfg.get('last_proven_manifest')
        if not manifest:
            messagebox.showinfo('Saved setup unavailable','The complete saved setup has not been recorded yet.',parent=self.root);return
        game=self.paths['game_root'].get();backups=self.paths['backup_root'].get()
        self.task(lambda:proven_build.install(manifest,game,backups,self.notify),
                  lambda receipt:self.status.set('Saved complete DX11 setup installed - '+str(receipt)))

    def self_tests(self):
        toolkit=self.paths['toolkit'].get()
        def work():
            result=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-v'],cwd=ROOT,
                capture_output=True,text=True,errors='replace')
            self.notify(result.stdout+result.stderr)
            if result.returncode:raise RuntimeError('Self-tests failed; see the diagnostic log.')
            bridge=subprocess.run(['dotnet',str(ROOT/'bin/DEEF.AnvilBridgeProbe.dll'),'stream-format-tests',toolkit],
                cwd=ROOT,capture_output=True,text=True,errors='replace')
            self.notify(bridge.stdout+bridge.stderr)
            if bridge.returncode:raise RuntimeError('C# format self-tests failed; see the diagnostic log.')
            return result.returncode
        self.task(work,lambda _:self.status.set('Tool self-tests passed - gameplay testing remains separate'))

    def audit_files(self):
        source=filedialog.askopenfilename(title='Choose original PRIVATE Forge',filetypes=[('Forge','*.forge')])
        if not source:return
        staged=filedialog.askopenfilename(title='Choose staged PRIVATE Forge',filetypes=[('Forge','*.forge')])
        if not staged:return
        game=Path(self.paths['game_root'].get()).resolve()
        if any(game in Path(p).resolve().parents for p in (source,staged)):
            messagebox.showerror('Private files required','Choose copies outside the live game folder.');return
        def work():
            import forge_audit, batch_engine
            report=forge_audit.compare_private(source,staged)
            path=mm.WORK/'diagnostics'/'PRIVATE_FORGE_AUDIT.json';batch_engine.record_json(path,report)
            self.notify(f"Added entries: {len(report['added'])}; inherited metadata concerns: {len(report['suspiciousInheritedMetadata'])}\n")
            return path
        self.task(work,lambda path:self.status.set('Private Forge audit saved - '+str(path)))

    def startup_test(self):
        if not messagebox.askyesno('Observe game startup','Launch the game and observe its process for 90 seconds?\n\n'
                'This detects an early exit or crash report. You still need to test menu/save/gameplay. The game may remain open.',parent=self.root):return
        game=self.paths['game_root'].get()
        import startup_check
        self.task(lambda:startup_check.check(game,mm.WORK/'diagnostics/STARTUP_OBSERVATION.json',notify=self.notify),
                  lambda result:self.status.set('Startup observation - '+result['status']))

    def restore(self):
        cfg=mm.load(mm.CONFIG,{})
        tuning=cfg.get('last_weapon_tuning_transaction','')
        last=tuning if tuning and mm.load(Path(tuning),{}).get('status')=='INSTALLED' else cfg.get('last_transaction','')
        path=filedialog.askopenfilename(title='Select INSTALL_TRANSACTION.json',
            initialdir=str(Path(last).parent) if last else str(mm.WORK/'backups'),
            initialfile=Path(last).name if last else '',filetypes=[('Transaction','*.json')])
        if not path:return
        if not messagebox.askyesno('Restore backup','Restore the saved Forge versions from this transaction?\nThe game must be closed.',parent=self.root):return
        import batch_engine
        self.task(lambda:batch_engine.restore_install(path),lambda _:self.status.set('Backup restored'))

    def restore_config(self):
        last=mm.load(mm.CONFIG,{}).get('last_config_transaction','')
        path=filedialog.askopenfilename(title='Select CONFIG_TRANSACTION.json',initialdir=str(Path(last).parent) if last else str(mm.WORK/'backups'),initialfile='CONFIG_TRANSACTION.json',filetypes=[('Config transaction','*.json')],parent=self.root)
        if not path:return
        import config_install
        self.task(lambda:config_install.restore_files(path),lambda _:self.status.set('Config installation restored'))

    def experiments(self):
        try:policy.require_ready(self.paths['game_root'].get())
        except Exception as exc:messagebox.showinfo('Baseline needed',str(exc));return
        audit=filedialog.askopenfilename(initialdir=mm.WORK,title='Select fresh NEW_RESOURCE_AUDIT.json',filetypes=[('Audit JSON','*.json')])
        if not audit:return
        if mm.WORK.resolve() not in Path(audit).resolve().parents:
            messagebox.showerror('Fresh audit required','Choose an audit created in this installer workspace.');return
        import auto_additions
        game=self.paths['game_root'].get();mods=self.paths['archive_dir'].get();kit=self.paths['toolkit'].get()
        self.task(lambda:auto_additions.stage(audit,game,mods,kit,base_manifest=self.manifest,notify=self.notify),
            lambda result:self.status.set('Private new-asset experiment saved - '+result[0]))

    @staticmethod
    def open_folder(path):
        path=Path(path);path.mkdir(parents=True,exist_ok=True);os.startfile(str(path))

    def close(self):
        if self.busy:messagebox.showinfo('Operation active','Wait for the current operation to finish.',parent=self.root);return
        self.save_paths();self.root.destroy()


def show_gui(smoke_test=False):
    root=tk.Tk();app=App(root)
    if smoke_test:
        for page in ('setup','library','options','review','diagnostics'):
            app.navigate(page);root.update_idletasks();root.update()
        root.update_idletasks()
        assert len(app.pages)==5 and root.winfo_width()>=1040
        print(json.dumps({'gui':'PASS','pages':list(app.pages),'window':[root.winfo_width(),root.winfo_height()],
                          'installationPaused':policy.recovery_state().get('installationPaused',True)}))
        root.destroy();return
    root.mainloop()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--smoke-test',action='store_true')
    show_gui(parser.parse_args().smoke_test)
