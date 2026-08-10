"""Control panel: browse or search verses, send selection to the display window."""
import html
import re
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt
from PySide6.QtGui import QFont, QFontMetrics, QKeySequence, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

import red_letter
import supplied_words
import theme
from bible import Bible, Verse
from display_window import DisplayWindow, _apply_verse_html, available_screens
from keybindings import KeyBindings
from keybindings_dialog import KeyBindingsDialog
from settings_dialog import SettingsDialog

LOGO_PATH = theme.APP_DIR / 'resources' / 'vcbc logo.png'


def _circular_pixmap(path: Path, size: int) -> QPixmap:
    source = QPixmap(str(path))
    if source.isNull():
        return source
    source = source.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    result = QPixmap(size, size)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path_clip = QPainterPath()
    path_clip.addEllipse(QRectF(0, 0, size, size))
    painter.setClipPath(path_clip)
    x = (size - source.width()) // 2
    y = (size - source.height()) // 2
    painter.drawPixmap(x, y, source)
    painter.end()
    return result


def _is_kjv(version_name: str) -> bool:
    # Words-of-Christ ("red letter") data only exists for the KJV — matched loosely
    # since bible files are keyed by filename stem, which can vary (e.g.
    # "King James Version", "KJV").
    return 'king james' in version_name.lower() or version_name.strip().upper() == 'KJV'


class MainWindow(QMainWindow):
    def __init__(self, bibles: dict[str, Bible]):
        super().__init__()
        self.bibles = bibles
        self.version_name = next(iter(bibles))
        self.bible = bibles[self.version_name]
        self.display = DisplayWindow(self.bible)
        self.display.is_kjv = _is_kjv(self.version_name)
        self.current_verses: list[Verse] = []
        self.keybindings = KeyBindings.load()

        self.setWindowTitle('Scripture Cast')
        self.setStyleSheet(f'QMainWindow {{ background: {theme.BG}; }} QWidget {{ color: {theme.TEXT}; }}')

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_identity_strip())

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(theme.SPACE_6, theme.SPACE_6, theme.SPACE_6, theme.SPACE_6)
        body_layout.setSpacing(theme.SPACE_6)
        body_layout.addWidget(self._build_find_passage_card(), stretch=135)
        body_layout.addWidget(self._build_on_display_card(), stretch=100)
        outer.addWidget(body, stretch=1)

        QApplication.instance().installEventFilter(self)
        self._apply_shortcuts(self.keybindings)
        self._load_book_chapter()

        # Must run after all widgets/layouts exist: the toolbar row's buttons need more
        # than 720px, so Qt grows the window past any earlier resize() to fit its real
        # content — centering before that happens uses the wrong (smaller) width and
        # ends up visibly off-center once the real size kicks in.
        self.resize(max(self.sizeHint().width(), 900), 640)
        self._center_on_screen()

    def _center_on_screen(self):
        screen = self.screen() or QApplication.primaryScreen()
        if not screen:
            return
        available = screen.availableGeometry()
        x = available.x() + (available.width() - self.width()) // 2
        y = available.y() + (available.height() - self.height()) // 2
        self.move(x, y)

    def _action_handlers(self) -> dict[str, callable]:
        return {
            'show_display': self._on_show_display_clicked,
            'send_to_display': self._on_send_clicked,
            'clear_display': self.display.clear,
            'blank_display': self.display.toggle_blank,
            'show_desktop': self.display.toggle_desktop,
            'switch_version': self._switch_to_next_version,
            'focus_search': self._on_focus_search,
        }

    def _on_focus_search(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.search_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search_edit.selectAll()

    def eventFilter(self, watched: QObject, event) -> bool:
        if watched is self.results_list.viewport() and event.type() == QEvent.Type.Resize:
            self._rewrap_results_list()
            return False
        # Application-wide so configured shortcuts work no matter which of our windows
        # (main control panel or fullscreen display) currently has OS keyboard focus —
        # per-window QShortcuts/keyPressEvent only fire on whichever window is focused,
        # and focus silently falls back to the main window after almost any interaction.
        if event.type() != QEvent.Type.KeyPress:
            return False
        if event.isAutoRepeat():
            # Holding a key down generates repeated synthetic KeyPress events; without
            # this, a single physical press-and-hold can fire an action (e.g. switch
            # version) multiple times in quick succession.
            return True
        pressed = QKeySequence(event.keyCombination())
        for action, handler in self._action_handlers().items():
            sequence = getattr(self.keybindings, action)
            if not sequence:
                continue
            if pressed.matches(QKeySequence(sequence)) != QKeySequence.SequenceMatch.ExactMatch:
                continue
            if action == 'send_to_display' and isinstance(QApplication.focusWidget(), (QLineEdit, QSpinBox)):
                # Let Return pass through untouched so the focused field's own
                # returnPressed handling (e.g. search) still fires normally.
                return False
            handler()
            return True
        return False

    def _apply_shortcuts(self, bindings: KeyBindings):
        self.keybindings = bindings

    def _on_keybindings_clicked(self):
        dialog = KeyBindingsDialog(self.keybindings, parent=self)
        dialog.bindings_changed.connect(self._apply_shortcuts)
        dialog.exec()

    def _build_on_display_card(self) -> QFrame:
        card, layout = self._make_card('On Display')

        header = QHBoxLayout()
        self.output_tag_label = QLabel()
        header.addStretch()
        header.addWidget(self.output_tag_label)
        layout.insertLayout(1, header)

        self._live_view_text = ''
        self._live_view_reference = ''

        self.live_view_frame = QFrame()
        self.live_view_frame.setStyleSheet('background: #0a0a0a; border: 1px solid ' + theme.DIVIDER + ';')
        self.live_view_frame.setMinimumHeight(220)
        frame_layout = QVBoxLayout(self.live_view_frame)
        frame_layout.setContentsMargins(theme.SPACE_4, theme.SPACE_4, theme.SPACE_4, theme.SPACE_4)
        self.live_view_label = QLabel('(nothing shown)')
        self.live_view_label.setWordWrap(True)
        self.live_view_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        frame_layout.addWidget(self.live_view_label)
        layout.addWidget(self.live_view_frame, stretch=1)

        self.display.content_changed.connect(self._on_display_content_changed)
        self.display.blanked_changed.connect(lambda _: self._update_live_view())
        self.display.desktop_shown_changed.connect(lambda _: self._update_live_view())

        button_grid = QVBoxLayout()
        top_row = QHBoxLayout()
        self.blank_button = QPushButton('Blank (Pause)')
        self.blank_button.setStyleSheet(theme.BUTTON_STYLE)
        self.blank_button.setCheckable(True)
        self.blank_button.setToolTip(
            'Temporarily hide the verse text (solid black) for a pause — prayer, announcement, etc.\n'
            'Your verse stays loaded; press again to instantly bring it back.'
        )
        self.blank_button.clicked.connect(self.display.set_blanked)
        self.display.blanked_changed.connect(self.blank_button.setChecked)
        self.desktop_button = QPushButton('Show Desktop')
        self.desktop_button.setStyleSheet(theme.BUTTON_STYLE)
        self.desktop_button.setCheckable(True)
        self.desktop_button.setToolTip('Hide the display window entirely, revealing the desktop underneath')
        self.desktop_button.clicked.connect(self.display.set_showing_desktop)
        self.display.desktop_shown_changed.connect(self.desktop_button.setChecked)
        top_row.addWidget(self.blank_button)
        top_row.addWidget(self.desktop_button)
        button_grid.addLayout(top_row)

        self.clear_button = QPushButton('Clear (Reset)')
        self.clear_button.setStyleSheet(theme.BUTTON_STYLE)
        self.clear_button.setToolTip(
            'Wipe the loaded verse completely. There is nothing to resume — '
            'select or search again to show something new.'
        )
        self.clear_button.clicked.connect(self.display.clear)
        button_grid.addWidget(self.clear_button)

        layout.addLayout(button_grid)
        self._update_output_tag()
        return card

    def _update_output_tag(self):
        self.output_tag_label.setText(f'Output: Screen {self.screen_combo.currentIndex()}'.upper())
        self.output_tag_label.setStyleSheet(theme.TAG_OUTLINE_STYLE)

    def _on_display_content_changed(self, text: str, reference: str):
        self._live_view_text = text
        self._live_view_reference = reference
        self._update_live_view()

    def _update_live_view(self):
        text = self._live_view_text
        if not text:
            self.live_view_label.setStyleSheet(
                'color: #777777; font-style: italic; font-family: Georgia, serif;'
            )
            self.live_view_label.setText('(nothing shown)')
            return

        status = ''
        if self.display.is_showing_desktop:
            status = '<div style="font-size: 11px; color: #ff8888;">(desktop shown — audience does not see this)</div>'
        elif self.display.is_blanked:
            status = '<div style="font-size: 11px; color: #ff8888;">(blanked — audience sees black)</div>'

        self.live_view_label.setStyleSheet('color: white; font-family: Georgia, serif;')
        self.live_view_label.setTextFormat(Qt.TextFormat.RichText)

        # This mirror shows the operator the whole verse, unlike the real display which
        # is paginated to fit the screen — so instead of pagination, shrink the font here
        # until the full text fits the fixed-height box. Book/chapter/verse reference is
        # deliberately omitted here — this box is purely a visual check of the verse text.
        text_size = self._fit_live_view_font_size(text, status)
        body_html = self.display.full_content_html()
        self.live_view_label.setText(
            f'<div style="font-size: {text_size}px;">{body_html}</div>'
            f'{status}'
        )

    def _fit_live_view_font_size(self, text: str, status: str) -> int:
        available_width = max(self.live_view_frame.width() - 2 * theme.SPACE_4 - 16, 100)
        available_height = max(self.live_view_frame.height() - 2 * theme.SPACE_4, 40)
        for text_size in range(20, 9, -1):
            font = QFont('Georgia')
            font.setPixelSize(text_size)
            metrics = QFontMetrics(font)
            bounds = metrics.boundingRect(0, 0, available_width, 0, Qt.TextFlag.TextWordWrap, text)
            # Leave room for the status note below the verse text, if shown.
            reserved = 16 if status else 0
            if bounds.height() + reserved <= available_height:
                return text_size
        return 10

    def _make_card(self, kicker: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName('card')
        card.setStyleSheet(theme.CARD_STYLE)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(theme.SPACE_4, theme.SPACE_4, theme.SPACE_4, theme.SPACE_4)
        layout.setSpacing(theme.SPACE_3)
        kicker_label = QLabel(kicker.upper())
        kicker_label.setStyleSheet(theme.KICKER_STYLE)
        layout.addWidget(kicker_label)
        return card, layout

    def _build_identity_strip(self) -> QWidget:
        strip = QFrame()
        strip.setStyleSheet(f'background: {theme.SURFACE}; border-bottom: 1px solid {theme.DIVIDER};')
        row = QHBoxLayout(strip)
        row.setContentsMargins(theme.SPACE_4, theme.SPACE_3, theme.SPACE_4, theme.SPACE_3)
        row.setSpacing(theme.SPACE_3)

        logo_label = QLabel()
        logo_pixmap = _circular_pixmap(LOGO_PATH, 40)
        if not logo_pixmap.isNull():
            logo_label.setPixmap(logo_pixmap)
        row.addWidget(logo_label)

        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        name_label = QLabel('Valenzuela City Baptist Church')
        name_label.setStyleSheet(f'font-family: {theme.FONT_HEADING}; font-weight: 600; font-size: 16px;')
        subtitle_label = QLabel('SCRIPTURE CAST — OPERATOR CONSOLE')
        subtitle_label.setStyleSheet(f'font-size: 10px; letter-spacing: 1px; color: {theme.TEXT_MUTED};')
        title_box.addWidget(name_label)
        title_box.addWidget(subtitle_label)
        row.addLayout(title_box)
        row.addStretch()

        self.version_tag_label = QLabel()
        self.version_tag_label.setStyleSheet(theme.TAG_OUTLINE_STYLE)
        row.addWidget(self.version_tag_label)

        self.version_combo = QComboBox()
        self.version_combo.setStyleSheet(theme.COMBO_STYLE)
        self.version_combo.addItems(list(self.bibles.keys()))
        self.version_combo.setCurrentText(self.version_name)
        self.version_combo.currentTextChanged.connect(self._on_version_combo_changed)
        row.addWidget(self.version_combo)

        self.screen_combo = QComboBox()
        self.screen_combo.setStyleSheet(theme.COMBO_STYLE)
        for i, screen in enumerate(available_screens()):
            self.screen_combo.addItem(f'{i}: {screen.name()} ({screen.geometry().width()}x{screen.geometry().height()})')
        self.screen_combo.currentIndexChanged.connect(lambda _: self._update_output_tag())
        row.addWidget(self.screen_combo)

        self.show_display_button = QPushButton('Show Display Window')
        self.show_display_button.setStyleSheet(theme.BUTTON_STYLE)
        self.show_display_button.clicked.connect(self._on_show_display_clicked)
        row.addWidget(self.show_display_button)

        self.preview_button = QPushButton('Preview')
        self.preview_button.setStyleSheet(theme.GHOST_BUTTON_STYLE)
        self.preview_button.setToolTip('Open the display as a normal window, for testing without a second monitor')
        self.preview_button.clicked.connect(self.display.show_preview)
        row.addWidget(self.preview_button)

        self.settings_button = QPushButton('Display Settings…')
        self.settings_button.setStyleSheet(theme.GHOST_BUTTON_STYLE)
        self.settings_button.clicked.connect(self._on_settings_clicked)
        row.addWidget(self.settings_button)

        self.keybindings_button = QPushButton('Shortcuts…')
        self.keybindings_button.setStyleSheet(theme.GHOST_BUTTON_STYLE)
        self.keybindings_button.clicked.connect(self._on_keybindings_clicked)
        row.addWidget(self.keybindings_button)

        self._update_version_tag()
        return strip

    def _update_version_tag(self):
        self.version_tag_label.setText(self.version_name.upper())

    def _on_version_combo_changed(self, version_name: str):
        self._set_version(version_name)

    def _set_version(self, version_name: str):
        """Single source of truth for switching the active Bible version. Updates
        internal state and the display directly, then syncs the combo box to match —
        rather than routing through the combo box's currentTextChanged signal, which
        under rapid repeated calls (e.g. keyboard auto-repeat) is not guaranteed to fire
        for every intermediate value, only the final one."""
        if version_name not in self.bibles or version_name == self.version_name:
            return
        self.version_name = version_name
        self.bible = self.bibles[version_name]
        self.display.bible = self.bible
        self.display.is_kjv = _is_kjv(version_name)
        self._update_version_tag()

        if self.version_combo.currentText() != version_name:
            self.version_combo.blockSignals(True)
            self.version_combo.setCurrentText(version_name)
            self.version_combo.blockSignals(False)

        # Refresh the book list in case the new version names books differently, without
        # resetting the browse position back to book 1 chapter 1 — switching translation
        # shouldn't lose the preacher's place in either the browser or the display.
        self.book_combo.blockSignals(True)
        self.book_combo.clear()
        self.book_combo.addItems(self.bible.book_names())
        self.book_combo.blockSignals(False)

        # Re-fetch whatever's currently shown/selected in the new version, by the same
        # book/chapter/verse coordinates, and re-render the results list to match — this
        # naturally replaces whatever the old version's results list was showing.
        if self.current_verses:
            self.current_verses = self._reload_verses_in_current_version(self.current_verses)
            if self.current_verses:
                self.display.show_verses(self.current_verses)
                book, chapter = self.current_verses[0].book, self.current_verses[0].chapter
                if self.book_combo.currentText() != book:
                    self.book_combo.blockSignals(True)
                    self.book_combo.setCurrentText(book)
                    self.book_combo.blockSignals(False)
                if self.chapter_spin.value() != chapter:
                    self.chapter_spin.blockSignals(True)
                    self.chapter_spin.setValue(chapter)
                    self.chapter_spin.blockSignals(False)
                verse_count = self.bible.verse_count(book, chapter)
                chapter_verses = self.bible.get_verses(book, chapter, 1, verse_count) if verse_count else []
                select_ranges = [(v.verse, v.verse) for v in self.current_verses]
                self._populate_results(chapter_verses, select_ranges=select_ranges)

    def _reload_verses_in_current_version(self, verses: list[Verse]) -> list[Verse]:
        reloaded = []
        for v in verses:
            match = self.bible.get_verses(v.book, v.chapter, v.verse, v.verse)
            if match:
                reloaded.append(match[0])
        return reloaded

    def _switch_to_next_version(self):
        names = list(self.bibles.keys())
        if len(names) < 2:
            return
        next_index = (names.index(self.version_name) + 1) % len(names)
        self._set_version(names[next_index])

    def _on_settings_clicked(self):
        screens = available_screens()
        index = self.screen_combo.currentIndex()
        screen = screens[index] if 0 <= index < len(screens) else None
        dialog = SettingsDialog(self.display, screen, parent=self)
        dialog.exec()

    def _build_find_passage_card(self) -> QFrame:
        card, layout = self._make_card('Find a Passage')

        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setStyleSheet(theme.INPUT_STYLE)
        self.search_edit.setPlaceholderText('Reference (John 3:16) or text search')
        self.search_edit.returnPressed.connect(self._on_search)
        search_row.addWidget(self.search_edit, stretch=1)
        self.search_button = QPushButton('Search')
        self.search_button.setStyleSheet(theme.BUTTON_STYLE)
        self.search_button.clicked.connect(self._on_search)
        search_row.addWidget(self.search_button)
        layout.addLayout(search_row)

        browse_row = QHBoxLayout()
        book_label = QLabel('Book')
        book_label.setStyleSheet(f'font-size: 12px; color: {theme.TEXT_MUTED};')
        browse_row.addWidget(book_label)
        self.book_combo = QComboBox()
        self.book_combo.setStyleSheet(theme.COMBO_STYLE)
        self.book_combo.currentTextChanged.connect(self._on_book_changed)
        browse_row.addWidget(self.book_combo, stretch=1)

        chapter_label = QLabel('Chapter')
        chapter_label.setStyleSheet(f'font-size: 12px; color: {theme.TEXT_MUTED};')
        browse_row.addWidget(chapter_label)
        self.chapter_spin = QSpinBox()
        self.chapter_spin.setStyleSheet(theme.SPINBOX_STYLE)
        self.chapter_spin.setMinimum(1)
        self.chapter_spin.valueChanged.connect(self._load_chapter)
        browse_row.addWidget(self.chapter_spin)
        layout.addLayout(browse_row)

        header_row = QHBoxLayout()
        self.results_title_label = QLabel()
        self.results_title_label.setStyleSheet(f'font-family: {theme.FONT_HEADING}; font-weight: 600; font-size: 15px;')
        header_row.addWidget(self.results_title_label)
        header_row.addStretch()
        self.back_to_browse_button = QPushButton('Back to browse')
        self.back_to_browse_button.setStyleSheet(theme.GHOST_BUTTON_STYLE)
        self.back_to_browse_button.clicked.connect(self._on_back_to_browse)
        self.back_to_browse_button.hide()
        header_row.addWidget(self.back_to_browse_button)
        self.results_count_label = QLabel()
        self.results_count_label.setStyleSheet(f'font-size: 11px; color: {theme.TEXT_MUTED};')
        header_row.addWidget(self.results_count_label)
        layout.addLayout(header_row)

        self.results_list = QListWidget()
        self.results_list.setStyleSheet(theme.RESULTS_LIST_STYLE)
        self.results_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results_list.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.results_list, stretch=1)

        return card

    def _rewrap_results_list(self):
        """Item widgets don't auto-rewrap on list resize like native item text would, so
        each row's cached size hint (set in _populate_results) is stale after the window
        is resized — recompute it against the list's new width."""
        wrap_width = max(self.results_list.viewport().width() - 12, 100)
        for i in range(self.results_list.count()):
            item = self.results_list.item(i)
            label = self.results_list.itemWidget(item)
            if not isinstance(label, QLabel):
                continue
            wrapped_height = label.heightForWidth(wrap_width)
            item.setSizeHint(QSize(wrap_width, wrapped_height))

    def _on_back_to_browse(self):
        self._load_chapter()

    def _load_book_chapter(self):
        self.book_combo.blockSignals(True)
        self.book_combo.clear()
        self.book_combo.addItems(self.bible.book_names())
        self.book_combo.blockSignals(False)
        self._on_book_changed(self.book_combo.currentText())

    def _on_book_changed(self, book: str):
        if not book:
            return
        count = self.bible.chapter_count(book)
        self.chapter_spin.blockSignals(True)
        self.chapter_spin.setMaximum(max(count, 1))
        self.chapter_spin.setValue(1)
        self.chapter_spin.blockSignals(False)
        self._load_chapter()

    def _load_chapter(self):
        book = self.book_combo.currentText()
        chapter = self.chapter_spin.value()
        if not book:
            return
        verse_count = self.bible.verse_count(book, chapter)
        verses = self.bible.get_verses(book, chapter, 1, verse_count) if verse_count else []
        self._populate_results(verses, title=f'{book} {chapter}', is_search_mode=False)

    def _on_search(self):
        query = self.search_edit.text().strip()
        if not query:
            return
        ranges = self.bible.parse_reference(query)
        if ranges:
            book, chapter = ranges[0][0], ranges[0][1]
            verse_count = self.bible.verse_count(book, chapter)
            verses = self.bible.get_verses(book, chapter, 1, verse_count) if verse_count else []
            select_ranges = [(r[2], r[3]) for r in ranges if r[0] == book and r[1] == chapter]
            self._populate_results(verses, select_ranges=select_ranges, title=f'{book} {chapter}', is_search_mode=False)
        else:
            verses = self.bible.search_text(query)
            words = [w for term in query.split(',') for w in term.split() if w.strip()]
            self._populate_results(verses, highlight_words=words, title=f'Results for “{query}”', is_search_mode=True)

    def _populate_results(
        self,
        verses: list[Verse],
        select_ranges: list[tuple[int, int]] | None = None,
        highlight_words: list[str] | None = None,
        title: str | None = None,
        is_search_mode: bool | None = None,
    ):
        if title is not None:
            self.results_title_label.setText(title)
        if is_search_mode is not None:
            self.back_to_browse_button.setVisible(is_search_mode)
        self.results_count_label.setText(f'{len(verses)} verse(s)')
        self.results_list.clear()
        self.results_list.blockSignals(select_ranges is not None)
        select_items = []
        for v in verses:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, v)
            self.results_list.addItem(item)
            label = QLabel(self._format_verse_html(v, highlight_words))
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setWordWrap(True)
            label.setContentsMargins(6, 3, 6, 3)
            # QListWidget's native selection highlight paints behind item widgets, so it's
            # hidden by this opaque QLabel — autoFillBackground lets the label's own
            # stylesheet background show instead; _refresh_selection_highlight keeps it in
            # sync with the item's actual selected state.
            label.setAutoFillBackground(True)
            self.results_list.setItemWidget(item, label)
            # sizeHint() alone measures the label's natural (unwrapped) width, which is
            # wider than the list can display — forcing a horizontal scrollbar instead of
            # wrapping, and in some cases under-measuring row height enough to clip
            # descenders (e.g. "g", "y"). Force-wrap against the list's actual viewport
            # width first so the computed height matches what will really be rendered.
            wrap_width = max(self.results_list.viewport().width() - 12, 100)
            wrapped_height = label.heightForWidth(wrap_width)
            item.setSizeHint(QSize(wrap_width, wrapped_height))
            if select_ranges and any(start <= v.verse <= end for start, end in select_ranges):
                select_items.append(item)
        if select_ranges is not None:
            self.results_list.blockSignals(False)
            for item in select_items:
                item.setSelected(True)
            if select_items:
                self.results_list.scrollToItem(select_items[0])
            self._on_selection_changed()
        self._refresh_selection_highlight()

    def _refresh_selection_highlight(self):
        """Marks each result row's label bold/non-bold to reflect its actual selection
        state, since setItemWidget()'s opaque QLabel hides QListWidget's native
        selection painting — no background highlight, just a weight change."""
        for i in range(self.results_list.count()):
            item = self.results_list.item(i)
            label = self.results_list.itemWidget(item)
            if not isinstance(label, QLabel):
                continue
            label.setStyleSheet('background-color: transparent;')
            font = label.font()
            font.setBold(item.isSelected())
            label.setFont(font)

    def _format_verse_html(self, v: Verse, highlight_words: list[str] | None) -> str:
        prefix = html.escape(f'{v.verse}  ')
        is_kjv = self.display.is_kjv
        config = self.display.config
        red_ranges = red_letter.red_ranges(v.book, v.chapter, v.verse, len(v.text)) if is_kjv and config.red_letter else []
        italic_ranges = (
            supplied_words.supplied_ranges(v.book, v.chapter, v.verse, len(v.text))
            if is_kjv and config.supplied_words_italic else []
        )
        highlight_ranges = []
        if highlight_words:
            pattern = '|'.join(re.escape(w) for w in highlight_words)
            highlight_ranges = [(m.start(), m.end()) for m in re.finditer(pattern, v.text, flags=re.IGNORECASE)]
        text = _apply_verse_html(v.text, red_ranges, italic_ranges, highlight_ranges)
        return f'<b style="color:{theme.ACCENT_700};">{prefix}</b>{text}'

    def _on_selection_changed(self):
        selected = self.results_list.selectedItems()
        self.current_verses = [item.data(Qt.ItemDataRole.UserRole) for item in selected]
        self._refresh_selection_highlight()
        if self.current_verses:
            self.display.show_verses(self.current_verses)

    def _on_show_display_clicked(self):
        screens = available_screens()
        index = self.screen_combo.currentIndex()
        if 0 <= index < len(screens):
            self.display.show_on_screen(screens[index])

    def _on_send_clicked(self):
        if self.current_verses:
            self.display.show_verses(self.current_verses)

    def closeEvent(self, event):
        self.display.close()
        super().closeEvent(event)
