"""Shared AnvilToolkit path for DEEF installer and private-copy tests."""
from __future__ import annotations
import json,os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / 'data' / 'local_toolkit.json'
DEFAULT_TOOLKIT = (json.loads(CONFIG.read_text(encoding='utf-8')).get('toolkitDirectory','') if CONFIG.is_file() else '') or os.environ.get('ANVIL_TOOLKIT_DIR','')


def require_toolkit(path=None):
    """Fail clearly rather than silently using an incorrect ATK installation."""
    if not (path or DEFAULT_TOOLKIT):
        raise FileNotFoundError('Select the complete AnvilToolkit folder in the GUI, set ANVIL_TOOLKIT_DIR, or configure data/local_toolkit.json.')
    chosen = Path(path or DEFAULT_TOOLKIT).expanduser()
    if not (chosen / 'AnvilToolkit.dll').is_file() or not (chosen / 'Libs').is_dir():
        raise FileNotFoundError(
            'Configured AnvilToolkit 1.3.7 folder is unavailable: {}. '
            'Expected AnvilToolkit.dll and Libs. Confirm the directory exists. '
            'To update the fixed path, edit data/local_toolkit.json.'.format(chosen)
        )
    return chosen
