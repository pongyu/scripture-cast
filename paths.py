"""Where per-user app data (settings, service plan, crash log, cached icons) lives,
following each OS's convention rather than hardcoding the Windows path everywhere.
"""
import os
import sys
from pathlib import Path


def app_data_dir() -> Path:
    if sys.platform == 'win32':
        base = os.environ.get('APPDATA')
        return Path(base) if base else Path.home() / 'AppData' / 'Roaming'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support'
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
