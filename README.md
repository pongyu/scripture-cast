# Scripture Cast

A lightweight Bible presentation tool for church services: search or browse a
passage, send it to a second-screen display, and jump around live during the
service without breaking flow.

Originally extracted and reimplemented from [OpenLP](https://openlp.org/) for a
single-operator, single-service, Scripture-only workflow — no songs, no
multi-language settings, no WebEngine — just a control panel and a fullscreen
display.

## Features

- **Search or browse** — jump straight to a reference (`John 3:16`), do a
  free-text search across the whole Bible, or browse book by book, chapter by
  chapter.
- **Second-screen display** — a frameless, always-on-top fullscreen window for
  the projector/TV, with a live mirror in the control panel so the operator
  always sees exactly what the congregation sees, even while "Show Desktop" is
  hiding the real output.
- **Live navigation** — Previous/Next steps through long verses split across
  screens, then keeps going into the next/previous verse in the Bible; type a
  verse number and hit Enter to jump straight to it, PowerPoint-slide-number
  style.
- **Service Plan** — save passages ahead of a service (`+ Add to Service`) so
  jumping back to the opening passage after a tangent doesn't mean
  re-searching. Persists across restarts.
- **Multiple Bible versions** — switch translations mid-service without losing
  your place; ships with the King James Version and Tagalog Ang Biblia.
- **KJV extras** — red-letter (words of Christ), italicized
  translator-supplied words, and verse numbers, all toggleable.
- **Fully theme-able** — control panel colors, display background/text colors,
  church name and logo are all configurable from Settings, so the app can be
  shared with other churches without touching code.
- **Configurable keyboard shortcuts** for every common action.

## Running from source

Requires Python 3.11+ and PySide6.

```
pip install PySide6
python app.py
```

By default it loads every `.sqlite` Bible file found in `bibles/`, falling
back to `%APPDATA%\openlp\data\bibles\` if that folder is empty (so it can
reuse Bibles already downloaded for OpenLP). You can also pass specific files
directly:

```
python app.py path\to\some-bible.sqlite
```

## Building a standalone .exe

```
pip install pyinstaller
pyinstaller scripture_cast.spec
```

This produces `dist\Scripture Cast\Scripture Cast.exe` plus a support folder —
no installer, no Python required on the target machine. Copy or zip the whole
`Scripture Cast` folder to share it.

## Default keyboard shortcuts

All configurable from **Settings → Shortcuts**.

| Action                | Default        |
| ---------------------- | -------------- |
| Send to Display        | `Enter`        |
| Show Display Window    | `Shift+Enter`  |
| Clear Display           | `Ctrl+Backspace` |
| Show/Hide Desktop       | `Ctrl+D`       |
| Switch Bible Version    | `Ctrl+Shift+V` |
| Focus Search Box        | `Ctrl+F`       |
| Add to Service          | `Ctrl+=`       |

Previous/Next are on-screen buttons only (also mirrored to the fullscreen
display's own arrow-key/Page Up/Down/Space handling). Typing a verse number
followed by `Enter` jumps to it directly.

## Configuration files

Settings persist to `%APPDATA%\bible-presenter\`:

| File                  | Contents                                   |
| ---------------------- | ------------------------------------------- |
| `config.json`           | Display text size, spacing, KJV toggles     |
| `theme_config.json`     | Control panel and display colors            |
| `identity.json`         | Church name and logo                        |
| `keybindings.json`      | Custom keyboard shortcuts                   |
| `service.json`          | Saved Service Plan passages                 |
| `crash.log`             | Traceback from the last uncaught error, if any |

## Project layout

- `app.py` — entrypoint, Bible discovery, crash logging
- `bible.py` — SQLite queries and reference parsing (`John 3:16`, ranges, etc.)
- `main_window.py` — control panel: search/browse, results list, Service Plan
- `display_window.py` — the fullscreen (and hidden-preview) display window
- `settings_dialog.py` — tabbed Display / Identity / Appearance / Shortcuts settings
- `theme.py` — control panel color theme, derived shades, Lucide icon rendering
- `identity.py`, `keybindings.py`, `config.py`, `service.py` — small persisted-config dataclasses
- `red_letter.py`, `supplied_words.py` — KJV red-letter and italics data
- `resources/` — fonts, icons, app icon, default church logo
