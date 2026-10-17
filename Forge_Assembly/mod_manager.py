# DEEF Direct Anvil v0.17, mapped batch staging with opt-in deployment and rollback.
# Python 3.8+ standard library. Game directories are read-only by design.
import storage_guard
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from toolkit_config import DEFAULT_TOOLKIT

ROOT = Path(__file__).resolve().parent
WORK = ROOT / 'workspace'
STATE = WORK / 'mods.json'
CONFIG = WORK / 'settings.json'
MOD_EXT = {'.zip', '.7z', '.rar'}
ARCHIVE_EXT = {'.zip', '.7z', '.rar'}
DEFAULT_SETTINGS = {
    'workspace_limit_gib': 200,
    'toolkit': DEFAULT_TOOLKIT, 'forge': '', 'archive_dir': str(ROOT.parent / 'DEEF_Mods'),
    'camera_clearance_m': '0.22', 'camera_x_m': '0.0',
    'camera_y_m': '0.0', 'camera_z_m': '0.0', 'game_root': '', 'backup_root': str(WORK / 'backups'),
    'hip_fov_deg': '', 'ads_fov_deg': '', 'body_follow_strength': '',
    'ready_weapon_x_m': '', 'ready_weapon_y_m': '', 'ready_weapon_z_m': '',
    'ads_weapon_x_m': '', 'ads_weapon_y_m': '', 'ads_weapon_z_m': '',
    'camera_pitch_deg': '', 'camera_yaw_deg': '', 'weapon_sway': '', 'camera_bob': '',
    'native_renderer_config': '', 'native_camera_config': '', 'native_input_config': '',
    'first_person_variant': 'Easy First Person v3 + Native First Person 0.2.10',
    'ready_stance_requested': False,
    'config_application': 'PREVIEW_ONLY_NOT_APPLIED_TO_GAME',
    'complete_content_pipeline': True,
    'weapon_damage_priority': 'Damage Balance', 'wabo_difficulty': '4_Realistic (Hardcore)',
    'thermal_priority': 'Standard night vision and thermal', 'item_wheel_variant': 'Vanilla item wheel',
    'nightmare_caller_priority': 'New Reinforcements', 'preserve_game_sensitivity': True,
    'foliage_profile': 'Vanilla concealment', 'npc_trigger_priority': 'Vanilla automatic fire',
    'preserve_player_automatic_fire': True, 'preserve_night_vision': True,
    'shotgun_damage_profile': '150', 'lmg_accuracy_profile': 'Precision',
    'ammo_capacity_profile': 'Vanilla', 'suppressor_profile': 'Normal', 'bcm_handguard': 'Black'
}

def mkdir():
    WORK.mkdir(parents=True, exist_ok=True)

def load(path, default):
    path=Path(path)
    if path.resolve()==CONFIG.resolve() and isinstance(default,dict):
        # A fresh checkout and an older partial settings file must use the same
        # build process; local, ignored workspace settings are not prerequisites.
        default=dict(DEFAULT_SETTINGS,**default)
    if not path.exists():
        return default.copy() if isinstance(default, dict) else list(default)
    with path.open('r', encoding='utf-8') as f:
        value=json.load(f)
    if path.resolve()==CONFIG.resolve() and isinstance(default,dict) and isinstance(value,dict):
        return dict(default,**value)
    return value

def save(path, value):
    mkdir()
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(value, f, indent=2, sort_keys=True)
    os.replace(str(temp), str(path))

def fingerprint(path):
    h = hashlib.sha256()
    with open(str(path), 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def sevenzip():
    for command in ('7z', '7za', '7zz'):
        exe = shutil.which(command)
        if exe:
            return exe
    candidates = [os.environ.get('SEVENZIP_EXE', ''),
                  r'C:\Program Files\7-Zip\7z.exe',r'C:\Program Files (x86)\7-Zip\7z.exe']
    return next((x for x in candidates if x and Path(x).is_file()), None)

def safe_archive_member(name):
    text = name.replace('\\', '/')
    parts = text.split('/')
    if not text or text.startswith('/') or '\x00' in text or ':' in parts[0] or '..' in parts:
        raise ValueError('Unsafe archive member path: ' + name)
    return text

def archive_inventory(path):
    path = Path(path)
    ext = path.suffix.lower()
    if ext == '.zip':
        with zipfile.ZipFile(str(path)) as z:
            return [info.filename for info in z.infolist() if not info.is_dir()]
    exe = sevenzip()
    if not exe:
        raise RuntimeError('7-Zip CLI not installed; cannot index ' + ext + '. No data changed.')
    p = storage_guard.run([exe, 'l', '-slt', str(path)], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, universal_newlines=True)
    if p.returncode:
        raise RuntimeError('7-Zip list failed: ' + p.stderr[-700:])
    # 7z -slt: an entry begins with Path, followed by Size. Reject archive's own header.
    chunks = p.stdout.split('----------')
    listing = chunks[-1] if len(chunks) > 1 else ''
    entries = []
    # Directories can have Size=0 and names ending in .forge. Read the full
    # record before classifying, including Folder/Attributes from 7-Zip.
    for block in listing.replace('\r\n','\n').split('\n\n'):
        fields=dict(line.split(' = ',1) for line in block.splitlines() if ' = ' in line)
        name=fields.get('Path','')
        if name and 'Size' in fields and fields.get('Folder')!='+' and 'D' not in fields.get('Attributes','').split(' ')[0] and not name.endswith(('/', '\\')):
            entries.append(name)
    return entries

def scan_archives(directory, current=None):
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise FileNotFoundError(str(directory))
    current = current or {}
    result = {}
    for path in sorted(directory.rglob('*')):
        if not path.is_file() or path.suffix.lower() not in MOD_EXT:
            continue
        rel = str(path.relative_to(directory)).replace('\\', '/')
        saved = current.get(rel, {})
        obj = {
            'archive': str(path), 'relativeArchive': rel, 'enabled': bool(saved.get('enabled', True)),
            '_enabled_by_user':bool(saved.get('_enabled_by_user',False)),
            'size': path.stat().st_size, 'mtimeNs': path.stat().st_mtime_ns, 'members': [], 'status': 'UNINDEXED',
            'routes': saved.get('routes', [])
        }
        try:
            members = archive_inventory(path)
            obj['members'] = sorted([safe_archive_member(m) for m in members])
            obj['status'] = 'INDEXED'
        except Exception as e:
            obj['status'] = 'ERROR: ' + str(e)
        result[rel] = obj
    return result

# The version-27 Forge directory stores each payload length in a signed 32-bit
# integer. Whole Forge files themselves may be far larger (including 22+ GiB).
# This bound is PER extracted resource, not a cap on the Forge size.
# ZIP and 7-Zip members are streamed to disk; never load them into RAM.
MAX_RESOURCE_BYTES = (1 << 31) - 1
PAYLOAD_DISK_HEADROOM = 256 * 1024 * 1024


def _copy_bounded(source, destination, expected_size=None, max_bytes=None):
    max_bytes = MAX_RESOURCE_BYTES if max_bytes is None else max_bytes
    count = 0
    while True:
        chunk = source.read(1024 * 1024)
        if not chunk:
            break
        count += len(chunk)
        storage_guard.check()
        if count > max_bytes:
            raise ValueError('Extracted payload exceeds its size limit')
        destination.write(chunk)
    if count == 0:
        raise ValueError('Empty mod payload cannot be installed')
    if expected_size is not None and count != expected_size:
        raise IOError('Extracted ZIP member length did not match archive metadata')
    return count


def _check_payload_space(directory, required):
    free = shutil.disk_usage(str(directory)).free
    if free < required + PAYLOAD_DISK_HEADROOM:
        raise OSError('Insufficient free space for mod payload (need at least {:.2f} GiB; have {:.2f} GiB).'.format(
            (required + PAYLOAD_DISK_HEADROOM) / 1024**3, free / 1024**3))


def extraction_target(archive, member, archive_hash=None):
    safe_archive_member(member)
    # Complete Forge archives are not individual signed-32-bit resource entries.
    limit = 64 * 1024**3 if member.lower().endswith('.forge') else MAX_RESOURCE_BYTES
    archive = Path(archive)
    member_digest = hashlib.sha256((str(archive) + '\n' + (archive_hash if archive_hash is not None else fingerprint(archive)) + '\n' + member).encode('utf-8')).hexdigest()[:16]
    basename = Path(member.replace('\\', '/')).name
    dest = WORK / 'payloads' / member_digest / basename
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest_file = Path(str(dest) + '.sha256')
    # A cache is only accepted when its size and companion digest are valid.
    if dest.is_file() and digest_file.is_file() and 0 < dest.stat().st_size <= limit:
        expected = digest_file.read_text(encoding='ascii').strip().lower()
        if len(expected) == 64 and fingerprint(dest) == expected:
            return dest
    dest.unlink(missing_ok=True)
    digest_file.unlink(missing_ok=True)
    pending = Path(str(dest) + '.part')
    pending.unlink(missing_ok=True)
    try:
        if archive.suffix.lower() == '.zip':
            with zipfile.ZipFile(str(archive)) as z:
                meta = z.getinfo(member)
                if not 0 < meta.file_size <= limit:
                    raise ValueError('ZIP payload must be nonempty and smaller than 2 GiB (Forge signed-32-bit entry limit): ' + member)
                _check_payload_space(dest.parent, meta.file_size)
                with z.open(meta) as source, pending.open('xb') as target:
                    _copy_bounded(source, target, expected_size=meta.file_size, max_bytes=limit)
        else:
            exe = sevenzip()
            if not exe:
                raise RuntimeError('Cannot extract this member without 7-Zip CLI')
            # For 7z/RAR, the member size is not necessarily known in advance.
            # Cap the streaming output, instead of allowing 7z to fill a disk.
            _check_payload_space(dest.parent, min(limit, max(archive.stat().st_size, 1)))
            with pending.open('xb') as target, tempfile.TemporaryFile() as errors:
                proc = storage_guard.Popen([exe, 'e', '-so', str(archive), member],
                                        stdout=subprocess.PIPE, stderr=errors)
                try:
                    _copy_bounded(proc.stdout, target, max_bytes=limit)
                    if proc.wait() != 0:
                        errors.seek(0)
                        raise RuntimeError('7z extraction failed: ' + errors.read()[-1000:].decode('utf-8', errors='replace'))
                except BaseException:
                    proc.kill() if proc.poll() is None else None
                    proc.wait()
                    raise
                finally:
                    proc.stdout.close()
        if not pending.is_file() or not 0 < pending.stat().st_size <= limit:
            raise ValueError('Extracted resource is empty or exceeds the 2 GiB Forge entry limit')
        digest = fingerprint(pending)
        os.replace(str(pending), str(dest))
        digest_tmp = Path(str(digest_file) + '.part')
        try:
            digest_tmp.write_text(digest, encoding='ascii')
            os.replace(str(digest_tmp), str(digest_file))
        finally:
            digest_tmp.unlink(missing_ok=True)
        return dest
    finally:
        pending.unlink(missing_ok=True)
    return dest

def conflicts(mods):
    routes = {}
    for mod, obj in mods.items():
        if not obj.get('enabled', True):
            continue
        for item in obj.get('routes', []):
            key = (str(item.get('forge', '')).lower(), str(item.get('data', '')).lower(),
                   str(item.get('object', '')).lower())
            routes.setdefault(key, []).append(mod)
    return {str(key): names for key, names in routes.items() if len(names) > 1}

def project_path_inside(base, target):
    try:
        return os.path.commonpath((str(Path(base).resolve()), str(Path(target).resolve()))) == str(Path(base).resolve())
    except ValueError:
        return False

def execute_bridge(command, toolkit, forge, data, obj, payload, on_line=None):
    # This can only stage into an isolated *versioned output folder*, never into the game.
    toolkit, forge, payload = map(lambda x: Path(x).resolve(), (toolkit, forge, payload))
    if not (toolkit / 'AnvilToolkit.dll').is_file() or not (toolkit / 'Libs').is_dir():
        raise ValueError('Select the original toolkit folder containing the DLL and Libs')
    if not forge.is_file() or forge.suffix.lower() != '.forge':
        raise ValueError('Select an existing source .forge file')
    if not payload.is_file() or project_path_inside(forge.parent, payload):
        raise ValueError('Replacement payload must exist outside the game directory')
    if any(x in data for x in '/\\') or not data.lower().endswith('.data'):
        raise ValueError('Choose one .data entry by filename')
    if command == 'stage' and (not obj or any(x in obj for x in '/\\')):
        raise ValueError('Choose one exact inner resource name')
    if project_path_inside(forge.parent, WORK) or project_path_inside(toolkit, WORK):
        raise ValueError('Workspace cannot reside inside the game or toolkit')
    dll = ROOT / 'bin' / 'DEEF.AnvilBridgeProbe.dll'
    if not dll.exists():
        build = storage_guard.run(['dotnet', 'build', str(ROOT / 'src' / 'AnvilBridgeProbe.csproj'),
                                '-c', 'Release', '-o', str(ROOT / 'bin'), '--nologo', '-p:AnvilToolkitDirectory='+str(toolkit.resolve())],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if on_line:
            on_line(build.stdout)
        if build.returncode or not dll.exists():
            raise RuntimeError('C# build failed; see output. dotnet 9 SDK is required.')
    token = time.strftime('%Y%m%d_%H%M%S')
    run_root = WORK / 'runs' / (token + '_' + hashlib.sha1(os.urandom(8)).hexdigest()[:6])
    out = run_root / 'StagedOutput'
    run_root.mkdir(parents=True)
    report = run_root / ('STAGE_RESULT.json' if command in ('stage','stage-data') else 'INVENTORY_RESULT.json')
    bridge_command = {'stage':'stream-stage','stage-data':'stream-stage-data','inspect':'stream-inspect'}.get(command,command)
    cmd = ['dotnet', str(dll), bridge_command, str(toolkit), str(forge), str(report)]
    if command == 'stage':
        cmd.extend([data, obj, str(payload), str(out)])
    elif command == 'stage-data':
        cmd.extend([data, str(payload), str(out)])
    elif command == 'inspect':
        cmd.append(data or 'AUTO')
    with open(str(run_root / 'CONSOLE.txt'), 'w', encoding='utf-8') as logfile:
        p = storage_guard.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             universal_newlines=True, bufsize=1)
        for line in p.stdout:
            logfile.write(line)
            if on_line:
                on_line(line)
        code = p.wait()
    if report.is_file():
        report_content = load(report, {})
    else:
        report_content = {'error': 'No JSON report generated'}
    if code or not report_content.get('verified'):
        raise RuntimeError('Bridge trial failed. See ' + str(report))
    return report_content, run_root

def cli():
    import argparse
    parser = argparse.ArgumentParser(description='DEEF Direct Anvil 0.16 - mapping, staging, optional install/rollback')
    sub = parser.add_subparsers(dest='cmd')
    scan = sub.add_parser('scan'); scan.add_argument('directory')
    sub.add_parser('conflicts')
    sub.add_parser('gui')
    args = parser.parse_args()
    if args.cmd == 'scan':
        prior = load(STATE, {})
        mods = scan_archives(args.directory, prior)
        save(STATE, mods)
        prefs = load(CONFIG, DEFAULT_SETTINGS)
        prefs['archive_dir'] = str(Path(args.directory).resolve())
        save(CONFIG, prefs)
        print('Indexed {} archives, {} members (unreadable archives flagged)'.format(
            len(mods), sum(len(x['members']) for x in mods.values())))
    elif args.cmd == 'conflicts':
        print(json.dumps(conflicts(load(STATE, {})), indent=2))
    elif args.cmd in (None, 'gui'):
        start_gui()

def start_gui(parent=None):
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    import threading
    embedded=parent is not None
    app = parent if embedded else tk.Tk()
    if not embedded:
        app.title('DEEF Direct Anvil Installer v0.19 - ADVANCED MANAGER')
        app.geometry('1220x820')
    prefs = load(CONFIG, DEFAULT_SETTINGS)
    prefs['toolkit'] = DEFAULT_TOOLKIT  # Ignore a stale path saved by older versions.
    mods = load(STATE, {})
    vars = {k: tk.StringVar(value=str(prefs.get(k, ''))) for k in (
        'toolkit', 'forge', 'archive_dir', 'camera_clearance_m', 'camera_x_m', 'camera_y_m', 'camera_z_m', 'first_person_variant',
        'game_root', 'backup_root', 'hip_fov_deg', 'ads_fov_deg', 'body_follow_strength',
        'ready_weapon_x_m','ready_weapon_y_m','ready_weapon_z_m',
        'ads_weapon_x_m','ads_weapon_y_m','ads_weapon_z_m',
        'camera_pitch_deg','camera_yaw_deg','weapon_sway','camera_bob',
        'native_renderer_config','native_camera_config','native_input_config')}
    ready = tk.BooleanVar(value=bool(prefs.get('ready_stance_requested', False)))
    selected_mod = tk.StringVar(value='')
    selected_member = tk.StringVar(value='')
    data_selected = tk.StringVar(value='')
    object_selected = tk.StringVar(value='')
    payload_path = tk.StringVar(value='')
    book = ttk.Notebook(app); book.pack(fill='both', expand=True, padx=10, pady=8)
    tab_mod, tab_stage, tab_batch, tab_config, tab_ini, tab_log = [ttk.Frame(book) for _ in range(6)]
    for frame, title in zip((tab_mod, tab_stage, tab_batch, tab_config, tab_ini, tab_log),
                            ('Mod Archives', 'Stage Replacement', 'Batch Install + Restore', 'Configs', 'INI Workspace', 'Output')):
        book.add(frame, text=title)
    logview = tk.Text(tab_log, wrap='word'); logview.pack(fill='both', expand=True)
    def log(s):
        def add():
            logview.insert('end', str(s)); logview.see('end')
        app.after(0, add)
    def persist():
        settings = {key: val.get() for key, val in vars.items()}
        settings['ready_stance_requested'] = bool(ready.get())
        settings['config_application'] = 'PREVIEW_ONLY_NOT_APPLIED_TO_GAME'
        save(CONFIG, settings); save(STATE, mods)
    def labeled(parent, name, var, row, choose=None, readonly=False):
        ttk.Label(parent, text=name).grid(row=row, column=0, sticky='w', padx=6, pady=6)
        entry = ttk.Entry(parent, textvariable=var, width=100,
                          state='readonly' if readonly else 'normal')
        entry.grid(row=row, column=1, sticky='ew', padx=6, pady=6)
        if choose:
            ttk.Button(parent, text='Browse', command=choose).grid(row=row, column=2, padx=6)
    def browse_dir(target):
        location = filedialog.askdirectory()
        if location: target.set(location); persist()
    def browse_file(target, filters=(('All files', '*.*'),)):
        location = filedialog.askopenfilename(filetypes=filters)
        if location: target.set(location); persist()
    labeled(tab_mod, 'Archive directory', vars['archive_dir'], 0, lambda: browse_dir(vars['archive_dir']))
    tab_mod.columnconfigure(1, weight=1)
    bar = ttk.Frame(tab_mod); bar.grid(row=1, column=0, columnspan=3, sticky='w')
    left = ttk.Frame(tab_mod); left.grid(row=2, column=0, columnspan=3, sticky='nsew')
    tab_mod.rowconfigure(2, weight=1)
    left.columnconfigure(0, weight=1); left.columnconfigure(1, weight=2); left.rowconfigure(0, weight=1)
    modlist = tk.Listbox(left); modlist.grid(row=0, column=0, sticky='nsew', padx=4)
    memberslist = tk.Listbox(left); memberslist.grid(row=0, column=1, sticky='nsew', padx=4)
    status_mod = tk.StringVar(value=''); ttk.Label(tab_mod, textvariable=status_mod, wraplength=940).grid(row=3, column=0, columnspan=3, sticky='w')
    keys = []
    def refresh():
        nonlocal keys
        keys = sorted(mods.keys()); modlist.delete(0, 'end')
        for key in keys:
            item = mods[key]
            flag = 'ON ' if item.get('enabled') else 'OFF'
            modlist.insert('end', '{} | {} | {} items'.format(flag, key[:75], len(item.get('members', []))))
        status_mod.set('{} archives indexed; {} conflicting route destinations'.format(len(keys), len(conflicts(mods))))
    def on_mod(event=None):
        chosen = modlist.curselection()
        if not chosen: return
        key = keys[chosen[0]]; selected_mod.set(key)
        memberslist.delete(0, 'end')
        for f in mods[key].get('members', []): memberslist.insert('end', f)
        status_mod.set(mods[key].get('status', ''))
    def on_member(event=None):
        chosen = memberslist.curselection()
        if chosen and selected_mod.get():
            selected_member.set(mods[selected_mod.get()]['members'][chosen[0]])
    modlist.bind('<<ListboxSelect>>', on_mod)
    memberslist.bind('<<ListboxSelect>>', on_member)
    def scan_click():
        try:
            mods.clear(); mods.update(scan_archives(vars['archive_dir'].get(), load(STATE, {})))
            persist(); refresh()
        except Exception as e: messagebox.showerror('Archive scan', str(e))
    def toggle():
        key = selected_mod.get()
        if key:
            mods[key]['enabled'] = not mods[key].get('enabled', True); mods[key]['_enabled_by_user']=True; persist(); refresh()
    ttk.Button(bar, text='Index Archives', command=scan_click).pack(side='left', padx=5)
    ttk.Button(bar, text='Enable / Disable Selected', command=toggle).pack(side='left', padx=5)
    ttk.Button(bar, text='Check Route Conflicts', command=lambda: messagebox.showinfo(
        'Route conflicts', json.dumps(conflicts(mods), indent=2)[:3000] or 'No mapped route conflicts')).pack(side='left', padx=5)
    def import_manifest_click():
        from tkinter import messagebox
        import batch_engine
        path = filedialog.askopenfilename(title='Select explicit DEEF route map',filetypes=[('JSON','*.json')])
        if not path:return
        try:
            updated, count=batch_engine.import_route_manifest(path,mods,vars['game_root'].get())
            if not messagebox.askyesno('Import mapped resources',
                 'Validated {} route entries against indexed archives and game files.\n'
                 'Import them now? Resource contents will be verified later during staging.'.format(count)):
                return
            mods.clear();mods.update(updated);persist();refresh()
            status_mod.set('Imported {} explicitly mapped routes'.format(count))
        except Exception as e:messagebox.showerror('Route import failed',str(e))
    def export_manifest_click():
        routes=[]
        game_root=Path(vars['game_root'].get()).resolve() if vars['game_root'].get() else None
        for name,mod in sorted(mods.items()):
            for r in mod.get('routes',[]):
                forge=r.get('forge','')
                if game_root and forge and project_path_inside(game_root,forge):
                    forge=str(Path(forge).resolve().relative_to(game_root)).replace('\\','/')
                routes.append({'archive':name,'kind':r.get('kind','inner'),'forge':forge,
                    'data':r.get('data',''),'object':r.get('object',''),'member':r.get('member','')})
        dest=WORK/'EXPLICIT_ROUTE_MANIFEST.json'
        save(dest,{'schema':'DEEF.route_manifest.1','routes':routes})
        messagebox.showinfo('Route map exported','Saved editable route manifest to\n'+str(dest))
    ttk.Button(bar,text='Import Explicit Route Map',command=import_manifest_click).pack(side='left',padx=5)
    ttk.Button(bar,text='Export Route Map',command=export_manifest_click).pack(side='left',padx=5)
    refresh()

    tab_stage.columnconfigure(1, weight=1)
    labeled(tab_stage, 'AnvilToolkit directory (fixed)', vars['toolkit'], 0, readonly=True)
    labeled(tab_stage, 'Source Forge (read-only)', vars['forge'], 1, lambda: browse_file(vars['forge'], [('Forge', '*.forge')]))
    ttk.Label(tab_stage, text='Forge .data resource (exact filename, or uniquely matching name without ordinal)').grid(row=2, column=0, sticky='w')
    data_combo = ttk.Combobox(tab_stage, textvariable=data_selected, width=90, state='readonly')
    data_combo.grid(row=2, column=1, sticky='ew', pady=4)
    ttk.Label(tab_stage, text='Nested object (exact extracted filename)').grid(row=3, column=0, sticky='w')
    object_combo = ttk.Combobox(tab_stage, textvariable=object_selected, width=90, state='readonly')
    object_combo.grid(row=3, column=1, sticky='ew', pady=4)
    labeled(tab_stage, 'Replacement payload file', payload_path, 4, lambda: browse_file(payload_path))
    details = tk.StringVar(value='Single-resource staging only. Batch deployment asks for a Yes/No decision and creates backups.')
    ttk.Label(tab_stage, textvariable=details, wraplength=980).grid(row=5, column=0, columnspan=3, sticky='w')
    steps = ttk.Frame(tab_stage); steps.grid(row=6, column=0, columnspan=3, sticky='w', pady=12)
    buttons = []
    def async_worker(fn):
        for btn in buttons: btn.configure(state='disabled')
        def work():
            try: fn()
            except Exception as exc:
                log('ERROR: ' + str(exc) + '\n'); app.after(0, lambda msg=str(exc): messagebox.showerror('Staging', msg))
            finally:
                app.after(0, lambda: [btn.configure(state='normal') for btn in buttons])
        threading.Thread(target=work, daemon=True).start()
    def bridge_inspect(data='AUTO'):
        # Passing a real but unused file to the shared staging wrapper is unnecessary;
        # this path calls the same dotnet code directly.
        exe = ROOT / 'bin' / 'DEEF.AnvilBridgeProbe.dll'
        if not exe.exists():
            b = storage_guard.run(['dotnet', 'build', str(ROOT / 'src' / 'AnvilBridgeProbe.csproj'), '-c', 'Release',
                                '-o', str(ROOT / 'bin'), '--nologo', '-p:AnvilToolkitDirectory='+str(Path(vars['toolkit'].get()).resolve())], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            log(b.stdout)
            if b.returncode: raise RuntimeError('C# build failed')
        tmp = WORK / 'inventory'; tmp.mkdir(parents=True, exist_ok=True)
        output = tmp / 'LAST_INVENTORY.json'
        # Remove any previous report so a failed process cannot look like a new success.
        if output.exists(): output.unlink()
        p = storage_guard.run(['dotnet', str(exe), 'inspect', vars['toolkit'].get(), vars['forge'].get(), str(output), data],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        log('INSPECT SELECTOR: {}\n'.format(data))
        log(p.stdout)
        result = load(output, {})
        if p.returncode or not result.get('verified'):
            problem = result.get('error', 'Inspector returned an error without a report')
            selected = result.get('selectedNestedData', data)
            if result.get('failureKind') == 'NESTED_DATA_DESERIALIZE_FAILED':
                raise RuntimeError('Cannot inspect {} after the extraction stability check. '
                    'No files were staged. Inspection details: {}.\n{}'.format(selected, output, problem))
            raise RuntimeError('Inspection failed for {}. Report: {}\n{}'.format(selected, output, problem))
        return result
    def inspect_forge():
        r = bridge_inspect('AUTO')
        def update():
            entries = r['nestedDataEntries']
            data_combo.configure(values=entries)
            # The original v0.14 test established this as a compatible example.
            preferred = '5_-_SHD-SPE-HOU-SnowTriplanar.data'
            if preferred in entries and not data_selected.get():
                data_selected.set(preferred)
            elif data_selected.get() not in entries:
                data_selected.set('')
            object_selected.set('')
            object_combo.configure(values=())
            details.set('Found {} Forge entries and {} .data resources. Example selected; '
                'not every .data is supported by AnvilToolkit. Do not stage a mod without an exact route.'
                .format(r['forgeCount'], len(entries)))
        app.after(0, update)
    def inspect_data():
        target = data_selected.get()
        if not target: raise ValueError('Choose a .data filename first')
        def clear_prior():
            object_selected.set('')
            object_combo.configure(values=())
        app.after(0, clear_prior)
        r = bridge_inspect(target)
        app.after(0, lambda: (object_combo.configure(values=r['nestedObjects']),
                              details.set('Found {} nested resources in {}.'.format(len(r['nestedObjects']), r['selectedNestedData']))))
    def use_member():
        key, member = selected_mod.get(), selected_member.get()
        if not key or not member: raise ValueError('Select an indexed mod and one archive member on Mod Archives tab')
        if not mods[key]['enabled']: raise ValueError('This mod is disabled')
        path = extraction_target(mods[key]['archive'], member)
        app.after(0, lambda: (payload_path.set(str(path)), details.set('Selected payload: ' + member)))
    def save_route():
        key = selected_mod.get()
        if not key or key not in mods:
            raise ValueError('Select an archive on the Mod Archives tab first')
        if not data_selected.get() or not object_selected.get() or not selected_member.get():
            raise ValueError('Inspect and select both nested targets and one archive member first')
        item = {'forge': vars['forge'].get(), 'data': data_selected.get(),
                'object': object_selected.get(), 'member': selected_member.get(),
                'sourceArchive': mods[key]['archive']}
        if not item['forge']:
            raise ValueError('Choose a source Forge first')
        routes = mods[key].setdefault('routes', [])
        if item not in routes: routes.append(item)
        persist()
        c = conflicts(mods)
        app.after(0, lambda: details.set('Route saved to {} ({} total). {} conflict targets.'.format(
            key, len(routes), len(c))))
    def save_data_route():
        key = selected_mod.get()
        if not key or key not in mods:
            raise ValueError('Select a mod archive first')
        if not data_selected.get() or not selected_member.get():
            raise ValueError('Select an exact .data destination and archive member')
        if not selected_member.get().lower().endswith('.data'):
            raise ValueError('Whole .data replacement requires a .data file from the mod archive')
        item = {'kind':'data','forge':vars['forge'].get(),'data':data_selected.get(),
                'object':'','member':selected_member.get(),'sourceArchive':mods[key]['archive']}
        if not item['forge']:raise ValueError('Choose a source Forge first')
        routes=mods[key].setdefault('routes',[])
        if item not in routes:routes.append(item)
        persist()
        app.after(0,lambda:details.set('Mapped WHOLE .data from {} -> {}; saved for batch install'.format(
            key,item['data'])))

    def staging():
        persist()
        # Do not stage a route with multiple enabled mods mapped to the same target.
        current_target = (vars['forge'].get().lower(), data_selected.get().lower(),
                          object_selected.get().lower())
        for key, names in conflicts(mods).items():
            if str(current_target) == key:
                raise RuntimeError('Conflicting enabled mods target this object: ' + ', '.join(names) +
                    '. Disable one mod before staging.')
        for key, value in (('data', data_selected.get()), ('object', object_selected.get()), ('payload', payload_path.get())):
            if not value: raise ValueError('Missing ' + key + '. Inspect and select an exact mapping.')
        report, folder = execute_bridge('stage', vars['toolkit'].get(), vars['forge'].get(), data_selected.get(),
                                        object_selected.get(), payload_path.get(), on_line=log)
        log('\nVERIFIED STAGED FORGE: ' + report['stagedForge'] + '\n')
        app.after(0, lambda: messagebox.showinfo('Verified staging complete',
            'Verification passed. Game files were not modified.\n\n' + report['stagedForge']))
    def button(name, fn):
        b = ttk.Button(steps, text=name, command=lambda: async_worker(fn)); b.pack(side='left', padx=4)
        buttons.append(b)
    button('Inspect Forge', inspect_forge)
    button('Inspect Selected .data', inspect_data)
    button('Use Selected Archive Member', use_member)
    button('Save INNER Object Route', save_route)
    button('Save WHOLE .data Route', save_data_route)
    button('STAGE ONE REPLACEMENT', staging)
    ttk.Label(tab_stage, text='Individual staging never installs. Batch install requires verified outputs, backups, explicit approval, and a closed game.',
              foreground='red').grid(row=7, column=0, columnspan=3, sticky='w', pady=12)

    # Batch workflow: exact route manifests only; route entries are NOT auto-guessed.
    import batch_engine as batch
    tab_batch.columnconfigure(1,weight=1)
    labeled(tab_batch,'Ghost Recon Breakpoint game root (contains GRB.exe)',vars['game_root'],0,
        lambda:browse_dir(vars['game_root']))
    labeled(tab_batch,'Backup directory (outside game)',vars['backup_root'],1,
        lambda:browse_dir(vars['backup_root']))
    allow_partial = tk.BooleanVar(value=False)
    ttk.Checkbutton(tab_batch,variable=allow_partial,
        text='Allow staging only mapped mods (unmapped enabled mods WILL NOT be installed)').grid(
            row=2,column=0,columnspan=3,sticky='w',padx=6,pady=4)
    batch_status=tk.StringVar(value='No batch staged. Import/index archives, map routes, then Review Plan.')
    ttk.Label(tab_batch,textvariable=batch_status,wraplength=1000).grid(row=3,column=0,columnspan=3,sticky='w',padx=6,pady=8)
    preview=tk.Text(tab_batch,height=22,wrap='word')
    preview.grid(row=5,column=0,columnspan=3,sticky='nsew',padx=6,pady=6)
    tab_batch.rowconfigure(5,weight=1)
    run_buttons=[]
    last_batch_manifest=[None]
    last_install_tx=[None]
    def make_current_plan():
        persist()
        return batch.make_plan(mods,vars['game_root'].get(),vars['toolkit'].get(),allow_partial.get())
    def show_plan():
        try:
            plan=make_current_plan()
            planpath=WORK/'LAST_INSTALL_PLAN.json'
            batch.record_json(planpath,plan)
            lines=['ENABLED MAPPED ROUTES: '+str(len(plan['routes'])),
                'UNMAPPED OR DISABLED MODS: '+str(len(plan['unmappedOrDisabled'])),
                'FILE GROUPS: '+str(len(plan['sourceHashes'])),
                'AUTOMATIC COMPLETE MODLIST: '+('YES' if plan['completeModlist'] else 'NO'),
                '\nISSUES (must be resolved before staging):']
            lines.extend(plan['issues'] or ['None'])
            lines.append('\nROUTES:')
            lines.extend('{} | {} / {} / {}'.format(x['mod'],Path(x['forge']).name,x['data'],x['object'])
                         for x in plan['routes'])
            lines.append('\nUNMAPPED/DISABLED ARCHIVES:')
            lines.extend('{}: {}'.format(x['mod'],x['reason']) for x in plan['unmappedOrDisabled'])
            preview.delete('1.0','end');preview.insert('1.0','\n'.join(lines))
            batch_status.set('Review plan saved to '+str(planpath))
            return plan
        except Exception as exc:
            messagebox.showerror('Plan failed',str(exc))
    def batch_stage(plan):
        if plan['issues']:
            raise RuntimeError('Plan has blocking issues. Open Review Plan for details.\n'+\
                               '\n'.join(plan['issues']))
        destination=WORK/'batches'/(time.strftime('%Y%m%d_%H%M%S')+'_'+os.urandom(3).hex())
        app.after(0,lambda:batch_status.set('Staging {} routes; NO game files are being written...'.format(len(plan['routes']))))
        result=batch.stage_plan(plan,destination,log=log)
        last_batch_manifest[0]=destination/'BATCH_RESULT.json'
        app.after(0,lambda:batch_status.set('VERIFIED: {} replacement routes in {} Forge files. Manifest: {}'.format(
            len(result['finishedRoutes']),len(result['outputs']),last_batch_manifest[0])))
        log('\nSTAGING COMPLETE. Source game files are unchanged.\n')
    def stage_click():
        plan=show_plan()
        if plan is None or plan['issues']:return
        if not messagebox.askyesno('Stage mapped mods',
            'Stage {} resource replacements to a separate workspace? No installed game files will change.'.format(len(plan['routes']))):
            return
        batch_worker(lambda:batch_stage(plan))
    def batch_worker(fn):
        for b in run_buttons:b.configure(state='disabled')
        def work():
            try:fn()
            except Exception as exc:
                log('BATCH ERROR: '+str(exc)+'\n')
                app.after(0,lambda msg=str(exc):messagebox.showerror('Batch Operation',msg))
            finally:app.after(0,lambda:[b.configure(state='normal') for b in run_buttons])
        threading.Thread(target=work,daemon=True).start()
    def install_click():
        manifest=last_batch_manifest[0]
        if not manifest or not Path(manifest).is_file():
            selected=filedialog.askopenfilename(title='Choose VERIFIED BATCH_RESULT.json',
                                                 filetypes=[('JSON','*.json')])
            if not selected:return
            manifest=Path(selected)
        answer=messagebox.askyesno('Install verified batch',
            'This will modify game Forge files AFTER verifying SHA-256 and backing up originals.\n'
            'The game must be closed. Game compatibility is not verified.\n\n'
            'Install this verified batch?',parent=app)
        if not answer:return
        backroot=vars['backup_root'].get()
        def run():
            transaction=batch.install_staged(manifest,backroot)
            last_install_tx[0]=transaction
            app.after(0,lambda:batch_status.set('INSTALLED (unverified in-game): backup manifest '+str(transaction)))
            log('GAME INSTALLATION COMPLETED. Backup: '+str(transaction)+'\n')
        batch_worker(run)
    def restore_click():
        transaction=last_install_tx[0]
        if not transaction or not Path(transaction).is_file():
            selected=filedialog.askopenfilename(title='Choose INSTALL_TRANSACTION.json from backup',
                                                 filetypes=[('JSON','*.json')])
            if not selected:return
            transaction=Path(selected)
        answer=messagebox.askyesno('Restore original Forge files',
            'Restore the backed-up originals (game must be closed).\n'
            'The installer will refuse if game files changed after installation.\n\n'
            'Restore the verified original backups?',parent=app)
        if not answer:return
        def run():
            batch.restore_install(transaction)
            app.after(0,lambda:batch_status.set('Original game Forge files restored from '+str(transaction)))
            log('RESTORE COMPLETED: '+str(transaction)+'\n')
        batch_worker(run)
    def batch_button(label,action):
        b=ttk.Button(buttonbar,text=label,command=action);b.pack(side='left',padx=5)
        run_buttons.append(b)
    buttonbar=ttk.Frame(tab_batch);buttonbar.grid(row=4,column=0,columnspan=3,sticky='w',pady=6)
    batch_button('1. Review Plan',show_plan)
    batch_button('2. STAGE ALL MAPPED MODS',stage_click)
    batch_button('3. INSTALL VERIFIED BATCH',install_click)
    batch_button('4. RESTORE ORIGINALS',restore_click)
    ttk.Label(tab_batch,text='CAUTION: No automatic mapping for full .forge/.data assets. Only explicitly mapped inner objects are supported. '
        'Install requires fresh verification, originals backup, and a closed game.',foreground='red',
        wraplength=1100).grid(row=6,column=0,columnspan=3,sticky='w',padx=6,pady=10)

    # Configs are profile-only until each mod-specific INI schema is confirmed.
    config_canvas=tk.Canvas(tab_config,highlightthickness=0)
    config_scroll=ttk.Scrollbar(tab_config,orient='vertical',command=config_canvas.yview)
    config_inside=ttk.Frame(config_canvas)
    config_inside.bind('<Configure>',lambda e:config_canvas.configure(scrollregion=config_canvas.bbox('all')))
    config_canvas.create_window((0,0),window=config_inside,anchor='nw')
    config_canvas.configure(yscrollcommand=config_scroll.set)
    config_canvas.pack(side='left',fill='both',expand=True)
    config_scroll.pack(side='right',fill='y')
    config_inside.columnconfigure(1,weight=1)
    ttk.Label(config_inside,text='SAVED PROFILE ONLY - load the actual Native First Person INI files in INI Workspace to edit real settings.',
              foreground='red',wraplength=950).grid(row=0,column=0,columnspan=3,sticky='w',pady=12)
    controls=[('First-person variant','first_person_variant'),('Camera clearance (m)','camera_clearance_m'),
      ('Camera X (m)','camera_x_m'),('Camera Y (m)','camera_y_m'),('Camera Z (m)','camera_z_m'),
      ('Hip FOV (degrees)','hip_fov_deg'),('ADS FOV (degrees)','ads_fov_deg'),
      ('Camera pitch (degrees)','camera_pitch_deg'),('Camera yaw (degrees)','camera_yaw_deg'),
      ('Body follow strength','body_follow_strength'),('Weapon sway','weapon_sway'),('Camera bob','camera_bob'),
      ('Ready weapon X offset (m)','ready_weapon_x_m'),('Ready weapon Y offset (m)','ready_weapon_y_m'),
      ('Ready weapon Z offset (m)','ready_weapon_z_m'),('ADS weapon X offset (m)','ads_weapon_x_m'),
      ('ADS weapon Y offset (m)','ads_weapon_y_m'),('ADS weapon Z offset (m)','ads_weapon_z_m')]
    for i,(title,key) in enumerate(controls,1):labeled(config_inside,title,vars[key],i)
    ttk.Checkbutton(config_inside,text='Ready stance requested (profile only; not injected)',variable=ready).grid(
        row=len(controls)+1,column=0,columnspan=2,sticky='w',padx=6,pady=8)
    ttk.Button(config_inside,text='SAVE CONFIG PROFILE',command=lambda:(persist(),
        messagebox.showinfo('Config saved','Profile saved to workspace/settings.json. Game INIs NOT changed.'))).grid(
        row=len(controls)+2,column=0,sticky='w',padx=6,pady=10)

    # Generic safe, user-driven INI editing: actual mod keys are preserved, not guessed.
    tab_ini.rowconfigure(2,weight=1);tab_ini.columnconfigure(0,weight=1)
    ini_source=tk.StringVar(value='');ini_status=tk.StringVar(value='Load an existing mod .ini; export a separate copy under workspace/config_overrides.')
    ttk.Label(tab_ini,text='Native First Person options: Renderer.ini, FOV.ini, ADSFOV.ini, Camera.ini, BodyFollow.ini, '
        'Interactions.ini, Startup.ini, Input.ini. Only load files you own; no game directory writes.',wraplength=950).grid(
        row=0,column=0,sticky='w',padx=8,pady=8)
    ini_buttonbar=ttk.Frame(tab_ini);ini_buttonbar.grid(row=1,column=0,sticky='w')
    ini_text=tk.Text(tab_ini,undo=True,wrap='none');ini_text.grid(row=2,column=0,sticky='nsew',padx=8,pady=8)
    def load_ini_file():
        path=filedialog.askopenfilename(filetypes=[('INI files','*.ini'),('Text files','*.txt')])
        if not path:return
        if Path(path).stat().st_size>2*1024*1024:
            messagebox.showerror('INI too large','INI editor limit is 2 MiB');return
        content=Path(path).read_text(encoding='utf-8-sig')
        ini_source.set(path);ini_text.delete('1.0','end');ini_text.insert('1.0',content)
        ini_status.set('Opened '+path)
    def export_ini():
        original=Path(ini_source.get())
        if not original.is_file():
            messagebox.showerror('No input','Load an INI file before exporting');return
        text=ini_text.get('1.0','end-1c')
        if len(text.encode('utf-8'))>2*1024*1024:
            messagebox.showerror('INI too large','INI editor limit is 2 MiB');return
        destination=WORK/'config_overrides'/original.name
        destination.parent.mkdir(parents=True,exist_ok=True)
        if destination.is_file():
            destination=destination.with_name(destination.stem+'_'+time.strftime('%Y%m%d_%H%M%S')+destination.suffix)
        destination.write_text(text,encoding='utf-8')
        ini_status.set('Exported staged INI: '+str(destination))
        messagebox.showinfo('Exported safely','Staged INI copy saved to:\n'+str(destination)+'\n\nNot applied to the game.')
    ttk.Button(ini_buttonbar,text='Load existing mod .ini',command=load_ini_file).pack(side='left',padx=6)
    ttk.Button(ini_buttonbar,text='Export edited .ini to workspace',command=export_ini).pack(side='left',padx=6)
    ttk.Label(tab_ini,textvariable=ini_status,wraplength=1000).grid(row=3,column=0,sticky='w',padx=8,pady=8)

    if not embedded:
        def close(): persist(); app.destroy()
        app.protocol('WM_DELETE_WINDOW', close)
        app.mainloop()

if __name__ == '__main__':
    cli()
