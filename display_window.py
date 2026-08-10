"""Frameless fullscreen window that shows verse text on a chosen monitor."""
import html
from dataclasses import dataclass, field

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QScreen
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from bible import Bible, Verse
from config import DisplayConfig
import red_letter


SAMPLE_VERSE_TEXT = 'For God so loved the world, that he gave his only begotten Son...'
SAMPLE_VERSE_REF = 'John 3:16'
SAMPLE_VERSE_NUMBER = 16


def verse_label_style(config: DisplayConfig, height: int) -> tuple[str, str, int]:
    """Compute the (text_label_css, reference_label_css, text_pixel_size) for a given
    config and display height. Shared by the real DisplayWindow and any inline preview
    so they always render identically."""
    text_size = round(height * config.text_size_percent / 100)
    ref_size = round(height * config.ref_size_percent / 100)
    padding = round(height * (0.025 if config.maximize_text else 0.06))
    text_css = (
        f'color: white; font-size: {text_size}px; font-family: Georgia, serif; '
        f'padding: {padding}px; line-height: {round(config.line_spacing_percent)}%;'
    )
    if config.maximize_text:
        # Small corner badge instead of a full centered line, so the reference stays
        # visible without taking a whole row away from the verse text — matters most on
        # a physically small screen (e.g. a 42" TV) where every row of height counts.
        badge_ref_size = max(round(ref_size * 0.7), 12)
        ref_css = (
            f'color: #999999; font-size: {badge_ref_size}px; font-family: Georgia, serif; '
            f'background-color: rgba(0, 0, 0, 160); padding: 4px 10px; border-radius: 4px;'
        )
    else:
        ref_css = (
            f'color: #cccccc; font-size: {ref_size}px; font-family: Georgia, serif; '
            f'padding-bottom: {round(padding * 0.67)}px;'
        )
    return text_css, ref_css, text_size


@dataclass
class _Segment:
    """One verse's text within a page, tagged with whether its number should be shown
    (continuation chunks of a word-split verse repeat the same verse but shouldn't
    repeat the number), plus any words-of-Christ ranges (relative to this segment's
    own text, not the full verse — matters for word-split continuation chunks)."""
    verse: int
    text: str
    show_number: bool
    red_ranges: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class _Chunk:
    """A run of consecutive verses (or, for a single overlong verse, a word-slice of it)
    that together fit on one page at the fixed display font size."""
    book: str
    chapter: int
    first_verse: int
    last_verse: int
    segments: list[_Segment] = field(default_factory=list)
    is_partial: bool = False

    @property
    def text(self) -> str:
        """Plain-text join of all segments, e.g. for the live-view mirror."""
        return ' '.join(s.text for s in self.segments)

    def to_html(self, show_numbers: bool = True) -> str:
        parts = []
        for s in self.segments:
            body = _apply_red_letter_html(s.text, s.red_ranges)
            if show_numbers and s.show_number:
                parts.append(f'<sup>{s.verse}</sup>&nbsp;{body}')
            else:
                parts.append(body)
        return ' '.join(parts)


def _apply_red_letter_html(text: str, ranges: list[tuple[int, int]]) -> str:
    """Escape text and wrap the given character ranges in a red <span>, without
    breaking HTML-escaping across the boundaries of a range."""
    if not ranges:
        return html.escape(text)
    pieces = []
    cursor = 0
    for start, end in sorted(ranges):
        if start > cursor:
            pieces.append(html.escape(text[cursor:start]))
        pieces.append(f'<span style="color:#e03030;">{html.escape(text[start:end])}</span>')
        cursor = end
    if cursor < len(text):
        pieces.append(html.escape(text[cursor:]))
    return ''.join(pieces)


class DisplayWindow(QWidget):
    # Emitted whenever the shown text/reference changes, for any reason (button click,
    # keyboard page/verse navigation, screen switch triggering repagination, or clear).
    # Args: (text, reference) — both '' when cleared.
    content_changed = Signal(str, str)

    # Emitted when the blank-to-black state toggles. Arg: is_blanked.
    blanked_changed = Signal(bool)

    # Emitted when the show-desktop state toggles. Arg: is_showing_desktop.
    desktop_shown_changed = Signal(bool)

    def __init__(self, bible: Bible | None = None):
        super().__init__()
        self.bible = bible
        # Words-of-Christ ("red letter") data only exists for the KJV — main_window.py
        # sets this whenever the active Bible version changes.
        self.is_kjv = True
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet('background-color: black;')
        # Without an explicit focus policy, this widget can never actually become the
        # OS-focused window (its default is NoFocus) — setFocus()/activateWindow() alone
        # don't fix that, so real keypresses would never be delivered to it once it's the
        # active window, and our shortcuts would go dead.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.text_label = QLabel('')
        self.text_label.setWordWrap(True)
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.reference_label = QLabel('', self)
        self.reference_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._layout = QVBoxLayout()
        self._layout.addStretch()
        self._layout.addWidget(self.text_label)
        self._layout.addWidget(self.reference_label)
        self._layout.addStretch()
        self.setLayout(self._layout)

        self._verses: list[Verse] = []
        self._pages: list[_Chunk] = []
        self._page_index = 0
        self._padding = 0
        self._height = 1080
        self._blanked = False
        self._showing_desktop = False
        self._restore_screen: QScreen | None = None
        self.config = DisplayConfig.load()
        self._apply_fonts(height=1080)

    def set_config(self, config: DisplayConfig):
        """Apply a new font/spacing config immediately and re-paginate the current content."""
        self.config = config
        self._apply_fonts(height=self._height)
        self._repaginate_and_show()

    def _apply_fonts(self, height: int):
        """Scale font sizes and padding relative to the target screen's height.

        Keeps verse text a consistent relative size whether shown on a small
        laptop panel or a large/high-res extended monitor or projector, while
        still being user-adjustable via self.config for cases like a large
        monitor viewed from a distance where the auto-scaled size reads too small.
        """
        self._height = height
        self._padding = round(height * (0.025 if self.config.maximize_text else 0.06))
        text_css, ref_css, text_size = verse_label_style(self.config, height)
        self.text_label.setStyleSheet(text_css)
        self.reference_label.setStyleSheet(ref_css)
        # QLabel stylesheets don't update QFont, so QFontMetrics needs its own font object to
        # measure the actually-rendered size for pagination.
        text_font = QFont('Georgia')
        text_font.setPixelSize(text_size)
        self.text_label.setFont(text_font)
        self._apply_reference_layout()

    def _apply_reference_layout(self):
        """Place the reference label either in the normal centered flow (full layout) or
        as a small floating corner badge (maximize_text), reclaiming its row for verse text."""
        if self.config.maximize_text:
            if self._layout.indexOf(self.reference_label) != -1:
                self._layout.removeWidget(self.reference_label)
            self.reference_label.adjustSize()
            margin = round(self._height * 0.02)
            x = self.width() - self.reference_label.width() - margin
            y = self.height() - self.reference_label.height() - margin
            self.reference_label.move(max(x, 0), max(y, 0))
            self.reference_label.raise_()
        else:
            if self._layout.indexOf(self.reference_label) == -1:
                self._layout.insertWidget(self._layout.count() - 1, self.reference_label)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.config.maximize_text:
            self._apply_reference_layout()

    def show_on_screen(self, screen: QScreen):
        self._restore_screen = screen
        self._showing_desktop = False
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        geometry = screen.geometry()
        self._apply_fonts(height=geometry.height())
        self.setGeometry(geometry)
        self.showFullScreen()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self._repaginate_and_show()

    def show_preview(self):
        """Show as a normal resizable window instead of fullscreen-on-a-screen.

        Useful for developing/testing the display output without a second monitor.
        """
        self._restore_screen = None
        self._showing_desktop = False
        self.setWindowFlags(Qt.WindowType.Window)
        self._apply_fonts(height=450)
        self.resize(800, 450)
        self.show()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self._repaginate_and_show()

    def show_verses(self, verses: list[Verse]):
        if not verses:
            self.clear()
            return
        self._verses = verses
        self._pages = self._paginate(verses)
        self._page_index = 0
        self._show_page()

    def _repaginate_and_show(self):
        """Re-split the current verses into pages sized for the (possibly new) window, then show page 1."""
        if self._verses:
            self.show_verses(self._verses)

    def _fits(self, text: str, available_width: int, available_height: int) -> bool:
        metrics = QFontMetrics(self.text_label.font())
        bounds = metrics.boundingRect(0, 0, available_width, 0, Qt.TextFlag.TextWordWrap, text)
        # boundingRect ignores the CSS line-height we apply for readability, so scale the
        # measured height by the configured spacing to keep pagination accurate.
        estimated_height = bounds.height() * self.config.line_spacing_percent / 100
        return estimated_height <= available_height

    def _verse_red_ranges(self, verse: Verse) -> list[tuple[int, int]]:
        if not self.is_kjv or not self.config.red_letter:
            return []
        return red_letter.red_ranges(verse.book, verse.chapter, verse.verse, len(verse.text))

    def _split_verse_by_words(self, verse: Verse, available_width: int, available_height: int) -> list[_Chunk]:
        """Break one overlong verse into word-filled chunks, each fitting on its own page."""
        words = verse.text.split()
        chunks = []
        current_words: list[str] = []
        for word in words:
            candidate = current_words + [word]
            if self._fits(' '.join(candidate), available_width, available_height) or not current_words:
                current_words = candidate
            else:
                chunks.append(' '.join(current_words))
                current_words = [word]
        if current_words:
            chunks.append(' '.join(current_words))

        full_red_ranges = self._verse_red_ranges(verse)
        is_partial = len(chunks) > 1
        segments = []
        offset = 0
        for i, text in enumerate(chunks):
            chunk_start, chunk_end = offset, offset + len(text)
            chunk_ranges = [
                (max(0, s - chunk_start), min(len(text), e - chunk_start))
                for s, e in full_red_ranges
                if s < chunk_end and e > chunk_start
            ]
            segments.append(_Segment(verse.verse, text, show_number=(i == 0), red_ranges=chunk_ranges))
            # +1 accounts for the space consumed by ' '.join() between chunks.
            offset = chunk_end + 1

        return [
            _Chunk(
                verse.book, verse.chapter, verse.verse, verse.verse,
                segments=[segment],
                is_partial=is_partial,
            )
            for segment in segments
        ]

    def _paginate(self, verses: list[Verse]) -> list[_Chunk]:
        """Split verses into pages that fit the label at the fixed display font size.

        Multiple short verses may share a page; a single verse too long for one
        page on its own is split at word boundaries across multiple pages instead
        of shrinking the font.
        """
        # In maximize mode the reference is a floating corner badge, not a layout row,
        # so it doesn't need to be reserved space here.
        reference_row_height = 0 if self.config.maximize_text else round(self._padding * 0.5)
        available_height = max(self.height() - 2 * self._padding - reference_row_height, 100)
        available_width = max(self.width() - 2 * self._padding, 200)

        pages: list[_Chunk] = []
        current: _Chunk | None = None
        for v in verses:
            if not self._fits(v.text, available_width, available_height):
                if current:
                    pages.append(current)
                    current = None
                pages.extend(self._split_verse_by_words(v, available_width, available_height))
                continue
            if current is None:
                current = _Chunk(
                    v.book, v.chapter, v.verse, v.verse,
                    segments=[_Segment(v.verse, v.text, show_number=True, red_ranges=self._verse_red_ranges(v))],
                )
                continue
            candidate_text = f'{current.text} {v.text}'
            if self._fits(candidate_text, available_width, available_height):
                new_segment = _Segment(v.verse, v.text, show_number=True, red_ranges=self._verse_red_ranges(v))
                current = _Chunk(
                    current.book, current.chapter, current.first_verse, v.verse,
                    segments=current.segments + [new_segment],
                )
            else:
                pages.append(current)
                current = _Chunk(
                    v.book, v.chapter, v.verse, v.verse,
                    segments=[_Segment(v.verse, v.text, show_number=True, red_ranges=self._verse_red_ranges(v))],
                )
        if current:
            pages.append(current)
        return pages

    def full_content(self) -> tuple[str, str]:
        """The complete current verse selection's (text, reference), independent of how
        it's paginated for the real screen — e.g. no '(cont.) [1/7]' page-fragment
        markers. Meant for UI that shows the operator what's loaded, like the control
        panel's live-view mirror, where those pagination details aren't meaningful."""
        if not self._verses:
            return '', ''
        text = ' '.join(v.text for v in self._verses)
        book, chapter = self._verses[0].book, self._verses[0].chapter
        first_v, last_v = self._verses[0].verse, self._verses[-1].verse
        ref = f'{book} {chapter}:{first_v}' if first_v == last_v else f'{book} {chapter}:{first_v}-{last_v}'
        return text, ref

    def _show_page(self):
        if not self._pages:
            self.text_label.setText('')
            self.reference_label.setText('')
            self.content_changed.emit('', '')
            return
        page = self._pages[self._page_index]
        if page.first_verse == page.last_verse:
            ref = f'{page.book} {page.chapter}:{page.first_verse}'
        else:
            ref = f'{page.book} {page.chapter}:{page.first_verse}-{page.last_verse}'
        if page.is_partial:
            ref += ' (cont.)'
        if len(self._pages) > 1:
            ref += f'  [{self._page_index + 1}/{len(self._pages)}]'
        # content_changed reports the *full* current selection (not just this page), so
        # the control panel's live-view mirror — which isn't screen-constrained the way
        # the real display is — can show the whole verse rather than a page fragment.
        self.content_changed.emit(*self.full_content())
        if self._blanked:
            return
        needs_rich_text = self.config.show_verse_numbers or any(s.red_ranges for s in page.segments)
        if needs_rich_text:
            self.text_label.setTextFormat(Qt.TextFormat.RichText)
            self.text_label.setText(page.to_html(show_numbers=self.config.show_verse_numbers))
        else:
            self.text_label.setTextFormat(Qt.TextFormat.PlainText)
            self.text_label.setText(page.text)
        self.reference_label.setText(ref)
        if self.config.maximize_text:
            self._apply_reference_layout()

    def set_blanked(self, blanked: bool):
        """Hide the current verse text (solid black) without losing the loaded selection,
        or restore it."""
        if blanked == self._blanked:
            return
        self._blanked = blanked
        if blanked:
            self.text_label.setText('')
            self.reference_label.setText('')
        else:
            self._show_page()
        self.blanked_changed.emit(blanked)

    def toggle_blank(self):
        self.set_blanked(not self._blanked)

    @property
    def is_blanked(self) -> bool:
        return self._blanked

    @property
    def is_showing_desktop(self) -> bool:
        return self._showing_desktop

    def set_showing_desktop(self, showing: bool):
        """Hide the display window entirely (revealing the desktop/whatever is behind it
        on that monitor) without losing the loaded verse, or restore fullscreen display."""
        if showing == self._showing_desktop:
            return
        self._showing_desktop = showing
        if showing:
            self.hide()
        elif self._restore_screen:
            self.show_on_screen(self._restore_screen)
        else:
            self.show()
        self.desktop_shown_changed.emit(showing)

    def toggle_desktop(self):
        self.set_showing_desktop(not self._showing_desktop)

    def next_page(self):
        if self._page_index < len(self._pages) - 1:
            self._page_index += 1
            self._show_page()
        else:
            self._go_to_adjacent_verse(forward=True)

    def previous_page(self):
        if self._page_index > 0:
            self._page_index -= 1
            self._show_page()
        else:
            self._go_to_adjacent_verse(forward=False)

    def _go_to_adjacent_verse(self, forward: bool):
        """At the start/end of the current selection, step to the next/previous verse in
        the Bible itself, crossing chapter and book boundaries as needed."""
        if not self.bible or not self._verses:
            return
        edge_verse = self._verses[-1] if forward else self._verses[0]
        lookup = self.bible.next_verse if forward else self.bible.previous_verse
        adjacent = lookup(edge_verse.book, edge_verse.chapter, edge_verse.verse)
        if adjacent:
            self.show_verses([adjacent])

    def clear(self):
        self._verses = []
        self._pages = []
        self._page_index = 0
        self.text_label.setText('')
        self.reference_label.setText('')
        self.content_changed.emit('', '')

    def keyPressEvent(self, event):
        # Configured action shortcuts (show_display, send_to_display, blank_display, etc.)
        # are handled centrally by an application-wide event filter in main_window.py, so
        # they work regardless of whether this window or the main window has OS focus.
        # Only pagination/escape — not user-configurable — are handled locally here.
        if event.key() == Qt.Key.Key_Escape:
            self.showNormal()
            self.hide()
            return
        if event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_PageDown, Qt.Key.Key_Space):
            self.next_page()
            return
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_PageUp):
            self.previous_page()
            return
        super().keyPressEvent(event)


def available_screens() -> list[QScreen]:
    return QApplication.screens()
