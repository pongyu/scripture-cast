"""Scripture Cast - standalone entrypoint."""
import sys
import traceback
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

import theme
from bible import Bible
from main_window import MainWindow

# When frozen by PyInstaller, bundled data files are unpacked to sys._MEIPASS at
# runtime; when running from source, they live next to this script.
APP_DIR = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
BUNDLED_BIBLES_DIR = APP_DIR / 'bibles'
OPENLP_BIBLES_DIR = Path.home() / 'AppData' / 'Roaming' / 'openlp' / 'data' / 'bibles'
ICON_PATH = APP_DIR / 'resources' / 'icon.ico'
CRASH_LOG_PATH = Path.home() / 'AppData' / 'Roaming' / 'bible-presenter' / 'crash.log'


def _log_uncaught_exception(exc_type, exc_value, exc_tb):
    """PySide6 doesn't reliably surface Python exceptions raised inside Qt signal
    handlers/slots — on some builds an unhandled one can silently kill the whole
    process instead of printing a traceback, which is what happened when a book
    selection triggered a crash with nothing in stdout/stderr to show why. This
    writes the traceback to disk before the process goes down, so a repeat has
    something to diagnose from."""
    text = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
    try:
        CRASH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CRASH_LOG_PATH.write_text(text)
    except OSError:
        pass
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def discover_bibles(paths: list[Path]) -> dict[str, Bible]:
    """Load every given .sqlite file as a Bible, keyed by its filename (without extension)
    as the version name shown in the UI."""
    bibles = {}
    for path in paths:
        bibles[path.stem] = Bible(str(path))
    return bibles


def main():
    sys.excepthook = _log_uncaught_exception
    if len(sys.argv) > 1:
        paths = [Path(p) for p in sys.argv[1:]]
        missing = [p for p in paths if not p.exists()]
        if missing:
            for p in missing:
                print(f'Bible database not found: {p}')
            sys.exit(1)
    else:
        paths = sorted(BUNDLED_BIBLES_DIR.glob('*.sqlite'))
        if not paths:
            paths = sorted(OPENLP_BIBLES_DIR.glob('*.sqlite'))
        if not paths:
            print(f'No Bible databases found in: {BUNDLED_BIBLES_DIR} or {OPENLP_BIBLES_DIR}')
            print('Usage: python app.py [path-to-bible.sqlite ...]')
            sys.exit(1)

    app = QApplication(sys.argv)
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    theme.load_fonts()
    bibles = discover_bibles(paths)
    window = MainWindow(bibles)
    window.show()
    exit_code = app.exec()
    for bible in bibles.values():
        bible.close()
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
