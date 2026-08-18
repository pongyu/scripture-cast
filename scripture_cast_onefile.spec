# Build with: pyinstaller scripture_cast_onefile.spec
# Produces dist\Scripture Cast.exe as a single file - no installer, no Python
# needed on the target machine. Self-extracts to a temp folder on each launch,
# so startup is slower than the onedir build (scripture_cast.spec).
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
    icon='resources/icon.ico',
)
