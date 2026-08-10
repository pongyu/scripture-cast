"""Bible data access: SQLite queries and verse reference parsing."""
import re
import sqlite3
from dataclasses import dataclass


REFERENCE_RE = re.compile(
    r'^\s*(?P<book>[1-3]?\s*[A-Za-z. ]+?)\s+(?P<chapter>\d+)\s*:\s*(?P<ranges>[\d\s,\-end]+)\s*$'
)

# One comma-separated piece, e.g. "16", "16-18", or "16-end".
RANGE_PART_RE = re.compile(r'^\s*(?P<verse>\d+)\s*(?:-\s*(?P<end_verse>\d+|end)\s*)?$')


@dataclass
class Verse:
    book: str
    chapter: int
    verse: int
    text: str


class Bible:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._book_names = [row['name'] for row in self.conn.execute('SELECT name FROM book ORDER BY id')]

    def close(self):
        self.conn.close()

    def book_names(self) -> list[str]:
        return list(self._book_names)

    def chapter_count(self, book: str) -> int:
        row = self.conn.execute(
            'SELECT MAX(chapter) AS n FROM verse JOIN book ON verse.book_id = book.id WHERE book.name = ?',
            (book,)
        ).fetchone()
        return row['n'] or 0

    def verse_count(self, book: str, chapter: int) -> int:
        row = self.conn.execute(
            'SELECT MAX(verse) AS n FROM verse JOIN book ON verse.book_id = book.id '
            'WHERE book.name = ? AND chapter = ?',
            (book, chapter)
        ).fetchone()
        return row['n'] or 0

    def get_verses(self, book: str, chapter: int, start_verse: int, end_verse: int | None = None) -> list[Verse]:
        end_verse = end_verse or start_verse
        rows = self.conn.execute(
            'SELECT book.name AS book, chapter, verse, text FROM verse JOIN book ON verse.book_id = book.id '
            'WHERE book.name = ? AND chapter = ? AND verse BETWEEN ? AND ? ORDER BY verse',
            (book, chapter, start_verse, end_verse)
        ).fetchall()
        return [Verse(r['book'], r['chapter'], r['verse'], r['text']) for r in rows]

    def next_verse(self, book: str, chapter: int, verse: int) -> Verse | None:
        """The verse immediately after this one, rolling over into the next chapter/book as needed."""
        verses = self.get_verses(book, chapter, verse + 1)
        if verses:
            return verses[0]
        chapter_count = self.chapter_count(book)
        if chapter < chapter_count:
            verses = self.get_verses(book, chapter + 1, 1)
            if verses:
                return verses[0]
        next_book = self._book_after(book)
        if next_book:
            verses = self.get_verses(next_book, 1, 1)
            if verses:
                return verses[0]
        return None

    def previous_verse(self, book: str, chapter: int, verse: int) -> Verse | None:
        """The verse immediately before this one, rolling back into the previous chapter/book as needed."""
        if verse > 1:
            verses = self.get_verses(book, chapter, verse - 1)
            if verses:
                return verses[0]
            return None
        if chapter > 1:
            prev_chapter = chapter - 1
            last_verse = self.verse_count(book, prev_chapter)
            if last_verse:
                verses = self.get_verses(book, prev_chapter, last_verse)
                if verses:
                    return verses[0]
        prev_book = self._book_before(book)
        if prev_book:
            last_chapter = self.chapter_count(prev_book)
            last_verse = self.verse_count(prev_book, last_chapter) if last_chapter else 0
            if last_verse:
                verses = self.get_verses(prev_book, last_chapter, last_verse)
                if verses:
                    return verses[0]
        return None

    def _book_after(self, book: str) -> str | None:
        try:
            index = self._book_names.index(book)
        except ValueError:
            return None
        return self._book_names[index + 1] if index + 1 < len(self._book_names) else None

    def _book_before(self, book: str) -> str | None:
        try:
            index = self._book_names.index(book)
        except ValueError:
            return None
        return self._book_names[index - 1] if index > 0 else None

    def find_book(self, partial_name: str) -> str | None:
        """Resolve a partial/abbreviated book name to its canonical name."""
        partial_name = partial_name.strip().lower()
        for name in self._book_names:
            if name.lower() == partial_name:
                return name
        matches = [name for name in self._book_names if name.lower().startswith(partial_name)]
        if len(matches) == 1:
            return matches[0]
        return None

    def parse_reference(self, reference: str) -> list[tuple[str, int, int, int]] | None:
        """Parse a reference into a list of (book, chapter, start_verse, end_verse).

        Supports a single verse ('John 3:16'), a range ('John 3:16-18'), an
        open-ended range to the end of the chapter ('John 3:16-end'), and
        comma-separated combinations of the above ('John 3:16,18-20,25').
        """
        match = REFERENCE_RE.match(reference)
        if not match:
            return None
        book = self.find_book(match.group('book'))
        if not book:
            return None
        chapter = int(match.group('chapter'))

        parts = [p for p in match.group('ranges').split(',') if p.strip()]
        if not parts:
            return None

        result = []
        for part in parts:
            part_match = RANGE_PART_RE.match(part)
            if not part_match:
                return None
            start_verse = int(part_match.group('verse'))
            end_raw = part_match.group('end_verse')
            if end_raw is None:
                end_verse = start_verse
            elif end_raw == 'end':
                end_verse = self.verse_count(book, chapter) or start_verse
            else:
                end_verse = int(end_raw)
            result.append((book, chapter, start_verse, end_verse))
        return result

    def search_text(self, query: str, limit: int = 200) -> list[Verse]:
        """Full-text substring search across verse text.

        A comma splits the query into alternatives (OR'd together); within
        each comma-separated term, whitespace-separated words must all be
        present (AND'd together). E.g. 'love, faith hope' matches verses
        containing "love" OR containing both "faith" and "hope".
        """
        terms = [t.strip() for t in query.split(',') if t.strip()]
        if not terms:
            return []

        clauses = []
        params: list[str] = []
        for term in terms:
            words = term.split()
            if not words:
                continue
            clauses.append('(' + ' AND '.join(['text LIKE ?'] * len(words)) + ')')
            params.extend(f'%{word}%' for word in words)
        if not clauses:
            return []

        sql = (
            'SELECT book.name AS book, chapter, verse, text FROM verse JOIN book ON verse.book_id = book.id '
            'WHERE (' + ' OR '.join(clauses) + ') ORDER BY book.id, chapter, verse LIMIT ?'
        )
        rows = self.conn.execute(sql, (*params, limit)).fetchall()
        return [Verse(r['book'], r['chapter'], r['verse'], r['text']) for r in rows]
