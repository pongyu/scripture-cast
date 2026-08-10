"""Visual constants for the control panel, translated from the "Industry" design
system's tokens (blueprint/wireframe cards, muted blue accent, Barlow fonts) into
Qt stylesheet values. Light theme only.
"""
import sys
from pathlib import Path

# When frozen by PyInstaller, bundled data files are unpacked to sys._MEIPASS at
# runtime; when running from source, they live next to this script (mirrors app.py).
APP_DIR = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
_FONTS_DIR = APP_DIR / 'resources' / 'fonts'

BG = '#f2f2f3'
SURFACE = '#e9e9ea'
TEXT = '#1d1f20'
TEXT_MUTED = '#6b6b6d'
ACCENT = '#5980a6'
ACCENT_100 = '#eef6ff'
ACCENT_600 = '#597ea3'
ACCENT_700 = '#416180'
ACCENT_800 = '#2c455d'
DIVIDER = '#d9d9da'
SELECTION_BG = ACCENT_100
SELECTION_TEXT = ACCENT_800

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

CARD_STYLE = f"""
    QFrame#card {{
        background: transparent;
        border: 1px solid {DIVIDER};
        border-top: 3px solid {ACCENT};
    }}
"""

KICKER_STYLE = f"""
    color: {ACCENT_700};
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 1px;
    font-family: {FONT_BODY};
"""

BUTTON_STYLE = f"""
    QPushButton {{
        background: #e6e6e7;
        color: {TEXT};
        border: 1px solid {DIVIDER};
        border-radius: 4px;
        padding: 6px 12px;
        font-family: {FONT_BODY};
    }}
    QPushButton:hover {{ background: {DIVIDER}; }}
    QPushButton:pressed {{ background: {ACCENT}; color: white; }}
    QPushButton:checkable:checked {{ background: {ACCENT}; color: white; border-color: {ACCENT}; }}
"""

PRIMARY_BUTTON_STYLE = f"""
    QPushButton {{
        background: {ACCENT};
        color: white;
        border: 1px solid {ACCENT_700};
        border-radius: 4px;
        padding: 7px 12px;
        font-family: {FONT_BODY};
        font-weight: 600;
    }}
    QPushButton:hover {{ background: {ACCENT_600}; }}
    QPushButton:pressed {{ background: {ACCENT_700}; }}
    QPushButton:disabled {{ background: {DIVIDER}; color: {TEXT_MUTED}; border-color: {DIVIDER}; }}
"""

GHOST_BUTTON_STYLE = f"""
    QPushButton {{
        background: transparent;
        color: {TEXT};
        border: 1px solid transparent;
        border-radius: 4px;
        padding: 6px 10px;
        font-family: {FONT_BODY};
    }}
    QPushButton:hover {{ background: {DIVIDER}; }}
"""

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
        from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

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


_CHEVRON_DOWN = _chevron_file_url(TEXT)
_CHEVRON_UP = _chevron_file_url(TEXT, direction='up')

COMBO_STYLE = f"""
    QComboBox {{
        background: {SURFACE};
        color: {TEXT};
        border: 1px solid {DIVIDER};
        border-radius: 4px;
        padding: 5px 8px;
        font-family: {FONT_BODY};
        font-size: 13px;
    }}
    QComboBox:hover {{ border-color: {ACCENT}; }}
    QComboBox::drop-down {{
        border: none;
        width: 26px;
    }}
    QComboBox::down-arrow {{
        image: url({_CHEVRON_DOWN});
        width: 10px;
        height: 6px;
    }}
    QComboBox QAbstractItemView {{
        background: {BG};
        color: {TEXT};
        border: 1px solid {DIVIDER};
        selection-background-color: {SELECTION_BG};
        selection-color: {SELECTION_TEXT};
        outline: none;
    }}
"""

SPINBOX_STYLE = f"""
    QSpinBox {{
        background: {SURFACE};
        color: {TEXT};
        border: 1px solid {DIVIDER};
        border-radius: 4px;
        padding: 5px 8px;
        font-family: {FONT_BODY};
        font-size: 13px;
    }}
    QSpinBox:hover {{ border-color: {ACCENT}; }}
    QSpinBox::up-button, QSpinBox::down-button {{
        border: none;
        width: 18px;
        background: transparent;
    }}
    QSpinBox::up-button {{ subcontrol-position: top right; }}
    QSpinBox::down-button {{ subcontrol-position: bottom right; }}
    QSpinBox::up-arrow {{
        image: url({_CHEVRON_UP});
        width: 8px;
        height: 5px;
    }}
    QSpinBox::down-arrow {{
        image: url({_CHEVRON_DOWN});
        width: 8px;
        height: 5px;
    }}
"""

INPUT_STYLE = f"""
    background: {SURFACE};
    color: {TEXT};
    border: 1px solid {DIVIDER};
    border-radius: 4px;
    padding: 5px 8px;
    font-family: {FONT_BODY};
    font-size: 13px;
"""

TAG_OUTLINE_STYLE = f"""
    color: {ACCENT};
    border: 1px solid {ACCENT};
    border-radius: 3px;
    padding: 2px 9px;
    font-size: 11px;
    font-family: {FONT_BODY};
"""

RESULTS_LIST_STYLE = f"""
    QListWidget {{
        background: transparent;
        border-top: 1px solid {DIVIDER};
        border-left: none;
        border-right: none;
        border-bottom: none;
        font-family: {FONT_BODY};
    }}
    QListWidget::item {{ border-bottom: 1px solid {DIVIDER}; }}
"""
