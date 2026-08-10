"""Persisted display settings: font sizes and line spacing, as a percentage of screen height."""
import json
from dataclasses import asdict, dataclass
from pathlib import Path

CONFIG_PATH = Path.home() / 'AppData' / 'Roaming' / 'bible-presenter' / 'config.json'


@dataclass
class DisplayConfig:
    text_size_percent: float = 6.0
    ref_size_percent: float = 3.0
    line_spacing_percent: float = 130.0  # relative to normal line height, e.g. 130 = 1.3x
    maximize_text: bool = False  # when True, reference moves to a small corner badge
    show_verse_numbers: bool = True  # small superscript number before each verse's text
    red_letter: bool = True  # words of Christ in red — only has effect while KJV is active
    supplied_words_italic: bool = True  # translator-added words in italics — KJV only

    @classmethod
    def load(cls) -> 'DisplayConfig':
        try:
            data = json.loads(CONFIG_PATH.read_text())
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            return cls()

    def save(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(asdict(self), indent=2))
