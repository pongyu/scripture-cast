"""Frameless fullscreen window that shows verse text on a chosen monitor."""
import html
from dataclasses import dataclass, field

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QScreen
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from bible import Bible, Verse
from config import DisplayConfig
import red_letter
import supplied_words
import theme as theme_module


SAMPLE_VERSE_BOOK = 'Genesis'
SAMPLE_VERSE_CHAPTER = 1
SAMPLE_VERSE_NUMBER = 2
# Narration (no red-letter) with one supplied word ("was") — unlike a Jesus-speech verse
# such as John 3:16, this lets the base display text color and the supplied-words gray
# both show clearly in the settings preview, instead of the whole line being red.
SAMPLE_VERSE_TEXT = (
    'And the earth was without form, and void; and darkness was upon the face of the '
    'deep. And the Spirit of God moved upon the face of the waters.'
)
SAMPLE_VERSE_REF = f'{SAMPLE_VERSE_BOOK} {SAMPLE_VERSE_CHAPTER}:{SAMPLE_VERSE_NUMBER}'


def verse_label_style(config: DisplayConfig, height: int, theme: 'theme_module.Theme | None' = None) -> tuple[str, str, int]:
    """Compute the (text_label_css, reference_label_css, text_pixel_size) for a given
    config and display height. Shared by the real DisplayWindow and any inline preview
    so they always render identically."""
    theme = theme or theme_module.instance
    # Floors guard against a transient height of 0 (e.g. before the widget's first
    # layout pass) producing a zero/negative font size, which Qt logs a warning for
    # ("QFont::setPointSize: Point size <= 0") when the size is later applied.
    text_size = max(round(height * config.text_size_percent / 100), 1)
    ref_size = max(round(height * config.ref_size_percent / 100), 1)
    padding = round(height * (0.025 if config.maximize_text else 0.06))
    text_css = (
        f'color: {theme.display_text}; font-size: {text_size}px; font-family: Georgia, serif; '
        f'padding: {padding}px; line-height: {round(config.line_spacing_percent)}%;'
    )
    if config.maximize_text:
        # Small corner badge instead of a full centered line, so the reference stays
        # visible without taking a whole row away from the verse text — matters most on
        # a physically small screen (e.g. a 42" TV) where every row of height counts.
        badge_ref_size = max(round(ref_size * 0.7), 12)
        ref_css = (
            f'color: {theme.display_text_muted}; font-size: {badge_ref_size}px; font-family: Georgia, serif; '
            f'padding: 4px 10px;'
        )
    else:
        ref_css = (
            f'color: {theme.display_text_muted}; font-size: {ref_size}px; font-family: Georgia, serif; '
            f'padding-bottom: {round(padding * 0.67)}px;'
        )
    return text_css, ref_css, text_size


@dataclass
class _Segment:
    """One verse's text within a page, tagged with whether its number should be shown
    (continuation chunks of a word-split verse repeat the same verse but shouldn't
    repeat the number), plus any words-of-Christ and supplied-word ranges (both
    relative to this segment's own text, not the full verse — matters for word-split
    continuation chunks)."""
    verse: int
    text: str
    show_number: bool
    red_ranges: list[tuple[int, int]] = field(default_factory=list)
    italic_ranges: list[tuple[int, int]] = field(default_factory=list)


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
            body = _apply_verse_html(s.text, s.red_ranges, s.italic_ranges)
            if show_numbers and s.show_number:
                parts.append(f'<sup>{s.verse}</sup>&nbsp;{body}')
            else:
                parts.append(body)
        return ' '.join(parts)


def _remap_ranges_to_chunk(ranges: list[tuple[int, int]], chunk_start: int, chunk_end: int) -> list[tuple[int, int]]:
    """Re-express full-verse character ranges relative to one word-split chunk's own
    text, keeping only the portion that actually falls within [chunk_start, chunk_end)."""
    chunk_len = chunk_end - chunk_start
    return [
        (max(0, s - chunk_start), min(chunk_len, e - chunk_start))
        for s, e in ranges
        if s < chunk_end and e > chunk_start
    ]


def _apply_verse_html(
    text: str,
    red_ranges: list[tuple[int, int]],
    italic_ranges: list[tuple[int, int]],
    highlight_ranges: list[tuple[int, int]] = (),
    theme: 'theme_module.Theme | None' = None,
) -> str:
    """Escape text and wrap it in red-letter <span>/italic <i>/search-highlight <span>
    markup as needed, without breaking HTML-escaping across a range boundary. Splits
    the text into same-styled runs at every range boundary first, so a character
    covered by more than one range (e.g. a translator-added word inside Jesus' speech)
    gets all its styles applied together rather than producing overlapping/nested tags."""
    if not red_ranges and not italic_ranges and not highlight_ranges:
        return html.escape(text)
    theme = theme or theme_module.instance

    boundaries = {0, len(text)}
    for ranges in (red_ranges, italic_ranges, highlight_ranges):
        for s, e in ranges:
            boundaries.add(s)
            boundaries.add(e)
    cuts = sorted(boundaries)

    def covers(ranges, pos: int) -> bool:
        return any(s <= pos < e for s, e in ranges)

    pieces = []
    for start, end in zip(cuts, cuts[1:]):
        run = text[start:end]
        if not run:
            continue
        escaped = html.escape(run)
        is_italic = covers(italic_ranges, start)
        is_red = covers(red_ranges, start)
        if is_italic:
            # Gray wins over red: a supplied word always reads as gray-italic, even
            # inside Jesus' red speech, rather than being colored red like the rest
            # of the surrounding red-letter run. The gray itself is derived from the
            # display's own text/background pair so it stays legible on any theme.
            escaped = f'<span style="font-style:italic;color:{theme.display_supplied_words};">{escaped}</span>'
        elif is_red:
            escaped = f'<span style="color:#e03030;">{escaped}</span>'
        if covers(highlight_ranges, start):
            escaped = f'<span style="background-color:#fff2a8;">{escaped}</span>'
        pieces.append(escaped)
    return ''.join(pieces)


class DisplayWindow(QWidget):
    # Emitted whenever the shown text/reference changes, for any reason (button click,
    # keyboard page/verse navigation, screen switch triggering repagination, or clear).
    # Args: (text, reference) — both '' when cleared.
    content_changed = Signal(str, str)

    # Emitted when the show-desktop state toggles. Arg: is_showing_desktop.
    desktop_shown_changed = Signal(bool)

    # Emitted whenever the current page or total page count changes (page navigation,
    # new selection, or clear). Args: (page_index_1_based, total_pages) — (0, 0) when
    # there's nothing loaded, so the control panel can show/enable page nav accordingly.
    page_changed = Signal(int, int)

    def __init__(self, bible: Bible | None = None):
        super().__init__()
        self.bible = bible
        # Words-of-Christ ("red letter") data only exists for the KJV — main_window.py
        # sets this whenever the active Bible version changes.
        self.is_kjv = True
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
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
        self._width = 1920
        self._showing_desktop = False
        self._restore_screen: QScreen | None = None
        self.config = DisplayConfig.load()
        self._apply_background()
        self._apply_fonts(height=1080)
        theme_module.instance.changed.connect(self._on_theme_changed)

    def _on_theme_changed(self):
        """Live-updates the display's background/text colors when the user picks new
        ones in Display Settings, without needing to reselect the verse."""
        self._apply_background()
        self._apply_fonts(height=self._height)
        self._show_page()

    def _apply_background(self):
        self.setStyleSheet(f'background-color: {theme_module.instance.display_bg};')

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
        # Keep the tracked target size in sync with real resizes (e.g. dragging the
        # preview window) — _paginate() reads these instead of width()/height() directly
        # because right after show_on_screen()/showFullScreen(), the OS hasn't always
        # applied the new geometry yet by the time pagination runs, which previously
        # caused the first page shown after switching to the display to be paginated
        # against the old (often much smaller) window size.
        self._width = self.width()
        self._height = self.height()
        if self.config.maximize_text:
            self._apply_reference_layout()

    def show_on_screen(self, screen: QScreen):
        self._restore_screen = screen
        self._showing_desktop = False
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        geometry = screen.geometry()
        self._width = geometry.width()
        self._apply_fonts(height=geometry.height())
        self.setGeometry(geometry)
        self.showFullScreen()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self._repaginate_and_show()

    def resize_for_thumbnail(self, width: int, height: int):
        """Lays this window out at the given resolution without ever showing it on a real
        screen — for a hidden instance dedicated to rendering an accurate small preview
        of what the real display would look like at that resolution (see sync_from())."""
        self._width = width
        self._apply_fonts(height=height)
        self.resize(width, height)
        self._repaginate_and_show()

    def show_preview(self):
        """Show as a normal resizable window instead of fullscreen-on-a-screen.

        Useful for developing/testing the display output without a second monitor.
        """
        self._restore_screen = None
        self._showing_desktop = False
        self.setWindowFlags(Qt.WindowType.Window)
        self._width = 800
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

    def sync_from(self, other: 'DisplayWindow'):
        """Matches this window's shown content and page position to another DisplayWindow's
        current state — e.g. a hidden preview instance catching up to the real display
        after page navigation, which happens entirely inside next_page()/previous_page()
        and so isn't otherwise visible to whatever's driving the preview."""
        self.bible = other.bible
        self.is_kjv = other.is_kjv
        self._verses = other._verses
        self._pages = self._paginate(self._verses) if self._verses else []
        self._page_index = min(other._page_index, max(len(self._pages) - 1, 0))
        self._show_page()

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

    def _verse_italic_ranges(self, verse: Verse) -> list[tuple[int, int]]:
        if not self.is_kjv or not self.config.supplied_words_italic:
            return []
        return supplied_words.supplied_ranges(verse.book, verse.chapter, verse.verse, len(verse.text))

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
        full_italic_ranges = self._verse_italic_ranges(verse)
        is_partial = len(chunks) > 1
        segments = []
        offset = 0
        for i, text in enumerate(chunks):
            chunk_start, chunk_end = offset, offset + len(text)
            segments.append(_Segment(
                verse.verse, text, show_number=(i == 0),
                red_ranges=_remap_ranges_to_chunk(full_red_ranges, chunk_start, chunk_end),
                italic_ranges=_remap_ranges_to_chunk(full_italic_ranges, chunk_start, chunk_end),
            ))
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
        available_height = max(self._height - 2 * self._padding - reference_row_height, 100)
        available_width = max(self._width - 2 * self._padding, 200)

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
                    segments=[_Segment(
                        v.verse, v.text, show_number=True,
                        red_ranges=self._verse_red_ranges(v), italic_ranges=self._verse_italic_ranges(v),
                    )],
                )
                continue
            candidate_text = f'{current.text} {v.text}'
            if self._fits(candidate_text, available_width, available_height):
                new_segment = _Segment(
                    v.verse, v.text, show_number=True,
                    red_ranges=self._verse_red_ranges(v), italic_ranges=self._verse_italic_ranges(v),
                )
                current = _Chunk(
                    current.book, current.chapter, current.first_verse, v.verse,
                    segments=current.segments + [new_segment],
                )
            else:
                pages.append(current)
                current = _Chunk(
                    v.book, v.chapter, v.verse, v.verse,
                    segments=[_Segment(
                        v.verse, v.text, show_number=True,
                        red_ranges=self._verse_red_ranges(v), italic_ranges=self._verse_italic_ranges(v),
                    )],
                )
        if current:
            pages.append(current)
        return pages

    def _show_page(self):
        if not self._pages:
            self.text_label.setText('')
            self.reference_label.setText('')
            self.content_changed.emit('', '')
            self.page_changed.emit(0, 0)
            return
        self.page_changed.emit(self._page_index + 1, len(self._pages))
        page = self._pages[self._page_index]
        if page.first_verse == page.last_verse:
            ref = f'{page.book} {page.chapter}:{page.first_verse}'
        else:
            ref = f'{page.book} {page.chapter}:{page.first_verse}-{page.last_verse}'
        if page.is_partial:
            ref += ' (cont.)'
        if len(self._pages) > 1:
            ref += f'  [{self._page_index + 1}/{len(self._pages)}]'
        # content_changed reports exactly what's on this page (not the whole selection),
        # so the control panel's live-view mirror shows the same text/reference — including
        # any "(cont.) [x/y]" marker — as the real display, instead of drifting out of sync
        # once a long verse or multi-verse selection spans more than one page.
        self.content_changed.emit(page.text, ref)
        needs_rich_text = self.config.show_verse_numbers or any(s.red_ranges or s.italic_ranges for s in page.segments)
        if needs_rich_text:
            self.text_label.setTextFormat(Qt.TextFormat.RichText)
            self.text_label.setText(page.to_html(show_numbers=self.config.show_verse_numbers))
        else:
            self.text_label.setTextFormat(Qt.TextFormat.PlainText)
            self.text_label.setText(page.text)
        self.reference_label.setText(ref)
        if self.config.maximize_text:
            self._apply_reference_layout()

    @property
    def is_showing_desktop(self) -> bool:
        return self._showing_desktop

    @property
    def has_content(self) -> bool:
        return bool(self._verses)

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
        # else: the display was never shown on a real screen yet (no verse sent), so
        # there's nothing to restore — showing a bare, unpositioned window here used to
        # paint a stray undecorated black box over the control panel.
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

    def can_go_next(self) -> bool:
        """Whether next_page() would do anything: either there's another page of the
        current (word-split) selection left, or there's a next verse in the Bible to
        advance to. False only at the very last verse of the Bible (Revelation 22:21)."""
        if self._page_index < len(self._pages) - 1:
            return True
        return self._can_go_to_adjacent_verse(forward=True)

    def can_go_previous(self) -> bool:
        """Whether previous_page() would do anything — see can_go_next(). False only at
        the very first verse of the Bible (Genesis 1:1)."""
        if self._page_index > 0:
            return True
        return self._can_go_to_adjacent_verse(forward=False)

    def _can_go_to_adjacent_verse(self, forward: bool) -> bool:
        if not self.bible or not self._verses:
            return False
        edge_verse = self._verses[-1] if forward else self._verses[0]
        lookup = self.bible.next_verse if forward else self.bible.previous_verse
        return lookup(edge_verse.book, edge_verse.chapter, edge_verse.verse) is not None

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
        self.page_changed.emit(0, 0)

    def keyPressEvent(self, event):
        # Configured action shortcuts (show_display, send_to_display, show_desktop, etc.)
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
