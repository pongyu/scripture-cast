"""Visual constants for the control panel, translated from the "Industry" design
system's tokens (blueprint/wireframe cards, muted blue accent, Barlow fonts) into
Qt stylesheet values. Light theme only.

The base palette (bg/surface/text/accent) is user-configurable at runtime via
ThemeConfig — see settings_dialog.py's Appearance section. Everything else (shades,
QSS strings) is derived from those four colors by the Theme class and recomputed
whenever the config changes, so already-built widgets can be restyled live.
"""
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor

# When frozen by PyInstaller, bundled data files are unpacked to sys._MEIPASS at
# runtime; when running from source, they live next to this script (mirrors app.py).
APP_DIR = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
_FONTS_DIR = APP_DIR / 'resources' / 'fonts'

CONFIG_PATH = Path.home() / 'AppData' / 'Roaming' / 'bible-presenter' / 'theme_config.json'

FONT_HEADING = '"Barlow Condensed", "Segoe UI Semibold", "Segoe UI", sans-serif'
FONT_BODY = '"Barlow", "Segoe UI", sans-serif'

_FONT_FILES = [
    'Barlow-Regular.ttf',
    'Barlow-Medium.ttf',
    'Barlow-Bold.ttf',
    'BarlowCondensed-Regular.ttf',
    'BarlowCondensed-SemiBold.ttf',
]


def load_fonts():
    """Registers the bundled Barlow/Barlow Condensed TTFs with Qt's font database, so
    FONT_HEADING/FONT_BODY resolve to the real typefaces instead of silently falling
    back to Segoe UI. Must run after a QApplication exists; safe to call more than
    once (Qt just re-registers the same families)."""
    from PySide6.QtGui import QFontDatabase
    for filename in _FONT_FILES:
        path = _FONTS_DIR / filename
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))

SPACE_2 = 7
SPACE_3 = 10
SPACE_4 = 14
SPACE_6 = 20

_ICON_CACHE_DIR = Path.home() / 'AppData' / 'Roaming' / 'bible-presenter' / 'icons'


def _chevron_file_url(color: str, direction: str = 'down') -> str:
    """Renders a small flat chevron to a cached PNG file and returns a plain path to it
    for use in QSS url(). Qt's QSS image loader silently fails (blank icon, no error) on
    both data: URIs and file:// URIs — it wants a plain filesystem path — so the icon is
    written to disk once and referenced by its raw path."""
    _ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _ICON_CACHE_DIR / f'chevron-{direction}-{color.lstrip("#")}.png'
    if not path.exists():
        from PySide6.QtCore import QPointF, Qt as _Qt
        from PySide6.QtGui import QImage, QPainter, QPainterPath, QPen

        scale = 4  # supersample then let Qt scale down, for a crisp look at small sizes
        w, h = 10 * scale, 6 * scale
        image = QImage(w, h, QImage.Format.Format_ARGB32)
        image.fill(_Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color))
        pen.setWidthF(1.6 * scale)
        pen.setCapStyle(_Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(_Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        arrow_path = QPainterPath()
        if direction == 'down':
            arrow_path.moveTo(QPointF(1 * scale, 1 * scale))
            arrow_path.lineTo(QPointF(5 * scale, 5 * scale))
            arrow_path.lineTo(QPointF(9 * scale, 1 * scale))
        else:
            arrow_path.moveTo(QPointF(1 * scale, 5 * scale))
            arrow_path.lineTo(QPointF(5 * scale, 1 * scale))
            arrow_path.lineTo(QPointF(9 * scale, 5 * scale))
        painter.drawPath(arrow_path)
        painter.end()
        image.save(str(path), 'PNG')
    return path.as_posix()


@dataclass
class ThemeConfig:
    bg: str = '#f2f2f3'
    surface: str = '#e9e9ea'
    text: str = '#1d1f20'
    accent: str = '#5980a6'
    display_bg: str = '#000000'
    display_text: str = '#ffffff'

    @classmethod
    def load(cls) -> 'ThemeConfig':
        try:
            data = json.loads(CONFIG_PATH.read_text())
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            return cls()

    def save(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(asdict(self), indent=2))


def _mix(color: str, target: str, amount: float) -> str:
    """Blends `color` toward `target` by `amount` (0-1); used to derive tints/shades
    (e.g. lighter/darker accent variants) from a single user-picked base color."""
    a, b = QColor(color), QColor(target)
    r = round(a.red() + (b.red() - a.red()) * amount)
    g = round(a.green() + (b.green() - a.green()) * amount)
    bl = round(a.blue() + (b.blue() - a.blue()) * amount)
    return QColor(r, g, bl).name()


class Theme(QObject):
    """Holds the live, derived color palette and the QSS strings built from it.
    Widgets read colors/styles off this object (e.g. `theme.instance.card_style`);
    calling apply() with a new ThemeConfig recomputes everything and emits `changed`
    so already-built widgets can restyle themselves without recreating the window."""

    changed = Signal()

    def __init__(self, config: ThemeConfig):
        super().__init__()
        self.config = config
        self._recompute()

    def apply(self, config: ThemeConfig):
        self.config = config
        self._recompute()
        self.changed.emit()

    def _recompute(self):
        c = self.config
        self.bg = c.bg
        self.surface = c.surface
        self.text = c.text
        self.accent = c.accent

        self.accent_100 = _mix(c.accent, '#ffffff', 0.88)
        self.accent_600 = _mix(c.accent, '#000000', 0.08)
        self.accent_700 = _mix(c.accent, '#000000', 0.25)
        self.accent_800 = _mix(c.accent, '#000000', 0.45)
        self.divider = _mix(c.bg, c.text, 0.16)
        self.text_muted = _mix(c.text, c.bg, 0.4)
        self.selection_bg = self.accent_100
        self.selection_text = self.accent_800

        self.display_bg = c.display_bg
        self.display_text = c.display_text
        # Reference line and the "maximize text" corner badge read as a muted version of
        # the verse text, same idea as text_muted above but against the display's own
        # bg/text pair instead of the control panel's.
        self.display_text_muted = _mix(c.display_text, c.display_bg, 0.35)
        # Supplied-words italic gray: blended between the display's text and background so
        # it stays legible (and still reads as "muted") against whatever combination the
        # user picks, instead of a fixed #888888 that could vanish on a light background.
        self.display_supplied_words = _mix(c.display_text, c.display_bg, 0.45)

        self._chevron_down = _chevron_file_url(self.text)
        self._chevron_up = _chevron_file_url(self.text, direction='up')

        self._build_styles()

    def _build_styles(self):
        self.card_style = f"""
            QFrame#card {{
                background: transparent;
                border: 1px solid {self.divider};
                border-top: 3px solid {self.accent};
            }}
        """

        self.kicker_style = f"""
            color: {self.accent_700};
            font-size: 10px;
            font-weight: 600;
            letter-spacing: 1px;
            font-family: {FONT_BODY};
        """

        self.button_style = f"""
            QPushButton {{
                background: {self.divider};
                color: {self.text};
                border: 1px solid {self.divider};
                border-radius: 4px;
                padding: 6px 12px;
                font-family: {FONT_BODY};
            }}
            QPushButton:hover {{ background: {self.divider}; }}
            QPushButton:pressed {{ background: {self.accent}; color: white; }}
            QPushButton:checkable:checked {{ background: {self.accent}; color: white; border-color: {self.accent}; }}
        """

        self.primary_button_style = f"""
            QPushButton {{
                background: {self.accent};
                color: white;
                border: 1px solid {self.accent_700};
                border-radius: 4px;
                padding: 7px 12px;
                font-family: {FONT_BODY};
                font-weight: 600;
            }}
            QPushButton:hover {{ background: {self.accent_600}; }}
            QPushButton:pressed {{ background: {self.accent_700}; }}
            QPushButton:disabled {{ background: {self.divider}; color: {self.text_muted}; border-color: {self.divider}; }}
        """

        self.ghost_button_style = f"""
            QPushButton {{
                background: transparent;
                color: {self.text};
                border: 1px solid transparent;
                border-radius: 4px;
                padding: 6px 10px;
                font-family: {FONT_BODY};
            }}
            QPushButton:hover {{ background: {self.divider}; }}
        """

        self.combo_style = f"""
            QComboBox {{
                background: {self.surface};
                color: {self.text};
                border: 1px solid {self.divider};
                border-radius: 4px;
                padding: 5px 8px;
                font-family: {FONT_BODY};
                font-size: 13px;
            }}
            QComboBox:hover {{ border-color: {self.accent}; }}
            QComboBox::drop-down {{
                border: none;
                width: 26px;
            }}
            QComboBox::down-arrow {{
                image: url({self._chevron_down});
                width: 10px;
                height: 6px;
            }}
            QComboBox QAbstractItemView {{
                background: {self.bg};
                color: {self.text};
                border: 1px solid {self.divider};
                selection-background-color: {self.selection_bg};
                selection-color: {self.selection_text};
                outline: none;
                font-family: {FONT_BODY};
                font-size: 13px;
            }}
        """

        self.spinbox_style = f"""
            QSpinBox {{
                background: {self.surface};
                color: {self.text};
                border: 1px solid {self.divider};
                border-radius: 4px;
                padding: 5px 8px;
                font-family: {FONT_BODY};
                font-size: 13px;
            }}
            QSpinBox:hover {{ border-color: {self.accent}; }}
            QSpinBox::up-button, QSpinBox::down-button {{
                border: none;
                width: 18px;
                background: transparent;
            }}
            QSpinBox::up-button {{ subcontrol-position: top right; }}
            QSpinBox::down-button {{ subcontrol-position: bottom right; }}
            QSpinBox::up-arrow {{
                image: url({self._chevron_up});
                width: 8px;
                height: 5px;
            }}
            QSpinBox::down-arrow {{
                image: url({self._chevron_down});
                width: 8px;
                height: 5px;
            }}
        """

        self.input_style = f"""
            background: {self.surface};
            color: {self.text};
            border: 1px solid {self.divider};
            border-radius: 4px;
            padding: 5px 8px;
            font-family: {FONT_BODY};
            font-size: 13px;
        """

        self.tag_outline_style = f"""
            color: {self.accent};
            border: 1px solid {self.accent};
            border-radius: 3px;
            padding: 2px 9px;
            font-size: 11px;
            font-family: {FONT_BODY};
        """

        self.results_list_style = f"""
            QListWidget {{
                background: transparent;
                border-top: 1px solid {self.divider};
                border-left: none;
                border-right: none;
                border-bottom: none;
                font-family: {FONT_BODY};
                outline: none;
            }}
            QListWidget::item {{ border-bottom: 1px solid {self.divider}; }}
            QListWidget::item:selected {{ background: transparent; border: none; }}
            QListWidget::item:focus {{ background: transparent; border: none; }}
        """


instance = Theme(ThemeConfig.load())
