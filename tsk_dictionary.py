"""Treasury of Scripture Knowledge (TSK) style cross-references — for a given verse,
other verses that are thematically or textually related. Data from openbible.info's
cross-reference dataset (CC-BY; TSK-derived, vote-weighted), converted to a flat
{"Book Chapter:Verse": ["Book Chapter:Verse", ...]} JSON keyed the same way
bible_dictionary.py keys by headword; see the parsing notes in tsk_dictionary.json's
sibling conversion script for how it was generated.
"""
import json
from pathlib import Path

DATA_PATH = Path(__file__).parent / 'tsk_dictionary.json'

_data: dict[str, list[str]] | None = None


def _load() -> dict[str, list[str]]:
    global _data
    if _data is None:
        try:
            _data = json.loads(DATA_PATH.read_text(encoding='utf-8'))
        except (FileNotFoundError, json.JSONDecodeError):
            _data = {}
    return _data


def lookup(book: str, chapter: int, verse: int) -> list[str]:
    """Cross-reference strings (e.g. "Romans 5:8") for the given verse, sorted by
    original relevance, or an empty list if this verse has none."""
    data = _load()
    key = f'{book} {chapter}:{verse}'
    return list(data.get(key, []))
