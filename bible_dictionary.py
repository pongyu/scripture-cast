"""Easton's Bible Dictionary (1897, public domain) — word/name/place lookups for
selected text in the results list. Source text from the Christian Classics Ethereal
Library's ThML edition (ccel.org/ccel/easton/ebd2.html), converted to a flat
{headword: definition} JSON file; see the parsing notes in easton_dictionary.json's
sibling conversion script for how it was generated.
"""
import json
from pathlib import Path

DATA_PATH = Path(__file__).parent / 'easton_dictionary.json'

_data: dict[str, str] | None = None


def _load() -> dict[str, str]:
    global _data
    if _data is None:
        try:
            _data = json.loads(DATA_PATH.read_text(encoding='utf-8'))
        except (FileNotFoundError, json.JSONDecodeError):
            _data = {}
    return _data


def lookup(word: str) -> str | None:
    """Easton's definition for a headword, or None if it isn't in the dictionary.
    Case-sensitive-first (most headwords are proper nouns), falling back to a
    title-cased match so a lowercase mid-sentence word (e.g. from a text selection
    that doesn't include a capital) still resolves."""
    data = _load()
    word = word.strip()
    if not word:
        return None
    if word in data:
        return data[word]
    titled = word[:1].upper() + word[1:]
    return data.get(titled)
