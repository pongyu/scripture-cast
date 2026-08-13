"""Popup dialog for adjusting display text size, with a live preview shaped like the target screen."""
import re
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QScreen
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit,
    QPushButton, QSlider, QTabWidget, QVBoxLayout, QWidget,
)

from config import DisplayConfig
from display_window import (
    DisplayWindow, SAMPLE_VERSE_BOOK, SAMPLE_VERSE_CHAPTER, SAMPLE_VERSE_NUMBER, SAMPLE_VERSE_REF, SAMPLE_VERSE_TEXT,
    _apply_verse_html, verse_label_style,
)
from identity import Identity, IdentityConfig, store_logo
from keybindings import ACTIONS, KeyBindings
import red_letter
import supplied_words
from theme import Theme, ThemeConfig

# Slider goes from 0 (Small) to 100 (Large); these map to the underlying text_size_percent range.
MIN_TEXT_SIZE_PERCENT = 3.0
MAX_TEXT_SIZE_PERCENT = 18.0
PREVIEW_BOX_WIDTH = 360


class SettingsDialog(QDialog):
    bindings_changed = Signal(KeyBindings)

    def __init__(
        self, display: DisplayWindow, preview_display: DisplayWindow, screen: QScreen | None, theme: Theme,
        identity: Identity, keybindings: KeyBindings, parent=None,
    ):
        super().__init__(parent)
        self.display = display
        self.preview_display = preview_display
        self.screen = screen
        self.theme = theme
        self.identity = identity
        self.keybindings = keybindings

        self.setWindowTitle('Display Settings')
        self._swatch_buttons: dict[str, QPushButton] = {}
        self._keybinding_edits: dict[str, QKeySequenceEdit] = {}

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._build_display_tab(), 'Display')
        tabs.addTab(self._build_identity_tab(), 'Identity')
        tabs.addTab(self._build_appearance_tab(), 'Appearance')
        tabs.addTab(self._build_shortcuts_tab(), 'Shortcuts')
        tabs.addTab(self._build_help_tab(), 'Help')
        layout.addWidget(tabs)

        close_button = QPushButton('Close')
        close_button.setStyleSheet(self.theme.primary_button_style)
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignRight)

        self._update_swatch_buttons()
        self._update_preview()

    def _build_display_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        slider_row = QHBoxLayout()
        slider_row.addWidget(QLabel('Text Size:'))
        slider_row.addWidget(QLabel('Small'))
        self.text_size_slider = QSlider(Qt.Orientation.Horizontal)
        self.text_size_slider.setStyleSheet(self.theme.slider_style)
        self.text_size_slider.setRange(0, 100)
        self.text_size_slider.setValue(self._percent_to_slider(self.display.config.text_size_percent))
        self.text_size_slider.setToolTip('How large the verse text appears on the display screen')
        self.text_size_slider.valueChanged.connect(self._on_text_size_changed)
        slider_row.addWidget(self.text_size_slider)
        slider_row.addWidget(QLabel('Large'))
        layout.addLayout(slider_row)

        self.maximize_checkbox = QCheckBox('Maximize text (shrink reference to a small corner label)')
        self.maximize_checkbox.setStyleSheet(self.theme.checkbox_style)
        self.maximize_checkbox.setToolTip(
            'Frees up the row normally used by the reference line for larger verse text.\n'
            'Useful on a physically small screen (e.g. a 42" TV) where every row of height counts.'
        )
        self.maximize_checkbox.setChecked(self.display.config.maximize_text)
        self.maximize_checkbox.toggled.connect(self._on_config_toggled)
        layout.addWidget(self.maximize_checkbox)

        self.verse_numbers_checkbox = QCheckBox('Show verse numbers')
        self.verse_numbers_checkbox.setStyleSheet(self.theme.checkbox_style)
        self.verse_numbers_checkbox.setToolTip(
            'Shows a small number before each verse, like a printed Bible.'
        )
        self.verse_numbers_checkbox.setChecked(self.display.config.show_verse_numbers)
        self.verse_numbers_checkbox.toggled.connect(self._on_config_toggled)
        layout.addWidget(self.verse_numbers_checkbox)

        self.red_letter_checkbox = QCheckBox('Red letter (words of Christ in red)')
        self.red_letter_checkbox.setStyleSheet(self.theme.checkbox_style)
        self.red_letter_checkbox.setToolTip(
            'Shows Jesus\' spoken words in red, like a traditional red-letter Bible.\n'
            'Only has an effect while the King James Version is the active translation.'
        )
        self.red_letter_checkbox.setChecked(self.display.config.red_letter)
        self.red_letter_checkbox.toggled.connect(self._on_config_toggled)
        layout.addWidget(self.red_letter_checkbox)

        self.supplied_words_checkbox = QCheckBox('Italicize translator-supplied words')
        self.supplied_words_checkbox.setStyleSheet(self.theme.checkbox_style)
        self.supplied_words_checkbox.setToolTip(
            'Shows words the KJV translators added for English readability, with no direct\n'
            'equivalent in the original Hebrew/Greek, in italics — a traditional KJV convention.\n'
            'Only has an effect while the King James Version is the active translation.'
        )
        self.supplied_words_checkbox.setChecked(self.display.config.supplied_words_italic)
        self.supplied_words_checkbox.toggled.connect(self._on_config_toggled)
        layout.addWidget(self.supplied_words_checkbox)

        layout.addWidget(QLabel('Preview — matches the selected display screen\'s shape:'))
        self.preview_box = QWidget()
        self.preview_box.setFixedWidth(PREVIEW_BOX_WIDTH)
        self.preview_label = QLabel(self.preview_box)
        self.preview_label.setWordWrap(True)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setTextFormat(Qt.TextFormat.RichText)
        self.preview_ref_label = QLabel(self.preview_box)
        self.preview_ref_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.preview_box, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch()
        return tab

    def _build_identity_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        layout.addWidget(QLabel('Shown in the control panel header:'))
        church_name_row = QHBoxLayout()
        church_name_row.addWidget(QLabel('Church name:'))
        self.church_name_edit = QLineEdit(self.identity.config.church_name)
        self.church_name_edit.setStyleSheet(self.theme.input_style)
        self.church_name_edit.editingFinished.connect(self._on_church_name_changed)
        church_name_row.addWidget(self.church_name_edit, stretch=1)
        layout.addLayout(church_name_row)

        logo_row = QHBoxLayout()
        logo_row.addWidget(QLabel('Logo:'))
        choose_logo_button = QPushButton('Choose Logo...')
        choose_logo_button.setStyleSheet(self.theme.button_style)
        choose_logo_button.clicked.connect(self._on_choose_logo)
        logo_row.addWidget(choose_logo_button)
        reset_logo_button = QPushButton('Use default')
        reset_logo_button.setStyleSheet(self.theme.ghost_button_style)
        reset_logo_button.clicked.connect(self._on_reset_logo)
        logo_row.addWidget(reset_logo_button)
        logo_row.addStretch()
        layout.addLayout(logo_row)
        layout.addStretch()
        return tab

    def _build_appearance_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        layout.addWidget(QLabel('Control panel colors:'))
        appearance_row = QHBoxLayout()
        for field, label_text in (
            ('bg', 'Background'), ('surface', 'Surface'), ('text', 'Text'), ('accent', 'Accent'),
        ):
            appearance_row.addLayout(self._make_swatch_control(field, label_text))
        appearance_row.addStretch()
        reset_theme_button = QPushButton('Reset to default')
        reset_theme_button.setStyleSheet(self.theme.ghost_button_style)
        reset_theme_button.clicked.connect(self._on_reset_theme)
        appearance_row.addWidget(reset_theme_button, alignment=Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(appearance_row)

        layout.addWidget(QLabel('Display screen colors:'))
        display_appearance_row = QHBoxLayout()
        for field, label_text in (
            ('display_bg', 'Background'), ('display_text', 'Verse text'),
        ):
            display_appearance_row.addLayout(self._make_swatch_control(field, label_text))
        display_appearance_row.addStretch()
        layout.addLayout(display_appearance_row)
        layout.addStretch()
        return tab

    def _build_shortcuts_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(QLabel('Click a field, then press the key combination you want. Click "Clear" to unset.'))

        form = QFormLayout()
        for action, _default, label in ACTIONS:
            row = QHBoxLayout()
            edit = QKeySequenceEdit(QKeySequence(getattr(self.keybindings, action)))
            edit.setStyleSheet(self.theme.input_style)
            self._keybinding_edits[action] = edit
            row.addWidget(edit)
            clear_button = QPushButton('Clear')
            clear_button.setStyleSheet(self.theme.ghost_button_style)
            clear_button.clicked.connect(edit.clear)
            row.addWidget(clear_button)
            form.addRow(f'{label}:', row)
        layout.addLayout(form)

        save_button = QPushButton('Save Shortcuts')
        save_button.setStyleSheet(self.theme.primary_button_style)
        save_button.clicked.connect(self._on_save_shortcuts)
        layout.addWidget(save_button, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addStretch()
        return tab

    def _build_help_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(14)

        tips = [
            (
                'Bible Dictionary lookups',
                'In the results list, click and drag across a word to highlight it, then '
                'right-click the highlighted word and choose "Look up" to see its entry '
                'from Easton\'s Bible Dictionary (names, places, and terms — not every '
                'word has an entry).',
            ),
            (
                'Jump to a verse number',
                'With the results list showing a chapter, type a verse number and press '
                'Enter to jump straight to it — works even while the fullscreen display '
                'is active.',
            ),
            (
                'Service Plan',
                'Save passages ahead of a service with "+ Add to Service" so you can jump '
                'back to them later without searching again.',
            ),
        ]
        for title, body in tips:
            title_label = QLabel(title)
            title_label.setStyleSheet(f'font-weight: 600; color: {self.theme.text};')
            layout.addWidget(title_label)
            body_label = QLabel(body)
            body_label.setWordWrap(True)
            body_label.setStyleSheet(f'color: {self.theme.text_muted};')
            layout.addWidget(body_label)

        layout.addStretch()
        return tab

    @staticmethod
    def _percent_to_slider(percent: float) -> int:
        span = MAX_TEXT_SIZE_PERCENT - MIN_TEXT_SIZE_PERCENT
        return round((percent - MIN_TEXT_SIZE_PERCENT) / span * 100)

    @staticmethod
    def _slider_to_percent(value: int) -> float:
        span = MAX_TEXT_SIZE_PERCENT - MIN_TEXT_SIZE_PERCENT
        return MIN_TEXT_SIZE_PERCENT + value / 100 * span

    def _on_text_size_changed(self, slider_value: int):
        text_size_percent = self._slider_to_percent(slider_value)
        self._apply_config(text_size_percent=text_size_percent, ref_size_percent=text_size_percent * 0.5)

    def _on_config_toggled(self, _checked: bool):
        self._apply_config()

    def _on_save_shortcuts(self):
        bindings = KeyBindings(**{
            action: self._keybinding_edits[action].keySequence().toString()
            for action, _default, _label in ACTIONS
        })
        bindings.save()
        self.keybindings = bindings
        self.bindings_changed.emit(bindings)

    def _on_church_name_changed(self):
        new_config = replace(self.identity.config, church_name=self.church_name_edit.text().strip())
        self.identity.apply(new_config)
        new_config.save()

    def _on_choose_logo(self):
        path_str, _filter = QFileDialog.getOpenFileName(
            self, 'Choose Logo', '', 'Images (*.png *.jpg *.jpeg *.bmp *.svg)'
        )
        if not path_str:
            return
        stored_path = store_logo(Path(path_str))
        new_config = replace(self.identity.config, logo_path=stored_path)
        self.identity.apply(new_config)
        new_config.save()

    def _on_reset_logo(self):
        new_config = replace(self.identity.config, logo_path='')
        self.identity.apply(new_config)
        new_config.save()

    def _make_swatch_control(self, field: str, label_text: str) -> QVBoxLayout:
        swatch_box = QVBoxLayout()
        swatch_box.addWidget(QLabel(label_text))
        button = QPushButton()
        button.setFixedSize(48, 24)
        button.clicked.connect(lambda _checked=False, f=field: self._on_pick_color(f))
        swatch_box.addWidget(button)
        self._swatch_buttons[field] = button
        return swatch_box

    def _update_swatch_buttons(self):
        for field, button in self._swatch_buttons.items():
            color = getattr(self.theme.config, field)
            button.setStyleSheet(f'background-color: {color}; border: 1px solid #888;')

    def _on_pick_color(self, field: str):
        current = QColor(getattr(self.theme.config, field))
        color = QColorDialog.getColor(current, self, f'Choose {field} color')
        if not color.isValid():
            return
        new_config = replace(self.theme.config, **{field: color.name()})
        self.theme.apply(new_config)
        new_config.save()
        self._update_swatch_buttons()
        self._update_preview()

    def _on_reset_theme(self):
        default_config = ThemeConfig()
        self.theme.apply(default_config)
        default_config.save()
        self._update_swatch_buttons()
        self._update_preview()

    def _apply_config(self, text_size_percent: float | None = None, ref_size_percent: float | None = None):
        config = DisplayConfig(
            text_size_percent=text_size_percent if text_size_percent is not None else self.display.config.text_size_percent,
            ref_size_percent=ref_size_percent if ref_size_percent is not None else self.display.config.ref_size_percent,
            line_spacing_percent=self.display.config.line_spacing_percent,
            maximize_text=self.maximize_checkbox.isChecked(),
            show_verse_numbers=self.verse_numbers_checkbox.isChecked(),
            red_letter=self.red_letter_checkbox.isChecked(),
            supplied_words_italic=self.supplied_words_checkbox.isChecked(),
        )
        self.display.set_config(config)
        self.preview_display.set_config(config)
        config.save()
        self._update_preview()

    def _update_preview(self):
        if self.screen and self.screen.geometry().width() > 0:
            geometry = self.screen.geometry()
            aspect_ratio = geometry.height() / geometry.width()
            screen_height = geometry.height()
        else:
            aspect_ratio = 9 / 16
            screen_height = 1080
        preview_height = round(PREVIEW_BOX_WIDTH * aspect_ratio)
        self.preview_box.setFixedHeight(preview_height)
        self.preview_box.setStyleSheet(f'background-color: {self.theme.display_bg};')
        self.preview_label.setFixedWidth(PREVIEW_BOX_WIDTH)

        # Scale down by the same factor the real display is shrunk by, so the preview's
        # font size is proportionally identical to what will appear on the real screen.
        scale = preview_height / screen_height
        text_css, ref_css, _ = verse_label_style(self.display.config, height=screen_height)
        text_css = self._scale_css_pixels(text_css, scale)
        ref_css = self._scale_css_pixels(ref_css, scale)
        self.preview_label.setStyleSheet(text_css)
        sample_len = len(SAMPLE_VERSE_TEXT)
        red_ranges = (
            red_letter.red_ranges(SAMPLE_VERSE_BOOK, SAMPLE_VERSE_CHAPTER, SAMPLE_VERSE_NUMBER, sample_len)
            if self.display.config.red_letter else []
        )
        italic_ranges = (
            supplied_words.supplied_ranges(SAMPLE_VERSE_BOOK, SAMPLE_VERSE_CHAPTER, SAMPLE_VERSE_NUMBER, sample_len)
            if self.display.config.supplied_words_italic else []
        )
        body = _apply_verse_html(SAMPLE_VERSE_TEXT, red_ranges, italic_ranges)
        if self.display.config.show_verse_numbers:
            self.preview_label.setText(f'<sup>{SAMPLE_VERSE_NUMBER}</sup>&nbsp;{body}')
        else:
            self.preview_label.setText(body)
        self.preview_ref_label.setStyleSheet(ref_css)
        self.preview_ref_label.setText(SAMPLE_VERSE_REF)

        if self.display.config.maximize_text:
            self.preview_label.setGeometry(0, 0, PREVIEW_BOX_WIDTH, preview_height)
            self.preview_ref_label.adjustSize()
            margin = round(preview_height * 0.02)
            x = PREVIEW_BOX_WIDTH - self.preview_ref_label.width() - margin
            y = preview_height - self.preview_ref_label.height() - margin
            self.preview_ref_label.move(max(x, 0), max(y, 0))
        else:
            ref_height = self.preview_ref_label.sizeHint().height()
            self.preview_label.setGeometry(0, 0, PREVIEW_BOX_WIDTH, preview_height - ref_height)
            self.preview_ref_label.setGeometry(0, preview_height - ref_height, PREVIEW_BOX_WIDTH, ref_height)
            self.preview_ref_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_ref_label.raise_()

    @staticmethod
    def _scale_css_pixels(css: str, scale: float) -> str:
        """Scale every '<n>px' value in a CSS string by `scale`, rounding to whole pixels."""
        return re.sub(r'(\d+)px', lambda m: f'{max(round(int(m.group(1)) * scale), 1)}px', css)
