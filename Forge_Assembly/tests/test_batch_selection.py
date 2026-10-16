"""Default reviewed-batch selection without game writes or UI dialogs."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import app_v20 as app


class BatchSelectionTests(unittest.TestCase):
 def test_picker_opens_at_saved_batch(self):
  with tempfile.TemporaryDirectory() as d:
   work=Path(d);manifest=work/'auto_runs/latest/BATCH_RESULT.json';manifest.parent.mkdir(parents=True);manifest.write_text('{}')
   cfg=work/'settings.json';cfg.write_text(json.dumps({'last_stage_manifest':str(manifest)}))
   ui=app.App.__new__(app.App);ui.manifest=None
   with patch.object(app.mm,'WORK',work),patch.object(app.mm,'CONFIG',cfg),patch.object(app.filedialog,'askopenfilename',return_value='') as picker:
    ui.select_batch()
   self.assertEqual(picker.call_args.kwargs['initialdir'],manifest.parent)
   self.assertEqual(picker.call_args.kwargs['initialfile'],manifest.name)
 def test_install_uses_saved_batch_without_opening_picker(self):
  with tempfile.TemporaryDirectory() as d:
   work=Path(d);manifest=work/'auto_runs/latest/BATCH_RESULT.json';manifest.parent.mkdir(parents=True);manifest.write_text('{}')
   cfg=work/'settings.json';cfg.write_text(json.dumps({'last_stage_manifest':str(manifest)}))
   ui=app.App.__new__(app.App);ui.manifest=None;ui.root=None;ui.validate_batch=lambda _:{}
   with patch.object(app.mm,'WORK',work),patch.object(app.mm,'CONFIG',cfg),patch.object(ui,'select_batch') as picker,patch.object(app.messagebox,'askyesno',return_value=False):
    ui.install()
   picker.assert_not_called();self.assertEqual(ui.manifest,str(manifest))
 def test_external_saved_path_is_not_automatically_used(self):
  with tempfile.TemporaryDirectory() as d:
   folder=Path(d);work=folder/'workspace';work.mkdir();outside=folder/'outside.json';outside.write_text('{}')
   cfg=work/'settings.json';cfg.write_text(json.dumps({'last_stage_manifest':str(outside)}))
   ui=app.App.__new__(app.App);ui.manifest=None
   with patch.object(app.mm,'WORK',work),patch.object(app.mm,'CONFIG',cfg):self.assertIsNone(ui.saved_batch())


if __name__=='__main__':unittest.main()
