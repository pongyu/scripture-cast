"""One-off fix for systematic data-entry bugs in bibles/King James Version.sqlite:

- Possessive apostrophes with the trailing "s" dropped (e.g. "the LORD'
  house" instead of "the LORD's house"), plus a related bug where an
  already-correct plural possessive apostrophe (e.g. "sons'") was missing
  its following space (e.g. "sons'wives" instead of "sons' wives").
- A stray space before punctuation (e.g. "thou art , and" instead of
  "thou art, and"), found via a bulk diff against an independent
  reference KJV text (a-closer-walk's scripts/check_kjv_against_reference.py)
  after the apostrophe fix below was already in place.

Found while investigating a report of "LORD' house" (missing "S") in
Jeremiah 51:51 in a-closer-walk (a sibling project reusing this database as
a data source). Confirmed present in this database itself, not introduced
downstream: 1,455 missing-'s'-before-space, 55 missing-'s'-before-punctuation,
175 missing-space-after-correct-plural-possessive, and 1,718
space-before-punctuation instances, all fixed here.

Every fix was verified before writing: (1) the words affected by the
missing-'s' patterns are ordinary nouns/names (king, father, LORD, man,
David, children, ...) where 's is unambiguously the correct possessive,
(2) a space directly before punctuation is never grammatically correct in
any context, so that fix carries no ambiguity, and (3) after fixing, zero
instances of any bug pattern remain anywhere in the database.

Run manually, once:
    python scripts/fix_possessive_apostrophes.py
"""

import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "bibles" / "King James Version.sqlite"

# A word ending in "s" followed directly (no space) by an apostrophe and a
# lowercase letter is an already-correct plural possessive missing its
# space (e.g. "sons'wives" -> "sons' wives"). Must run before the two
# patterns below, so their negative lookbehind for a trailing "s" sees an
# already-spaced-out apostrophe rather than a smashed-together one.
_MISSING_SPACE = re.compile(r"\b([A-Za-z]*s)'([a-z])")

# A word NOT ending in "s", followed by a bare apostrophe then a space and a
# lowercase letter, is a possessive with its "s" dropped (e.g. "the LORD'
# house" -> "the LORD's house"). The [^sS] exclusion is what keeps this from
# ever touching an already-correct plural possessive like "sons' wives".
_MISSING_S_BEFORE_SPACE = re.compile(r"\b([A-Za-z]*[^sS])' (?=[a-z])")

# Same bug, but at a clause/sentence boundary instead of before another word
# (e.g. "is the LORD'." -> "is the LORD's.").
_MISSING_S_BEFORE_PUNCT = re.compile(r"\b([A-Za-z]*[^sS])'(?=[,.;:!?])")

# A stray space directly before punctuation (e.g. "thou art , and" ->
# "thou art, and") — never grammatically correct, so unconditional.
_SPACE_BEFORE_PUNCT = re.compile(r"([A-Za-z]) ([,.;:!?])")


def _possessive_s(word: str) -> str:
    # Standard KJV typesetting keeps the possessive "S" capitalized when it
    # follows an all-caps word like LORD (LORD'S, not LORD's) — confirmed
    # against kingjamesbibleonline.org's Jeremiah 51:51, which has no
    # in-corpus example to check against since this bug affected 100% of
    # the LORD-possessive occurrences in this database.
    return "S" if word.isupper() else "s"


def fix_text(text: str) -> str:
    text = _MISSING_SPACE.sub(lambda m: f"{m.group(1)}' {m.group(2)}", text)
    text = _MISSING_S_BEFORE_SPACE.sub(
        lambda m: f"{m.group(1)}'{_possessive_s(m.group(1))} ", text
    )
    text = _MISSING_S_BEFORE_PUNCT.sub(
        lambda m: f"{m.group(1)}'{_possessive_s(m.group(1))}", text
    )
    text = _SPACE_BEFORE_PUNCT.sub(r"\1\2", text)
    return text


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, text FROM verse")
    rows = cur.fetchall()

    updates = [(fixed, vid) for vid, text in rows if (fixed := fix_text(text)) != text]
    print(f"{len(updates)} of {len(rows)} verses need fixing")

    cur.executemany("UPDATE verse SET text = ? WHERE id = ?", updates)
    conn.commit()

    # Verify: zero remaining matches of any bug pattern.
    cur.execute("SELECT text FROM verse")
    remaining = 0
    for (text,) in cur.fetchall():
        remaining += len(_MISSING_SPACE.findall(text))
        remaining += len(_MISSING_S_BEFORE_SPACE.findall(text))
        remaining += len(_MISSING_S_BEFORE_PUNCT.findall(text))
        remaining += len(_SPACE_BEFORE_PUNCT.findall(text))
    print(f"remaining matches after fix: {remaining}")
    assert remaining == 0, "fix left some instances unresolved — investigate before trusting this run"

    conn.close()
    print(f"wrote {DB_PATH}")


if __name__ == "__main__":
    main()
