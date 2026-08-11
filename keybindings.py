"""Persisted keyboard shortcuts for the control panel's most common actions."""
import json
from dataclasses import asdict, dataclass
from pathlib import Path

CONFIG_PATH = Path.home() / 'AppData' / 'Roaming' / 'bible-presenter' / 'keybindings.json'

# (action name, default shortcut, human-readable label)
ACTIONS = [
    ('show_display', 'Shift+Return', 'Show Display Window'),
    ('send_to_display', 'Return', 'Send to Display'),
    ('clear_display', 'Ctrl+Backspace', 'Clear Display'),
    ('show_desktop', 'D', 'Show/Hide Desktop'),
    ('switch_version', 'Ctrl+Shift+V', 'Switch Bible Version'),
    ('focus_search', 'Ctrl+F', 'Focus Search Box'),
]


@dataclass
class KeyBindings:
    show_display: str = 'Shift+Return'
    send_to_display: str = 'Return'
    clear_display: str = 'Ctrl+Backspace'
    show_desktop: str = 'D'
    switch_version: str = 'Ctrl+Shift+V'
    focus_search: str = 'Ctrl+F'

    @classmethod
    def load(cls) -> 'KeyBindings':
        try:
            data = json.loads(CONFIG_PATH.read_text())
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            return cls()

    def save(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(asdict(self), indent=2))
