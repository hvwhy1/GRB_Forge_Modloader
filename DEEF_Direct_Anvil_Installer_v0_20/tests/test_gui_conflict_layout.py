"""Private Tk integration: page placement and nonfatal choice/resume/cancel."""
import json,tempfile,time,tkinter as tk,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack
import mod_manager as mm,deployment_policy as policy,batch_engine as be,conflict_flow as flow
from app_v20 import App

def widgets(parent):
 for child in parent.winfo_children():
  yield child;yield from widgets(child)
def texts(parent):
 result=[]
 for w in widgets(parent):
  try:result.append(str(w.cget('text')))
  except tk.TclError:pass
 return result

class GuiConflictTests(unittest.TestCase):
 def run_case(self,cancel=False):
  with tempfile.TemporaryDirectory() as d,ExitStack() as stack:
   folder=Path(d);work=folder/'workspace';work.mkdir();mods=folder/'mods';mods.mkdir();game=folder/'game';game.mkdir();(game/'GRB.exe').write_bytes(b'untouched fixture')
   cfg=work/'settings.json';state=work/'mods.json';mm.save(cfg,dict(game_root=str(game),archive_dir=str(mods),toolkit=str(folder/'toolkit')));mm.save(state,{})
   for obj,name,value in ((mm,'WORK',work),(mm,'CONFIG',cfg),(mm,'STATE',state),(policy,'WORK',work),(policy,'STATE',work/'baseline.json')):stack.enter_context(patch.object(obj,name,value))
   stack.enter_context(patch('tkinter.messagebox.showerror',side_effect=AssertionError('A resolvable conflict must not be an error popup')))
   stack.enter_context(patch('tkinter.messagebox.showinfo',side_effect=AssertionError('Unexpected modal message')))
   root=tk.Tk();root.withdraw()
   try:
    app=App(root)
    if not cancel:
     app.navigate('library');app.navigate('options');app.navigate('review')
     self.assertIn('Install FPV',texts(app.pages['options']))
     self.assertNotIn('Nightmare caller resources',texts(app.pages['options']))
     self.assertIn('Forge Assembly',root.title())
     self.assertNotIn('Conflict priorities',texts(app.pages['setup']))
     self.assertNotIn('Review mod completeness',texts(app.pages['setup']))
     self.assertNotIn('Open reports',texts(app.pages['setup']))
     self.assertIn('Conflict priorities',texts(app.pages['review']))
     self.assertIn('Review mod completeness',texts(app.pages['review']))
     self.assertNotIn('Nightmare caller resources',texts(app.pages['review']))
     self.assertNotIn('Radio/Nightmare caller conflicts:',texts(app.pages['library']))
     self.assertIn('Open reports',texts(app.pages['diagnostics']))
    report=work/'CONFLICT_POLICY_REPORT.json'
    row=dict(mod='Fixture',member='Default/file.data',archiveSha256='a'*64)
    be.record_json(report,dict(groups=[dict(target='datapc.forge||#1:2',state='VERIFIED_BINARY_CONFLICT_GROUP',candidates=[row])]))
    result=[];app.task(lambda:flow.resolve(report,'SAME_ARCHIVE_CONTAINER_VARIANTS_NEED_EXACT_CHOICE',app.request_conflict_choice),lambda value:result.append(value))
    acted=False;deadline=time.monotonic()+8
    while app.busy and time.monotonic()<deadline:
     root.update()
     dialogs=[w for w in root.winfo_children() if w.winfo_class()=='Toplevel']
     if dialogs and not acted:
      dialog=dialogs[0];dialog.withdraw()
      if not cancel:
       combo=next(w for w in widgets(dialog) if w.winfo_class()=='TCombobox' and 'Automatic merge / mod order' in w.cget('values'))
       combo.set(combo.cget('values')[-1])
      label='Cancel staging' if cancel else 'Save choice and continue'
      next(w for w in widgets(dialog) if w.winfo_class()=='TButton' and w.cget('text')==label).invoke();acted=True
     time.sleep(.01)
    self.assertTrue(acted);self.assertFalse(app.busy);self.assertEqual(app.active_page,'review')
    self.assertEqual(result,[] if cancel else [True])
    self.assertEqual((game/'GRB.exe').read_bytes(),b'untouched fixture')
    if cancel:self.assertIn('cancelled',app.status.get().lower())
    else:
     self.assertIn('datapc.forge||#1:2',mm.load(cfg,{})['conflict_priorities']['resource_winners'])
     for widget,previous in app.locked:
      if widget.winfo_exists():self.assertEqual(str(widget.cget('state')),str(previous))
   finally:
    for ident in root.tk.call('after','info'):root.after_cancel(ident)
    root.destroy()
 def test_controls_are_grouped_and_waiting_choice_resumes(self):self.run_case()
 def test_waiting_choice_can_be_cancelled_without_error_popup(self):self.run_case(cancel=True)

 def test_settings_follow_added_removed_and_disabled_archives_and_keep_choices(self):
  with tempfile.TemporaryDirectory() as d,ExitStack() as stack:
   folder=Path(d);work=folder/'workspace';work.mkdir();mods=folder/'mods';mods.mkdir()
   cfg=work/'settings.json';state=work/'mods.json'
   mm.save(cfg,dict(archive_dir=str(mods),thermal_priority='Pathfinder white thermal vision'))
   mm.save(state,{})
   for obj,name,value in ((mm,'WORK',work),(mm,'CONFIG',cfg),(mm,'STATE',state),(policy,'WORK',work),(policy,'STATE',work/'baseline.json')):stack.enter_context(patch.object(obj,name,value))
   stack.enter_context(patch('tkinter.messagebox.showerror',side_effect=AssertionError('Unexpected error popup')))
   root=tk.Tk();root.withdraw()
   try:
    app=App(root);app.navigate('review')
    self.assertNotIn('thermal_priority',app.merge_controls)
    archive=mods/'Nightmare Mode-1102-1.zip'
    with zipfile.ZipFile(archive,'w') as z:
     z.writestr('New Reinforcements/A.data',b'A');z.writestr('Traffic Improvements/A.data',b'B')
    run=work/'auto_runs/old';run.mkdir(parents=True)
    rows=[dict(mod=archive.name,member=m,archiveSha256=be.sha256(archive)) for m in ('New Reinforcements/A.data','Traffic Improvements/A.data')]
    be.record_json(run/'CONFLICT_POLICY_REPORT.json',dict(groups=[dict(target='forge||#1:2',state='VERIFIED_COMPOSED',candidates=rows)]))
    app.navigate('review')
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
     root.update()
     if not app.busy and 'nightmare_caller_priority' in app.merge_controls:break
     time.sleep(.01)
    self.assertIn('nightmare_caller_priority',app.merge_controls)
    self.assertIn('forge||#1:2',app.merge_variant_controls)
    var,labels=app.merge_variant_controls['forge||#1:2'];var.set(list(labels)[-1])
    next(w for w in widgets(app.pages['review']) if w.winfo_class()=='TButton' and w.cget('text')=='Save merge options').invoke()
    saved=mm.load(cfg,{});self.assertEqual(saved['thermal_priority'],'Pathfinder white thermal vision')
    self.assertEqual(saved['conflict_priorities']['resource_winners']['forge||#1:2']['member'],rows[-1]['member'])
    current=mm.load(state,{});current[archive.name]['enabled']=False;mm.save(state,current)
    app.navigate('review');self.assertNotIn('nightmare_caller_priority',app.merge_controls);self.assertFalse(app.merge_variant_controls)
    archive.unlink();app.navigate('review');root.update()
    deadline=time.monotonic()+5
    while app.busy and time.monotonic()<deadline:root.update();time.sleep(.01)
    self.assertFalse(app.merge_controls);self.assertFalse(app.merge_variant_controls)
   finally:
    for ident in root.tk.call('after','info'):root.after_cancel(ident)
    root.destroy()

 def test_worker_error_shows_readable_message_and_records_technical_details(self):
  with tempfile.TemporaryDirectory() as d,ExitStack() as stack:
   work=Path(d);cfg=work/'settings.json';state=work/'mods.json';mm.save(cfg,dict(archive_dir=str(work/'mods')));mm.save(state,{})
   for obj,name,value in ((mm,'WORK',work),(mm,'CONFIG',cfg),(mm,'STATE',state),(policy,'WORK',work),(policy,'STATE',work/'baseline.json')):stack.enter_context(patch.object(obj,name,value))
   popup=stack.enter_context(patch('tkinter.messagebox.showerror'))
   root=tk.Tk();root.withdraw()
   try:
    app=App(root)
    def fail():raise RuntimeError('SAME_ARCHIVE_CONTAINER_VARIANTS_NEED_EXACT_CHOICE')
    app.task(fail);deadline=time.monotonic()+5
    while app.busy and time.monotonic()<deadline:root.update();time.sleep(.01)
    self.assertFalse(app.busy);self.assertEqual(popup.call_args.args[0],'Choose which mod should win')
    self.assertNotIn('Traceback',popup.call_args.args[1]);self.assertNotIn('SAME_ARCHIVE',popup.call_args.args[1])
    report=next((work/'diagnostics/errors').glob('*.json'))
    self.assertIn('Traceback',json.loads(report.read_text())['technicalDetails'])
   finally:
    for ident in root.tk.call('after','info'):root.after_cancel(ident)
    root.destroy()

if __name__=='__main__':unittest.main()
