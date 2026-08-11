"""Control panel: browse or search verses, send selection to the display window."""
import html
import re
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt
from PySide6.QtGui import QFont, QFontMetrics, QKeySequence, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QPushButton, QSizePolicy, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

import identity
import red_letter
import supplied_words
import theme
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

    def _build_on_display_card(self) -> QFrame:
        card, layout = self._make_card('On Display')

        header = QHBoxLayout()
        self.output_tag_label = QLabel()
        header.addStretch()
        header.addWidget(self.output_tag_label)
        layout.insertLayout(1, header)

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

        button_grid = QVBoxLayout()
        top_row = QHBoxLayout()
        self.show_display_button = QPushButton('Show Display Window')
        self.show_display_button.setStyleSheet(THEME.primary_button_style)
        self.show_display_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.show_display_button.setToolTip('Same as the Shift+Enter shortcut — bring the display window to the front.')
        self.show_display_button.clicked.connect(self._on_show_display_clicked)
        top_row.addWidget(self.show_display_button)

        self.desktop_button = QPushButton('Show Desktop')
        self.desktop_button.setStyleSheet(THEME.button_style)
        self.desktop_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.desktop_button.setCheckable(True)
        self.desktop_button.setToolTip('Hide the display window entirely, revealing the desktop underneath')
        self.desktop_button.clicked.connect(self.display.set_showing_desktop)
        self.display.desktop_shown_changed.connect(self.desktop_button.setChecked)
        top_row.addWidget(self.desktop_button)
        button_grid.addLayout(top_row)

        self.clear_button = QPushButton('Clear (Reset)')
        self.clear_button.setStyleSheet(THEME.danger_button_style)
        self.clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_button.setToolTip(
            'Wipe the loaded verse completely. There is nothing to resume — '
            'select or search again to show something new.'
        )
        self.clear_button.clicked.connect(self.display.clear)
        button_grid.addWidget(self.clear_button)

        layout.addLayout(button_grid)

        page_nav_row = QHBoxLayout()
        self.prev_page_button = QPushButton(' Previous')
        self.prev_page_button.setStyleSheet(THEME.ghost_button_style)
        self.prev_page_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.prev_page_button.setIcon(theme.lucide_icon('chevron-left', THEME.text))
        self.prev_page_button.setToolTip(
            'Same as the Left Arrow/Page Up key on the display.\n'
            'Steps back within a long verse split across screens; at the start of the\n'
            'current selection, moves to the previous verse in the Bible instead.'
        )
        self.prev_page_button.clicked.connect(self.display.previous_page)
        page_nav_row.addWidget(self.prev_page_button)

        page_nav_row.addStretch()

        self.page_indicator_label = QLabel()
        self.page_indicator_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        page_nav_row.addWidget(self.page_indicator_label)

        page_nav_row.addStretch()

        self.next_page_button = QPushButton('Next ')
        self.next_page_button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.next_page_button.setStyleSheet(THEME.ghost_button_style)
        self.next_page_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_page_button.setIcon(theme.lucide_icon('chevron-right', THEME.text))
        self.next_page_button.setToolTip(
            'Same as the Right Arrow/Space/Page Down key on the display.\n'
            'Steps forward within a long verse split across screens; at the end of the\n'
            'current selection, moves to the next verse in the Bible instead.'
        )
        self.next_page_button.clicked.connect(self.display.next_page)
        page_nav_row.addWidget(self.next_page_button)
        layout.addLayout(page_nav_row)

        self.display.page_changed.connect(self._on_page_changed)
        self._on_page_changed(0, 0)

        layout.addStretch()
        self._update_output_tag()
        return card

    def _build_service_card(self) -> QFrame:
        card, layout = self._make_card('Service Plan')

        self.service_list = QListWidget()
        self.service_list.setStyleSheet(THEME.service_list_style)
        self.service_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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

    def _update_output_tag(self):
        self.output_tag_label.setText(f'Output: Screen {self.screen_combo.currentIndex()}'.upper())
        self.output_tag_label.setStyleSheet(THEME.tag_outline_style)
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
        self.version_combo.addItems(list(self.bibles.keys()))
        self.version_combo.setCurrentText(self.version_name)
        self.version_combo.currentTextChanged.connect(self._on_version_combo_changed)
        row.addWidget(self.version_combo)

        self.screen_combo = QComboBox()
        for i, screen in enumerate(available_screens()):
            self.screen_combo.addItem(f'{i}: {screen.name()} ({screen.geometry().width()}x{screen.geometry().height()})')
        self.screen_combo.currentIndexChanged.connect(lambda _: self._update_output_tag())
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
            item.setSizeHint(self._result_item_size_hint(label))
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
        return f'<b style="color:{THEME.accent_700};">{prefix}</b>{text}'

    def _on_selection_changed(self):
        selected = self.results_list.selectedItems()
        self.current_verses = [item.data(Qt.ItemDataRole.UserRole) for item in selected]
        self._refresh_selection_highlight()
        self.add_to_service_button.setEnabled(bool(self.current_verses))
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
        self.prev_page_button.setStyleSheet(THEME.ghost_button_style)
        self.next_page_button.setStyleSheet(THEME.ghost_button_style)
        self.prev_page_button.setIcon(theme.lucide_icon('chevron-left', THEME.text))
        self.next_page_button.setIcon(theme.lucide_icon('chevron-right', THEME.text))
        self.page_indicator_label.setStyleSheet(f'color: {THEME.text_muted}; font-size: 12px;')
        self._update_output_tag()

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
