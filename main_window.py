"""Control panel: browse or search verses, send selection to the display window."""
import html
import re
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics, QKeySequence, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

import bible_dictionary
import identity
import red_letter
import strongs_dictionary
import supplied_words
import theme
import tsk_dictionary
from bible import Bible, Verse
from display_window import DisplayWindow, _apply_verse_html, available_screens
from keybindings import KeyBindings
from service import ServiceItem, ServiceList
from settings_dialog import SettingsDialog

DEFAULT_LOGO_PATH = theme.APP_DIR / 'resources' / 'vcbc logo.png'
THEME = theme.instance
IDENTITY = identity.instance


def _circular_pixmap(path: Path, size: int) -> QPixmap:
    source = QPixmap(str(path))
    if source.isNull():
        return source
    source = source.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    result = QPixmap(size, size)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    # setClipPath's anti-aliased edge doesn't blend correctly against a transparent
    # destination — it leaves a faint gray halo of the clipped-away corner pixels
    # bleeding through at the circle's boundary. Drawing the mask as its own shape
    # first, then compositing the source pixmap only where that shape was painted
    # (SourceIn), produces a clean edge instead.
    path_clip = QPainterPath()
    path_clip.addEllipse(QRectF(0, 0, size, size))
    painter.fillPath(path_clip, Qt.GlobalColor.black)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
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
        # A second DisplayWindow, never shown on any real screen, dedicated to rendering
        # the control panel's "On Display" mirror. Resizing/grabbing the real self.display
        # for a thumbnail would visibly corrupt the actual congregation-facing output
        # whenever it's live on a projector, so the mirror needs its own instance kept in
        # sync with the same verses/config/version — see _sync_preview_display().
        self.preview_display = DisplayWindow(self.bible)
        self.preview_display.is_kjv = self.display.is_kjv
        self.current_verses: list[Verse] = []
        # Whether the results list is currently showing text-search results (which
        # can span many books/chapters, so each row needs a book/chapter prefix) or a
        # single browsed chapter (where the book/chapter is already shown once in the
        # results title, so each row only needs its verse number) — see
        # _format_verse_html.
        self._results_is_search_mode = False
        # PowerPoint-style "type a slide number, hit Enter" verse jump — only active
        # while the results list has focus (see eventFilter), so it never fights with
        # typing in the search box or chapter spinner.
        self._verse_jump_buffer = ''
        self._suppress_display_sync = False
        # Deferred (not called synchronously — see _on_page_changed) to a single-shot
        # timer instead of QTimer.singleShot() so a manual book/chapter pick can
        # cancel a pending resync via .stop() before it fires and overwrites the
        # user's own browse action with stale display state.
        self._results_sync_timer = QTimer(self)
        self._results_sync_timer.setSingleShot(True)
        self._results_sync_timer.timeout.connect(self._sync_results_selection_to_display)
        self.service = ServiceList.load()
        self.keybindings = KeyBindings.load()
        self._cards: list[QFrame] = []
        self._kicker_labels: list[QLabel] = []

        self.setWindowTitle('Scripture Cast')

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

        right_column = QVBoxLayout()
        right_column.setSpacing(theme.SPACE_6)
        right_column.addWidget(self._build_on_display_card())
        right_column.addWidget(self._build_service_card(), stretch=1)
        body_layout.addLayout(right_column, stretch=100)

        outer.addWidget(body, stretch=1)

        # Qt implicitly designates the first eligible QPushButton in a window as the
        # "default button", which intercepts Return/Enter globally before it reaches
        # whichever widget actually has focus — this silently ate the verse-jump
        # feature's Enter key while the results list was focused. None of this
        # window's buttons are meant to be a default-on-Enter action (Enter's
        # behavior is already explicit via the send_to_display keybinding and the
        # verse-jump buffer), so disable autoDefault on all of them.
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)

        QApplication.instance().installEventFilter(self)
        self._apply_shortcuts(self.keybindings)
        self._load_book_chapter()
        self._apply_theme()
        THEME.changed.connect(self._apply_theme)
        IDENTITY.changed.connect(self._update_identity)

        # Must run after all widgets/layouts exist: the toolbar row's buttons need more
        # than 720px, so Qt grows the window past any earlier resize() to fit its real
        # content — centering before that happens uses the wrong (smaller) width and
        # ends up visibly off-center once the real size kicks in.
        self.resize(max(self.sizeHint().width(), 1480), 760)
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
            'show_desktop': self.display.toggle_desktop,
            'switch_version': self._switch_to_next_version,
            'focus_search': self._on_focus_search,
            'add_to_service': self._on_add_to_service_clicked,
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
        if watched is self.live_view_frame and event.type() == QEvent.Type.Resize:
            self._rewrap_live_view_frame()
            return False
        if getattr(watched, '_is_verse_row_label', False) and self._handle_verse_label_mouse_event(watched, event):
            return True
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
        # Global (not gated on results_list having focus) so this works the same way
        # switch_version/show_desktop do: the fullscreen display is always-on-top and
        # covers the control panel entirely on a single-monitor setup, so requiring a
        # click into the results list first would make this unreachable whenever the
        # display is actually live — which is exactly when it's most useful. Only
        # excluded while a text field is focused, so it doesn't hijack normal typing
        # in the search box or chapter spinner.
        if not isinstance(QApplication.focusWidget(), (QLineEdit, QSpinBox)) and self._handle_verse_jump_key(event):
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

    def _handle_verse_label_mouse_event(self, label: QLabel, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            label._press_pos = event.position()
            # Each row's QLabel has its own independent text-selection state, so
            # without this a word highlighted for a dictionary lookup earlier stays
            # visibly selected even after picking a different word in another row —
            # clear every other row's selection whenever a new one is about to start,
            # matching how text selection normally behaves in one place at a time.
            for i in range(self.results_list.count()):
                other_label = self.results_list.itemWidget(self.results_list.item(i))
                if isinstance(other_label, QLabel) and other_label is not label:
                    other_label.setSelection(0, 0)
            return False  # let the label still start its own text-selection drag
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            press_pos = getattr(label, '_press_pos', None)
            moved = press_pos is not None and (event.position() - press_pos).manhattanLength() > 4
            if not moved:
                # A plain click, not a drag — the label consumed it for its own
                # (empty) text selection, so the list never saw it as a row click.
                # Select the row directly instead.
                self.results_list.setCurrentItem(label._verse_row_item)
            return False
        return False

    def _handle_verse_jump_key(self, event) -> bool:
        """PowerPoint-style "type a number, hit Enter" verse jump: digits build up a
        buffer and Enter selects the matching verse number within the currently
        loaded chapter/results — same list of verses the operator is already
        browsing or searching, not a chapter/book lookup. Works globally (not gated
        on the results list having focus) so it's reachable even while the
        always-on-top fullscreen display is covering the control panel. Any other
        key (or a number with no match) just clears the buffer and falls through to
        normal handling."""
        key = event.key()
        if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            self._verse_jump_buffer += chr(key)
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            buffer, self._verse_jump_buffer = self._verse_jump_buffer, ''
            if not buffer:
                return False
            target = int(buffer)
            for i in range(self.results_list.count()):
                item = self.results_list.item(i)
                verse = item.data(Qt.ItemDataRole.UserRole)
                if verse is not None and verse.verse == target:
                    self.results_list.setCurrentItem(item)
                    self.results_list.scrollToItem(item)
                    return True
            # No verse with that number in the current results — do nothing, per spec.
            return True
        self._verse_jump_buffer = ''
        return False

    def _apply_shortcuts(self, bindings: KeyBindings):
        self.keybindings = bindings

    def _build_on_display_card(self) -> QFrame:
        card, layout = self._make_card('On Display')

        self.live_view_frame = QFrame()
        self.live_view_frame.setStyleSheet(f'background: {THEME.display_bg}; border: 1px solid {THEME.divider};')
        live_view_frame_layout = QVBoxLayout(self.live_view_frame)
        live_view_frame_layout.setContentsMargins(0, 0, 0, 0)
        self.live_view_label = QLabel('(nothing shown)')
        self.live_view_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Without Ignored, setPixmap() below makes the label's sizeHint() grow to the
        # pixmap's exact size — if that's ever a hair larger than the frame's current
        # interior (e.g. rounding in the KeepAspectRatio scale), the QVBoxLayout grows
        # the frame to fit, which re-fires the frame's Resize event, which recomputes and
        # re-sets its height again — an infinite resize feedback loop that visibly grows
        # the whole window. Ignoring the pixmap's own size hint breaks that cycle: the
        # frame's size is the only thing driving the label's size, never the reverse.
        self.live_view_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        live_view_frame_layout.addWidget(self.live_view_label)
        layout.addWidget(self.live_view_frame)

        # A floating banner over the thumbnail (not part of the layout, so it can't affect
        # the frame's size and re-trigger the resize loop described above) warning the
        # operator when the real, audience-facing display doesn't currently match what
        # this mirror shows (the desktop is showing instead of the verse).
        self.live_view_status_label = QLabel(self.live_view_frame)
        self.live_view_status_label.setStyleSheet(
            'background-color: rgba(200, 40, 40, 200); color: white; font-size: 11px; '
            'font-weight: 600; padding: 3px 8px;'
        )
        self.live_view_status_label.hide()

        self.display.content_changed.connect(lambda *_: self._sync_preview_display())
        self.display.desktop_shown_changed.connect(lambda _: self._sync_preview_display())

        # Previous/Next sit above the setup/utility actions since they're what the
        # operator touches constantly during a service, while Show Display/Show
        # Desktop/Clear are occasional (mostly once-per-service) actions. Styled as
        # flat, borderless icon buttons clustered together (rather than full pill
        # buttons pinned to opposite edges) since they're a tight, lightweight nav
        # pair, not two separate primary actions.
        page_nav_row = QHBoxLayout()
        page_nav_row.addStretch()
        self.prev_page_button = QPushButton(' Prev')
        self.prev_page_button.setStyleSheet(THEME.flat_nav_button_style)
        self.prev_page_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.prev_page_button.setIcon(theme.lucide_icon('chevron-left', THEME.text))
        self.prev_page_button.setToolTip(
            'Same as the Left Arrow/Page Up key on the display.\n'
            'Steps back within a long verse split across screens; at the start of the\n'
            'current selection, moves to the previous verse in the Bible instead.'
        )
        self.prev_page_button.clicked.connect(self.display.previous_page)
        page_nav_row.addWidget(self.prev_page_button)

        self.page_indicator_label = QLabel()
        self.page_indicator_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        page_nav_row.addWidget(self.page_indicator_label)

        self.next_page_button = QPushButton('Next ')
        self.next_page_button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.next_page_button.setStyleSheet(THEME.flat_nav_button_style)
        self.next_page_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_page_button.setIcon(theme.lucide_icon('chevron-right', THEME.text))
        self.next_page_button.setToolTip(
            'Same as the Right Arrow/Space/Page Down key on the display.\n'
            'Steps forward within a long verse split across screens; at the end of the\n'
            'current selection, moves to the next verse in the Bible instead.'
        )
        self.next_page_button.clicked.connect(self.display.next_page)
        page_nav_row.addWidget(self.next_page_button)
        page_nav_row.addStretch()
        layout.addLayout(page_nav_row)

        self.display.page_changed.connect(self._on_page_changed)
        self._on_page_changed(0, 0)

        action_row = QHBoxLayout()
        action_row.setSpacing(theme.SPACE_2)
        self.show_display_button = QPushButton('Show Display Window')
        self.show_display_button.setStyleSheet(THEME.primary_button_style)
        self.show_display_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.show_display_button.setToolTip('Same as the Shift+Enter shortcut — bring the display window to the front.')
        self.show_display_button.clicked.connect(self._on_show_display_clicked)
        action_row.addWidget(self.show_display_button)

        self.desktop_button = QPushButton('Show Desktop')
        self.desktop_button.setStyleSheet(THEME.button_style)
        self.desktop_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.desktop_button.setCheckable(True)
        self.desktop_button.setToolTip('Hide the display window entirely, revealing the desktop underneath')
        self.desktop_button.clicked.connect(self.display.set_showing_desktop)
        self.display.desktop_shown_changed.connect(self.desktop_button.setChecked)
        action_row.addWidget(self.desktop_button)

        self.clear_button = QPushButton('Clear (Reset)')
        self.clear_button.setStyleSheet(THEME.danger_button_style)
        self.clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_button.setToolTip(
            'Wipe the loaded verse completely. There is nothing to resume — '
            'select or search again to show something new.'
        )
        self.clear_button.clicked.connect(self.display.clear)
        action_row.addWidget(self.clear_button)

        layout.addLayout(action_row)

        layout.addStretch()
        self._on_output_screen_changed()
        return card

    def _build_service_card(self) -> QFrame:
        card, layout = self._make_card('Service Plan')

        self.service_list = QListWidget()
        self.service_list.setStyleSheet(THEME.service_list_style)
        self.service_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Each row is 30px (see _build_service_row's fixed height) — guarantee at
        # least 5 rows are visible before the list needs to scroll, regardless of
        # how much leftover space the window happens to have.
        self.service_list.setMinimumHeight(5 * 30)
        self.service_list.itemClicked.connect(self._on_service_item_activated)
        layout.addWidget(self.service_list, stretch=1)

        self.clear_service_button = QPushButton('Clear Service')
        self.clear_service_button.setStyleSheet(THEME.ghost_button_style)
        self.clear_service_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_service_button.clicked.connect(self._on_clear_service_clicked)
        layout.addWidget(self.clear_service_button)

        self._populate_service_list()
        return card

    def _build_service_row(self, index: int, service_item: ServiceItem) -> QWidget:
        row = QWidget()
        # setItemWidget() does not stretch this widget to fill the list item's taller
        # setSizeHint() cell — without an explicit height matching that hint, Qt gives
        # the row only its natural (smaller) sizeHint and top-anchors it within the
        # cell, which let the button overflow the row's real bottom edge and look
        # vertically offset from the label even though both were centered *within*
        # the too-short row.
        row.setFixedHeight(30)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(6, 0, 6, 0)
        label = QLabel(service_item.label)
        label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        row_layout.addWidget(label, stretch=1)
        icon_size = 16
        delete_button = QToolButton()
        delete_button.setAutoRaise(True)
        # QSS can restyle the button's background on :hover, but not the icon pixmap
        # itself — without swapping to a white icon on hover, the muted gray "x"
        # nearly disappears against the red highlight.
        idle_icon = theme.lucide_icon('x', THEME.text_muted, size=icon_size)
        hover_icon = theme.lucide_icon('x', '#ffffff', size=icon_size)
        delete_button.setIcon(idle_icon)
        delete_button.setIconSize(QSize(icon_size, icon_size))
        delete_button.setFixedSize(24, 24)
        delete_button.setStyleSheet(
            'QToolButton { background: transparent; border: none; padding: 0px; margin: 0px; } '
            f'QToolButton:hover {{ background: #c0392b; border-radius: 4px; }}'
        )
        delete_button.setCursor(Qt.CursorShape.PointingHandCursor)
        delete_button.setToolTip(f'Remove {service_item.label} from the Service Plan')
        delete_button.clicked.connect(lambda: self._on_remove_service_item_clicked(index))

        class _HoverIconSwap(QObject):
            def eventFilter(self_filter, watched, event):
                if event.type() == QEvent.Type.Enter:
                    delete_button.setIcon(hover_icon)
                elif event.type() == QEvent.Type.Leave:
                    delete_button.setIcon(idle_icon)
                return False

        hover_filter = _HoverIconSwap(delete_button)
        delete_button.installEventFilter(hover_filter)
        delete_button._hover_filter = hover_filter  # keep the filter alive with the button

        row_layout.addWidget(delete_button, alignment=Qt.AlignmentFlag.AlignVCenter)
        return row

    def _populate_service_list(self):
        self.service_list.clear()
        for index, service_item in enumerate(self.service.items):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, service_item)
            self.service_list.addItem(item)
            item.setSizeHint(QSize(0, 30))
            self.service_list.setItemWidget(item, self._build_service_row(index, service_item))

    def _on_add_to_service_clicked(self):
        if not self.current_verses:
            return
        first, last = self.current_verses[0], self.current_verses[-1]
        if first.verse == last.verse:
            label = f'{first.book} {first.chapter}:{first.verse}'
        else:
            label = f'{first.book} {first.chapter}:{first.verse}-{last.verse}'
        service_item = ServiceItem(
            book=first.book, chapter=first.chapter, first_verse=first.verse, last_verse=last.verse, label=label,
        )
        self.service.items.append(service_item)
        self.service.save()
        self._populate_service_list()

    def _on_service_item_activated(self, item: QListWidgetItem):
        service_item: ServiceItem = item.data(Qt.ItemDataRole.UserRole)
        verses = self.bible.get_verses(service_item.book, service_item.chapter, service_item.first_verse, service_item.last_verse)
        if not verses:
            return
        self.current_verses = verses
        self.display.show_verses(verses)
        self.add_to_service_button.setEnabled(True)

    def _on_remove_service_item_clicked(self, index: int):
        del self.service.items[index]
        self.service.save()
        self._populate_service_list()

    def _on_clear_service_clicked(self):
        if not self.service.items:
            return
        self.service.items.clear()
        self.service.save()
        self._populate_service_list()

    def _on_page_changed(self, page_index: int, total_pages: int):
        # Only worth showing when the current verse is actually word-split across
        # multiple screens — otherwise it'd read "1/1" for every single verse, which
        # doesn't tell the operator anything (Next/Previous still move between verses
        # regardless of this indicator, via the Bible itself rather than page-splitting).
        self.page_indicator_label.setText(f'Part {page_index}/{total_pages}' if total_pages > 1 else '')
        self.page_indicator_label.setStyleSheet(f'color: {THEME.text_muted}; font-size: 12px;')
        self.prev_page_button.setEnabled(self.display.can_go_previous())
        self.next_page_button.setEnabled(self.display.can_go_next())
        # Deferred to the next event-loop tick rather than called synchronously here:
        # page_changed can fire from deep inside the results list's own
        # itemSelectionChanged handling (click a verse -> selection changes ->
        # show_verses() -> page_changed -> back into this list's selection again),
        # and mutating the same QListWidget's selection re-entrantly from within its
        # own signal chain segfaults PySide6/Qt rather than raising a catchable
        # Python exception. Running it after the current call stack unwinds keeps
        # the same end result without the re-entrancy. Uses a restartable timer
        # (not QTimer.singleShot) so a manual book/chapter change in the meantime can
        # cancel this before it fires — see _on_book_changed/_load_chapter.
        self._results_sync_timer.start(0)

    def _sync_results_selection_to_display(self):
        """Keeps the results list's highlighted row following whatever's actually on
        the live display — page_changed fires on every navigation (Previous/Next,
        verse-jump, or a normal click), so this is the single place that reconciles
        the two, rather than duplicating "select this verse" logic at each call site."""
        target = self.display.current_page_verse_range()
        if target is None:
            return
        book, chapter, first_verse, last_verse = target
        current_book = self.book_combo.currentText()
        current_chapter = self.chapter_spin.value()
        self._suppress_display_sync = True
        try:
            if self._results_is_search_mode or current_book != book or current_chapter != chapter:
                verse_count = self.bible.verse_count(book, chapter)
                verses = self.bible.get_verses(book, chapter, 1, verse_count) if verse_count else []
                self.book_combo.blockSignals(True)
                self.book_combo.setCurrentText(book)
                self.book_combo.blockSignals(False)
                self.chapter_spin.blockSignals(True)
                self.chapter_spin.setValue(chapter)
                self.chapter_spin.blockSignals(False)
                self._populate_results(
                    verses, select_ranges=[(first_verse, last_verse)], title=f'{book} {chapter}', is_search_mode=False,
                )
            else:
                select_items = []
                for i in range(self.results_list.count()):
                    item = self.results_list.item(i)
                    verse = item.data(Qt.ItemDataRole.UserRole)
                    if verse is not None and first_verse <= verse.verse <= last_verse:
                        select_items.append(item)
                self.results_list.blockSignals(True)
                self.results_list.clearSelection()
                for item in select_items:
                    item.setSelected(True)
                self.results_list.blockSignals(False)
                if select_items:
                    self.results_list.scrollToItem(select_items[0])
                # blockSignals() above means itemSelectionChanged never fires, so
                # current_verses would otherwise go stale here (still pointing at
                # whatever was last clicked, not wherever navigation actually landed)
                # — update it the same way _on_selection_changed() would, without its
                # show_verses() call (still suppressed).
                self._on_selection_changed()
        finally:
            self._suppress_display_sync = False

    def _on_output_screen_changed(self):
        self._rewrap_live_view_frame()

    def _rewrap_live_view_frame(self):
        """Locks the live-view mirror's height to its current width so it always shows
        the same aspect ratio as the selected output screen, instead of whatever shape
        the card's layout happens to squeeze it into."""
        width = max(self.live_view_frame.width(), 100)
        screen_width, screen_height = self._target_screen_resolution()
        aspect_ratio = screen_height / screen_width
        self.live_view_frame.setFixedHeight(round(width * aspect_ratio))
        self._sync_preview_display()

    def _target_screen_resolution(self) -> tuple[int, int]:
        screens = available_screens()
        index = self.screen_combo.currentIndex()
        if 0 <= index < len(screens):
            geometry = screens[index].geometry()
            return geometry.width(), geometry.height()
        return 1920, 1080

    def _sync_preview_display(self):
        """Keeps the hidden preview_display's content/page position matched to the real
        display, resized to the selected output screen's resolution, then re-grabs it as
        a scaled thumbnail — the mirror is a genuine rendering of what the real display
        would show, not an approximation, the same way a presentation app's "presenter
        view" mirrors the actual slide rather than reconstructing it separately.

        preview_display is never itself hidden for "show desktop" — that's a state of the
        real, audience-facing display only. The operator's copy keeps showing the loaded
        verse underneath regardless, with a status note layered on top, so they can
        always see what's queued up even while the real screen is hidden."""
        screen_width, screen_height = self._target_screen_resolution()
        if self.preview_display.size().toTuple() != (screen_width, screen_height):
            self.preview_display.resize_for_thumbnail(screen_width, screen_height)
        self.preview_display.sync_from(self.display)

        if not self.display.has_content:
            self.live_view_label.setText('(nothing shown)')
            self.live_view_label.setStyleSheet(f'color: {THEME.display_text_muted}; font-style: italic;')
            self.live_view_status_label.hide()
            return

        pixmap = self.preview_display.grab()
        scaled = pixmap.scaled(
            self.live_view_frame.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
        )
        self.live_view_label.setStyleSheet('')
        self.live_view_label.setPixmap(scaled)

        if self.display.is_showing_desktop:
            self.live_view_status_label.setText('DESKTOP SHOWN — AUDIENCE DOES NOT SEE THIS')
            self.live_view_status_label.show()
        else:
            self.live_view_status_label.hide()
        self.live_view_status_label.adjustSize()
        margin = 6
        self.live_view_status_label.move(margin, margin)

    def _make_card(self, kicker: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName('card')
        card.setStyleSheet(THEME.card_style)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(theme.SPACE_4, theme.SPACE_4, theme.SPACE_4, theme.SPACE_4)
        layout.setSpacing(theme.SPACE_3)
        kicker_label = QLabel(kicker.upper())
        kicker_label.setStyleSheet(THEME.kicker_style)
        layout.addWidget(kicker_label)
        self._cards.append(card)
        self._kicker_labels.append(kicker_label)
        return card, layout

    def _build_identity_strip(self) -> QWidget:
        strip = QFrame()
        self.identity_strip = strip
        row = QHBoxLayout(strip)
        row.setContentsMargins(theme.SPACE_4, theme.SPACE_3, theme.SPACE_4, theme.SPACE_3)
        row.setSpacing(theme.SPACE_3)

        self.identity_logo_label = QLabel()
        row.addWidget(self.identity_logo_label)

        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        self.identity_name_label = QLabel()
        self.identity_subtitle_label = QLabel('SCRIPTURE CAST — OPERATOR CONSOLE')
        title_box.addWidget(self.identity_name_label)
        title_box.addWidget(self.identity_subtitle_label)
        row.addLayout(title_box)
        row.addStretch()
        self._update_identity()

        self.version_combo = QComboBox()
        self.version_combo.setStyleSheet(THEME.combo_style)
        self.version_combo.addItems(list(self.bibles.keys()))
        self.version_combo.setCurrentText(self.version_name)
        self.version_combo.currentTextChanged.connect(self._on_version_combo_changed)
        row.addWidget(self.version_combo)

        self.screen_combo = QComboBox()
        self.screen_combo.setStyleSheet(THEME.combo_style)
        for i, screen in enumerate(available_screens()):
            self.screen_combo.addItem(f'{i}: {screen.name()} ({screen.geometry().width()}x{screen.geometry().height()})')
        self.screen_combo.currentIndexChanged.connect(lambda _: self._on_output_screen_changed())
        row.addWidget(self.screen_combo)

        self.settings_button = QPushButton('Settings…')
        self.settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_button.clicked.connect(self._on_settings_clicked)
        row.addWidget(self.settings_button)

        return strip

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
        self.preview_display.bible = self.bible
        self.preview_display.is_kjv = self.display.is_kjv

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
        dialog = SettingsDialog(self.display, self.preview_display, screen, THEME, IDENTITY, self.keybindings, parent=self)
        dialog.bindings_changed.connect(self._apply_shortcuts)
        dialog.exec()
        # Live-updates while the dialog was open only reached self.display/preview_display's
        # own config; the mirror's rendered thumbnail itself is only refreshed on content
        # change, so force one now in case a text-size/spacing/etc. setting changed.
        self._sync_preview_display()

    def _build_find_passage_card(self) -> QFrame:
        card, layout = self._make_card('Find a Passage')

        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText('Reference (John 3:16) or text search')
        self.search_edit.returnPressed.connect(self._on_search)
        search_row.addWidget(self.search_edit, stretch=1)
        self.search_button = QPushButton('Search')
        self.search_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_button.clicked.connect(self._on_search)
        search_row.addWidget(self.search_button)
        layout.addLayout(search_row)

        browse_row = QHBoxLayout()
        self.book_label = QLabel('Book')
        browse_row.addWidget(self.book_label)
        self.book_combo = QComboBox()
        self.book_combo.setStyleSheet(THEME.combo_style)
        self.book_combo.setMaxVisibleItems(15)
        self.book_combo.currentTextChanged.connect(self._on_book_changed)
        browse_row.addWidget(self.book_combo, stretch=1)

        self.chapter_label = QLabel('Chapter')
        browse_row.addWidget(self.chapter_label)
        self.chapter_spin = QSpinBox()
        self.chapter_spin.setMinimum(1)
        self.chapter_spin.valueChanged.connect(self._load_chapter)
        browse_row.addWidget(self.chapter_spin)
        layout.addLayout(browse_row)

        header_row = QHBoxLayout()
        self.results_title_label = QLabel()
        header_row.addWidget(self.results_title_label)
        header_row.addStretch()
        self.back_to_browse_button = QPushButton('Back to browse')
        self.back_to_browse_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_to_browse_button.clicked.connect(self._on_back_to_browse)
        self.back_to_browse_button.hide()
        header_row.addWidget(self.back_to_browse_button)
        self.results_count_label = QLabel()
        header_row.addWidget(self.results_count_label)
        layout.addLayout(header_row)

        self.results_list = QListWidget()
        self.results_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results_list.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.results_list, stretch=1)

        self.add_to_service_button = QPushButton('+ Add to Service')
        self.add_to_service_button.setStyleSheet(THEME.button_style)
        self.add_to_service_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_to_service_button.setToolTip(
            'Same as the Ctrl+= shortcut.\n'
            'Save the currently selected passage to the Service Plan below, so it can be\n'
            'jumped back to later without searching again.'
        )
        self.add_to_service_button.clicked.connect(self._on_add_to_service_clicked)
        self.add_to_service_button.setEnabled(False)
        layout.addWidget(self.add_to_service_button)

        return card

    def _rewrap_results_list(self):
        """Item widgets don't auto-rewrap on list resize like native item text would, so
        each row's cached size hint (set in _populate_results) is stale after the window
        is resized — recompute it against the list's new width."""
        for i in range(self.results_list.count()):
            item = self.results_list.item(i)
            label = self.results_list.itemWidget(item)
            if not isinstance(label, QLabel):
                continue
            item.setSizeHint(self._result_item_size_hint(label))

    def _result_item_size_hint(self, label: QLabel) -> QSize:
        # sizeHint() alone measures the label's natural (unwrapped) width, which is wider
        # than the list can display — forcing a horizontal scrollbar instead of wrapping.
        # heightForWidth() forces the wrap and gives back a height, but for rich-text
        # labels (bold/italic spans from red-letter/supplied-words markup) it consistently
        # under-reports the real rendered line height enough to clip ascenders/descenders
        # or overlap the next row — so pad it with a small buffer per wrapped line.
        wrap_width = max(self.results_list.viewport().width() - 12, 100)
        wrapped_height = label.heightForWidth(wrap_width)
        line_height = label.fontMetrics().height()
        line_count = max(round(wrapped_height / max(line_height, 1)), 1)
        return QSize(wrap_width, wrapped_height + line_count * 3)

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
        # A manual browse action always wins over a still-pending deferred resync
        # from an earlier display-navigation event (see _on_page_changed) — without
        # this, that stale callback could fire right after and silently overwrite
        # the book/chapter the user just picked with whatever the display still
        # happens to be showing.
        self._results_sync_timer.stop()
        verse_count = self.bible.verse_count(book, chapter)
        verses = self.bible.get_verses(book, chapter, 1, verse_count) if verse_count else []
        self._populate_results(verses, title=f'{book} {chapter}', is_search_mode=False)

    def _on_search(self):
        query = self.search_edit.text().strip()
        if not query:
            return
        self._results_sync_timer.stop()
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
            self._results_is_search_mode = is_search_mode
        self.results_count_label.setText(f'{len(verses)} verse(s)')
        # Blocked for the whole clear()+repopulate, not just while select_ranges is
        # being applied — QListWidget.clear() fires itemSelectionChanged immediately
        # if the list had a selection, even mid-repopulation with no new items yet,
        # which let stale/incomplete selection state leak into whatever's listening
        # (see the _sync_results_selection_to_display timer-reentrancy bug this
        # caused: a stray itemSelectionChanged during clear() pushed the old
        # selection back to self.display, which restarted the deferred resync timer
        # and silently reverted a book/chapter the user had just picked).
        self.results_list.blockSignals(True)
        self.results_list.clear()
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
            # Lets the operator drag-select a word in the verse text and right-click
            # it to look up a definition — see _on_verse_label_context_menu.
            # TextSelectableByMouse implicitly makes Qt give the label ClickFocus,
            # which caused the row underneath to visibly "select" just from mouse
            # movement — these labels should only ever participate in text
            # selection, never in keyboard focus/tab order, so force it back off.
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            label.customContextMenuRequested.connect(
                lambda pos, lbl=label: self._on_verse_label_context_menu(lbl, pos)
            )
            # TextSelectableByMouse also means the label consumes left-clicks for its
            # own text cursor, so a plain click no longer reaches the QListWidget
            # underneath as a "select this row" event — _is_verse_row_label +
            # eventFilter's mouse handling (see _handle_verse_label_mouse_event)
            # restores that: a click with no real drag still selects the row, while
            # an actual click-and-drag still highlights a word for the dictionary
            # lookup, same as before this feature existed.
            label._is_verse_row_label = True
            label._verse_row_item = item
            label.installEventFilter(self)
            self.results_list.setItemWidget(item, label)
            item.setSizeHint(self._result_item_size_hint(label))
            if select_ranges and any(start <= v.verse <= end for start, end in select_ranges):
                select_items.append(item)
        for item in select_items:
            item.setSelected(True)
        self.results_list.blockSignals(False)
        if select_items:
            self.results_list.scrollToItem(select_items[0])
        if select_ranges is not None:
            self._on_selection_changed()
        self._refresh_selection_highlight()

    def _refresh_selection_highlight(self):
        """Paints each result row's label to reflect its actual selection state,
        since setItemWidget()'s opaque QLabel hides QListWidget's native selection
        painting — a background tint plus bold text, not just a weight change."""
        for i in range(self.results_list.count()):
            item = self.results_list.item(i)
            label = self.results_list.itemWidget(item)
            if not isinstance(label, QLabel):
                continue
            selected = item.isSelected()
            background = THEME.selection_bg if selected else 'transparent'
            label.setStyleSheet(
                f'background-color: {background}; '
                f'selection-background-color: {THEME.text_selection_bg}; selection-color: {THEME.text_selection_text};'
            )
            font = label.font()
            font.setBold(selected)
            label.setFont(font)

    def _on_verse_label_context_menu(self, label: QLabel, pos):
        selected_text = label.selectedText().strip()
        # Selecting rich-text spanning HTML formatting (e.g. a red-letter/italic
        # boundary) can pick up stray whitespace/punctuation at the edges — trim to
        # the actual word(s) a dictionary headword would match against.
        word = selected_text.strip(' \t\n.,;:!?"\'()[]')
        verse: Verse = label._verse_row_item.data(Qt.ItemDataRole.UserRole)

        menu = QMenu(self)
        if word:
            lookup_action = menu.addAction(f'Look up “{word}” in Bible Dictionary')
            lookup_action.triggered.connect(lambda: self._show_dictionary_lookup(word))
        # Cross-references apply to the whole verse, not a selection, so this is
        # always offered — unlike the dictionary lookup above.
        tsk_action = menu.addAction('Cross-References (TSK)')
        tsk_action.triggered.connect(lambda: self._show_tsk_lookup(verse))
        menu.exec(label.mapToGlobal(pos))

    def _show_dictionary_lookup(self, word: str):
        definition = bible_dictionary.lookup(word)
        strongs_entries = strongs_dictionary.lookup(word)

        dialog = QDialog(self)
        dialog.setWindowTitle('Bible Dictionary')
        dialog.setMinimumWidth(440)
        dialog.setMaximumWidth(560)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(theme.SPACE_6, theme.SPACE_6, theme.SPACE_6, theme.SPACE_6)
        layout.setSpacing(theme.SPACE_3)

        headword = QLabel(html.escape(word))
        headword.setStyleSheet(f'font-family: {theme.FONT_HEADING}; font-weight: 600; font-size: 20px; color: {THEME.text};')
        layout.addWidget(headword)

        body_text = self._build_dictionary_lookup_html(word, definition, strongs_entries)
        body = QLabel(body_text)
        body.setWordWrap(True)
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet(
            f'color: {THEME.text}; font-size: 13px; line-height: 150%; '
            f'selection-background-color: {THEME.text_selection_bg}; selection-color: {THEME.text_selection_text};'
        )

        # Combined entries (Easton's plus several Strong's numbers) can run long —
        # cap the dialog's growth with a scroll area rather than letting it stretch
        # past the screen.
        scroll = QScrollArea()
        scroll.setWidget(body)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMaximumHeight(420)
        scroll.setStyleSheet(THEME.scroll_area_style)
        layout.addWidget(scroll)

        close_button = QPushButton('Close')
        close_button.setStyleSheet(THEME.primary_button_style)
        close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignRight)

        dialog.setStyleSheet(f'QDialog {{ background: {THEME.bg}; }}')
        dialog.exec()

    def _build_dictionary_lookup_html(self, word: str, definition: str | None, strongs_entries: list[dict]) -> str:
        kicker_style = f'color: {THEME.accent_700}; font-size: 11px; letter-spacing: 1px;'
        sections = []

        sections.append(f'<div style="{kicker_style}"><b>EASTON\'S BIBLE DICTIONARY</b></div>')
        if definition:
            sections.append(f'<div style="margin-top:4px;">{html.escape(definition)}</div>')
        else:
            sections.append(
                f'<div style="margin-top:4px; color:{THEME.text_muted};">No entry found for “{html.escape(word)}”.</div>'
            )

        if strongs_entries:
            sections.append(f'<div style="{kicker_style} margin-top:16px;"><b>STRONG\'S CONCORDANCE</b></div>')
            for entry in strongs_entries:
                language = 'Greek' if entry['number'][0] == 'G' else 'Hebrew'
                heading = f"{entry['number']} · {entry['lemma']} ({entry['translit']}) — {language}"
                sections.append(
                    f'<div style="margin-top:10px;">{html.escape(heading)}</div>'
                    f'<div>{html.escape(entry["def"])}</div>'
                )

        return ''.join(sections)

    def _show_tsk_lookup(self, verse: Verse):
        references = tsk_dictionary.lookup(verse.book, verse.chapter, verse.verse)

        dialog = QDialog(self)
        dialog.setWindowTitle('Cross-References')
        dialog.setMinimumWidth(560)
        dialog.setMaximumWidth(640)
        outer_layout = QVBoxLayout(dialog)
        outer_layout.setContentsMargins(theme.SPACE_6, theme.SPACE_6, theme.SPACE_6, theme.SPACE_6)
        outer_layout.setSpacing(theme.SPACE_3)

        heading = QLabel(html.escape(f'{verse.book} {verse.chapter}:{verse.verse}'))
        heading.setStyleSheet(f'font-family: {theme.FONT_HEADING}; font-weight: 600; font-size: 20px; color: {THEME.text};')
        outer_layout.addWidget(heading)

        columns = QHBoxLayout()
        columns.setSpacing(theme.SPACE_4)

        body = QLabel(self._build_tsk_lookup_html(verse, references))
        body.setWordWrap(True)
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        body.setOpenExternalLinks(False)
        body.linkActivated.connect(lambda href: self._on_tsk_reference_clicked(href, dialog))
        body.linkHovered.connect(self._on_tsk_reference_hovered)
        body.setStyleSheet(f'color: {THEME.text}; font-size: 13px; line-height: 150%;')

        list_scroll = QScrollArea()
        list_scroll.setWidget(body)
        list_scroll.setWidgetResizable(True)
        list_scroll.setFrameShape(QFrame.Shape.NoFrame)
        list_scroll.setFixedWidth(160)
        list_scroll.setMaximumHeight(280)
        list_scroll.setStyleSheet(THEME.scroll_area_style)
        columns.addWidget(list_scroll)

        preview_frame = QFrame()
        preview_frame.setObjectName('tskPreviewFrame')
        preview_frame.setStyleSheet(
            f'#tskPreviewFrame {{ background: {THEME.surface}; border: 1px solid {THEME.divider}; border-radius: 8px; }}'
        )
        preview_layout = QVBoxLayout(preview_frame)
        preview_layout.setContentsMargins(16, 14, 16, 14)
        preview_layout.setSpacing(6)

        self._tsk_preview_kicker = QLabel('HOVER A REFERENCE TO PREVIEW IT')
        self._tsk_preview_kicker.setWordWrap(True)
        self._tsk_preview_kicker.setStyleSheet(
            f'color: {THEME.accent_700}; font-size: 10px; font-weight: 600; letter-spacing: 1px;'
        )
        preview_layout.addWidget(self._tsk_preview_kicker)

        self._tsk_preview_text = QLabel('')
        self._tsk_preview_text.setWordWrap(True)
        self._tsk_preview_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._tsk_preview_text.setStyleSheet(f'color: {THEME.text}; font-size: 13px; line-height: 150%;')
        preview_layout.addWidget(self._tsk_preview_text)
        preview_layout.addStretch()

        columns.addWidget(preview_frame, stretch=1)
        outer_layout.addLayout(columns)

        close_button = QPushButton('Close')
        close_button.setStyleSheet(THEME.primary_button_style)
        close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        close_button.clicked.connect(dialog.accept)
        outer_layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignRight)

        dialog.setStyleSheet(f'QDialog {{ background: {THEME.bg}; }}')
        dialog.exec()

    def _build_tsk_lookup_html(self, verse: Verse, references: list[str]) -> str:
        if not references:
            label = html.escape(f'{verse.book} {verse.chapter}:{verse.verse}')
            return f'<div style="color:{THEME.text_muted};">No cross-references found for “{label}”.</div>'
        sections = []
        for ref in references:
            escaped = html.escape(ref)
            sections.append(
                f'<div style="margin-top:6px;">'
                f'<a href="{escaped}" style="color:{THEME.accent}; text-decoration:none;">{escaped}</a>'
                f'</div>'
            )
        return ''.join(sections)

    def _on_tsk_reference_clicked(self, href: str, dialog: QDialog):
        dialog.accept()
        # TSK reference text is English-only (e.g. "Deuteronomy 32:4"), so cross-reference
        # navigation always resolves and displays against the KJV, regardless of the
        # active translation — matches the hover preview, and avoids either failing to
        # parse the reference or showing the wrong book against a non-English bible.
        self._set_version(self._english_version_name())
        ranges = self.bible.parse_reference(href)
        if not ranges:
            return
        book, chapter, start_verse, end_verse = ranges[0]
        self._results_sync_timer.stop()
        verse_count = self.bible.verse_count(book, chapter)
        verses = self.bible.get_verses(book, chapter, 1, verse_count) if verse_count else []
        self.book_combo.blockSignals(True)
        self.book_combo.setCurrentText(book)
        self.book_combo.blockSignals(False)
        self.chapter_spin.blockSignals(True)
        self.chapter_spin.setValue(chapter)
        self.chapter_spin.blockSignals(False)
        self._populate_results(
            verses, select_ranges=[(start_verse, end_verse)], title=f'{book} {chapter}', is_search_mode=False,
        )

    def _on_tsk_reference_hovered(self, href: str):
        if not href:
            self._tsk_preview_kicker.setText('HOVER A REFERENCE TO PREVIEW IT')
            self._tsk_preview_text.setText('')
            return
        # TSK cross-reference book names/text are English-only, so the preview always
        # resolves against the KJV regardless of the active translation — otherwise
        # switching to e.g. Tagalog either fails to parse the reference (different book
        # names) or renders garbled/escaped text from a non-English bible.
        preview_bible = self._english_bible()
        ranges = preview_bible.parse_reference(href)
        if not ranges:
            return
        book, chapter, start_verse, end_verse = ranges[0]
        verses = preview_bible.get_verses(book, chapter, start_verse, end_verse)
        if not verses:
            return
        preview = ' '.join(v.text.strip() for v in verses)
        self._tsk_preview_kicker.setText(html.escape(href).upper())
        self._tsk_preview_text.setText(html.escape(preview))

    def _english_version_name(self) -> str:
        for name in self.bibles:
            if _is_kjv(name):
                return name
        return self.version_name

    def _english_bible(self) -> Bible:
        return self.bibles[self._english_version_name()]

    def _format_verse_html(self, v: Verse, highlight_words: list[str] | None) -> str:
        if self._results_is_search_mode:
            prefix = html.escape(f'{v.book} {v.chapter}:{v.verse}  ')
        else:
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
        return f'<b style="color:{THEME.accent_700};">{prefix}</b>{text}'

    def _on_selection_changed(self):
        selected = self.results_list.selectedItems()
        self.current_verses = [item.data(Qt.ItemDataRole.UserRole) for item in selected]
        self._refresh_selection_highlight()
        self.add_to_service_button.setEnabled(bool(self.current_verses))
        # Suppressed while _sync_results_selection_to_display() is re-selecting rows
        # to follow Previous/Next/verse-jump navigation — that navigation already
        # happened inside self.display itself, so pushing show_verses() back at it
        # here would reset its page position (show_verses() always starts at page 0)
        # instead of leaving whatever page it actually landed on alone.
        if self.current_verses and not self._suppress_display_sync:
            self.display.show_verses(self.current_verses)

    def _on_show_display_clicked(self):
        screens = available_screens()
        index = self.screen_combo.currentIndex()
        if 0 <= index < len(screens):
            self.display.show_on_screen(screens[index])

    def _on_send_clicked(self):
        if self.current_verses:
            self.display.show_verses(self.current_verses)

    def _update_identity(self):
        """Refreshes the identity strip's logo/church name from IDENTITY.config, both at
        startup and live after IDENTITY.changed fires (the user picked a new logo/name
        in Display Settings)."""
        config = IDENTITY.config
        logo_path = Path(config.logo_path) if config.logo_path else DEFAULT_LOGO_PATH
        if not logo_path.exists():
            logo_path = DEFAULT_LOGO_PATH
        logo_pixmap = _circular_pixmap(logo_path, 40)
        self.identity_logo_label.setPixmap(logo_pixmap if not logo_pixmap.isNull() else QPixmap())
        self.identity_name_label.setText(config.church_name or identity.DEFAULT_CHURCH_NAME)

    def _apply_theme(self):
        """Re-applies every color-derived stylesheet after THEME.changed fires (the user
        picked new colors in Display Settings), so the running window updates live
        instead of requiring a restart. Mirrors the styling calls made when each widget
        was first built in the _build_*/_make_card methods above."""
        self.setStyleSheet(f'QMainWindow {{ background: {THEME.bg}; }} QWidget {{ color: {THEME.text}; }}')

        self.identity_strip.setStyleSheet(f'background: {THEME.surface};')
        self.identity_name_label.setStyleSheet(f'font-family: {theme.FONT_HEADING}; font-weight: 600; font-size: 16px;')
        self.identity_subtitle_label.setStyleSheet(f'font-size: 10px; letter-spacing: 1px; color: {THEME.text_muted};')
        self.version_combo.setStyleSheet(THEME.combo_style)
        self.screen_combo.setStyleSheet(THEME.combo_style)
        self.show_display_button.setStyleSheet(THEME.primary_button_style)
        self.settings_button.setStyleSheet(THEME.ghost_button_style)

        for card in self._cards:
            card.setStyleSheet(THEME.card_style)
        for kicker_label in self._kicker_labels:
            kicker_label.setStyleSheet(THEME.kicker_style)

        self.search_edit.setStyleSheet(THEME.input_style)
        self.search_button.setStyleSheet(THEME.button_style)
        self.book_label.setStyleSheet(f'font-size: 12px; color: {THEME.text_muted};')
        self.book_combo.setStyleSheet(THEME.combo_style)
        self.chapter_label.setStyleSheet(f'font-size: 12px; color: {THEME.text_muted};')
        self.chapter_spin.setStyleSheet(THEME.spinbox_style)
        self.results_title_label.setStyleSheet(f'font-family: {theme.FONT_HEADING}; font-weight: 600; font-size: 15px;')
        self.back_to_browse_button.setStyleSheet(THEME.ghost_button_style)
        self.results_count_label.setStyleSheet(f'font-size: 11px; color: {THEME.text_muted};')
        self.results_list.setStyleSheet(THEME.results_list_style)

        self.live_view_frame.setStyleSheet(f'background: {THEME.display_bg}; border: 1px solid {THEME.divider};')
        self.desktop_button.setStyleSheet(THEME.button_style)
        self.clear_button.setStyleSheet(THEME.danger_button_style)
        self.prev_page_button.setStyleSheet(THEME.flat_nav_button_style)
        self.next_page_button.setStyleSheet(THEME.flat_nav_button_style)
        self.prev_page_button.setIcon(theme.lucide_icon('chevron-left', THEME.text))
        self.next_page_button.setIcon(theme.lucide_icon('chevron-right', THEME.text))
        self.page_indicator_label.setStyleSheet(f'color: {THEME.text_muted}; font-size: 12px;')
        self._on_output_screen_changed()

        self.service_list.setStyleSheet(THEME.service_list_style)
        self.clear_service_button.setStyleSheet(THEME.ghost_button_style)
        self.add_to_service_button.setStyleSheet(THEME.button_style)
        self._populate_service_list()

        # The verse number's accent color is baked directly into each result row's HTML
        # (not CSS), so a stylesheet re-apply alone won't pick up an accent change —
        # re-render the currently visible rows' text to match.
        for i in range(self.results_list.count()):
            item = self.results_list.item(i)
            label = self.results_list.itemWidget(item)
            verse = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(label, QLabel) and verse is not None:
                label.setText(self._format_verse_html(verse, None))
        self._refresh_selection_highlight()

    def closeEvent(self, event):
        self.display.close()
        self.preview_display.close()
        super().closeEvent(event)
