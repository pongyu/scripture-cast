# Build with: pyinstaller scripture_cast_onefile.spec
# Windows: produces dist\Scripture Cast.exe as a single file - no installer, no
# Python needed on the target machine. Self-extracts to a temp folder on each
# launch, so startup is slower than the onedir build (scripture_cast.spec).
# macOS: produces dist/Scripture Cast.app (unsigned).
import sys

icon = 'resources/icon.icns' if sys.platform == 'darwin' else 'resources/icon.ico'

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('bibles', 'bibles'),
        ('red_letter_kjv.json', '.'),
        ('supplied_words_kjv.json', '.'),
        ('easton_dictionary.json', '.'),
        ('strongs_dictionary.json', '.'),
        ('tsk_dictionary.json', '.'),
        ('resources', 'resources'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Scripture Cast',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)

if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='Scripture Cast.app',
        icon=icon,
        bundle_identifier='org.biblepresenter.scripturecast',
        info_plist={
            'NSHighResolutionCapable': True,
        },
    )
