"""Per-church branding: the name shown in the control panel's identity strip and an
optional custom logo, so the app isn't hardcoded to one church. Persisted like
theme.ThemeConfig/config.DisplayConfig; live-updatable via the `changed` signal so the
control panel can pick up a new name/logo without restarting.
"""
import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

APP_DATA_DIR = Path.home() / 'AppData' / 'Roaming' / 'bible-presenter'
CONFIG_PATH = APP_DATA_DIR / 'identity_config.json'
LOGO_STORAGE_PATH = APP_DATA_DIR / 'logo.png'

DEFAULT_CHURCH_NAME = 'Valenzuela City Baptist Church'


@dataclass
class IdentityConfig:
    church_name: str = DEFAULT_CHURCH_NAME
    # Empty string means "use the bundled default logo" (resources/vcbc logo.png).
    logo_path: str = ''

    @classmethod
    def load(cls) -> 'IdentityConfig':
        try:
            data = json.loads(CONFIG_PATH.read_text())
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            return cls()

    def save(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(asdict(self), indent=2))


def store_logo(source_path: Path) -> str:
    """Copies a user-picked logo image into app data so it persists independently of
    where the original file lives (survives it being moved/deleted, e.g. a USB drive or
    Downloads folder), and returns the path to use as IdentityConfig.logo_path."""
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    destination = LOGO_STORAGE_PATH.with_suffix(source_path.suffix.lower() or '.png')
    shutil.copyfile(source_path, destination)
    return str(destination)


class Identity(QObject):
    """Live holder for the active IdentityConfig, mirroring theme.Theme's shape:
    widgets read config off `identity.instance.config`; apply() swaps it and emits
    `changed` so already-built widgets (the identity strip) can refresh in place."""

    changed = Signal()

    def __init__(self, config: IdentityConfig):
        super().__init__()
        self.config = config

    def apply(self, config: IdentityConfig):
        self.config = config
        self.changed.emit()


instance = Identity(IdentityConfig.load())
