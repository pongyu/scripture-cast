"""Supplied-word ("italics") ranges for the KJV, extracted from CrossWire's GPL-licensed
OSIS KJV module (crosswire.org/~dmsmith/kjv2011/kjv-osis.zip) — the same source used for
red_letter.py. Marks words the KJV translators added for English readability with no
direct equivalent in the underlying Hebrew/Greek (OSIS <transChange type="added">),
traditionally printed in italics.

Only meaningful for the KJV — other versions/translations have no data here.
"""
import json
from pathlib import Path

DATA_PATH = Path(__file__).parent / 'supplied_words_kjv.json'

_data: dict[str, list[list[int]]] | None = None


def _load() -> dict[str, list[list[int]]]:
    global _data
    if _data is None:
        try:
            _data = json.loads(DATA_PATH.read_text(encoding='utf-8'))
        except (FileNotFoundError, json.JSONDecodeError):
            _data = {}
    return _data


def supplied_ranges(book: str, chapter: int, verse: int, text_length: int) -> list[tuple[int, int]]:
    """Character ranges (start, end) within a verse's text that are translator-supplied
    words, conventionally rendered in italics.

    Ranges are clamped to text_length: the CrossWire source and this app's KJV database
    differ by a character or two on a small number of verses (the same class of edition
    differences documented in red_letter.py), so a raw range could otherwise run past
    the actual text.
    """
    key = f'{book} {chapter}:{verse}'
    ranges = _load().get(key)
    if not ranges:
        return []
    return [(max(0, s), min(text_length, e)) for s, e in ranges if s < text_length]
