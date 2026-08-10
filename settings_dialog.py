"""Popup dialog for adjusting display text size, with a live preview shaped like the target screen."""
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QScreen
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from config import DisplayConfig
from display_window import (
    DisplayWindow, SAMPLE_VERSE_NUMBER, SAMPLE_VERSE_REF, SAMPLE_VERSE_TEXT, _apply_verse_html, verse_label_style,
)
import red_letter
import supplied_words

# Slider goes from 0 (Small) to 100 (Large); these map to the underlying text_size_percent range.
MIN_TEXT_SIZE_PERCENT = 3.0
MAX_TEXT_SIZE_PERCENT = 18.0
PREVIEW_BOX_WIDTH = 360


class SettingsDialog(QDialog):
    def __init__(self, display: DisplayWindow, screen: QScreen | None, parent=None):
        super().__init__(parent)
        self.display = display
        self.screen = screen

        self.setWindowTitle('Display Settings')
        layout = QVBoxLayout(self)

        slider_row = QHBoxLayout()
        slider_row.addWidget(QLabel('Text Size:'))
        slider_row.addWidget(QLabel('Small'))
        self.text_size_slider = QSlider(Qt.Orientation.Horizontal)
        self.text_size_slider.setRange(0, 100)
        self.text_size_slider.setValue(self._percent_to_slider(self.display.config.text_size_percent))
        self.text_size_slider.setToolTip('How large the verse text appears on the display screen')
        self.text_size_slider.valueChanged.connect(self._on_text_size_changed)
        slider_row.addWidget(self.text_size_slider)
        slider_row.addWidget(QLabel('Large'))
        layout.addLayout(slider_row)

        self.maximize_checkbox = QCheckBox('Maximize text (shrink reference to a small corner label)')
        self.maximize_checkbox.setToolTip(
            'Frees up the row normally used by the reference line for larger verse text.\n'
            'Useful on a physically small screen (e.g. a 42" TV) where every row of height counts.'
        )
        self.maximize_checkbox.setChecked(self.display.config.maximize_text)
        self.maximize_checkbox.toggled.connect(self._on_config_toggled)
        layout.addWidget(self.maximize_checkbox)

        self.verse_numbers_checkbox = QCheckBox('Show verse numbers')
        self.verse_numbers_checkbox.setToolTip(
            'Shows a small number before each verse, like a printed Bible.'
        )
        self.verse_numbers_checkbox.setChecked(self.display.config.show_verse_numbers)
        self.verse_numbers_checkbox.toggled.connect(self._on_config_toggled)
        layout.addWidget(self.verse_numbers_checkbox)

        self.red_letter_checkbox = QCheckBox('Red letter (words of Christ in red)')
        self.red_letter_checkbox.setToolTip(
            'Shows Jesus\' spoken words in red, like a traditional red-letter Bible.\n'
            'Only has an effect while the King James Version is the active translation.'
        )
        self.red_letter_checkbox.setChecked(self.display.config.red_letter)
        self.red_letter_checkbox.toggled.connect(self._on_config_toggled)
        layout.addWidget(self.red_letter_checkbox)

        self.supplied_words_checkbox = QCheckBox('Italicize translator-supplied words')
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
        self.preview_box.setStyleSheet('background-color: black;')
        self.preview_label = QLabel(self.preview_box)
        self.preview_label.setWordWrap(True)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setTextFormat(Qt.TextFormat.RichText)
        self.preview_ref_label = QLabel(self.preview_box)
        self.preview_ref_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.preview_box, alignment=Qt.AlignmentFlag.AlignHCenter)

        close_button = QPushButton('Close')
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignRight)

        self._update_preview()

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
        self.preview_label.setFixedWidth(PREVIEW_BOX_WIDTH)

        # Scale down by the same factor the real display is shrunk by, so the preview's
        # font size is proportionally identical to what will appear on the real screen.
        scale = preview_height / screen_height
        text_css, ref_css, _ = verse_label_style(self.display.config, height=screen_height)
        text_css = self._scale_css_pixels(text_css, scale)
        ref_css = self._scale_css_pixels(ref_css, scale)
        self.preview_label.setStyleSheet(text_css)
        sample_len = len(SAMPLE_VERSE_TEXT.rstrip('.'))
        red_ranges = red_letter.red_ranges('John', 3, 16, sample_len) if self.display.config.red_letter else []
        italic_ranges = (
            supplied_words.supplied_ranges('John', 3, 16, sample_len)
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
