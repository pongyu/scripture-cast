"""Persisted "service plan": an ordered list of passages the operator lines up ahead of
a service, so jumping back to the opening passage (or ahead to a later one) after the
preacher references a different book doesn't require re-searching mid-service."""
import json
from dataclasses import asdict, dataclass
from pathlib import Path

CONFIG_PATH = Path.home() / 'AppData' / 'Roaming' / 'bible-presenter' / 'service.json'


@dataclass
class ServiceItem:
    book: str
    chapter: int
    first_verse: int
    last_verse: int
    # Cached "Genesis 1:1-3" style text, computed once when added, so the list can
    # render without a live Bible lookup (and survives across Bible version switches,
    # which re-resolve the actual verses by book/chapter/verse — see
    # MainWindow._reload_verses_in_current_version).
    label: str


@dataclass
class ServiceList:
    items: list[ServiceItem]

    @classmethod
    def load(cls) -> 'ServiceList':
        try:
            data = json.loads(CONFIG_PATH.read_text())
            items = [ServiceItem(**item) for item in data.get('items', [])]
            return cls(items=items)
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            return cls(items=[])

    def save(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(asdict(self), indent=2))
