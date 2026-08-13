"""Strong's Exhaustive Concordance (James Strong, 1890; public domain), Greek and
Hebrew dictionaries — matched by English word rather than by a per-word Strong's
number, since the app's Bible text isn't Strong's-tagged. Data: Open Scriptures'
JSON conversion (github.com/openscriptures/strongs), CC-BY-SA; see
strongs_dictionary.json's sibling conversion script for how it was generated from
the two source files (strongs-greek-dictionary.js, strongs-hebrew-dictionary.js).
"""
import json
from pathlib import Path

DATA_PATH = Path(__file__).parent / 'strongs_dictionary.json'

_entries: dict[str, dict] | None = None
_word_index: dict[str, list[str]] | None = None


def _load():
    global _entries, _word_index
    if _entries is None:
        try:
            data = json.loads(DATA_PATH.read_text(encoding='utf-8'))
            _entries = data['entries']
            _word_index = data['word_index']
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            _entries = {}
            _word_index = {}
    return _entries, _word_index


def lookup(word: str) -> list[dict]:
    """Strong's entries whose KJV gloss matches the given English word, sorted
    Greek-then-Hebrew then by number. Each entry has number/lemma/translit/def/kjv_def.
    Returns an empty list if nothing matches."""
    entries, word_index = _load()
    word = word.strip().lower()
    if not word:
        return []
    numbers = word_index.get(word, [])
    results = []
    for number in numbers:
        entry = dict(entries[number])
        entry['number'] = number
        results.append(entry)
    results.sort(key=lambda e: (e['number'][0] != 'G', int(e['number'][1:])))
    return results
