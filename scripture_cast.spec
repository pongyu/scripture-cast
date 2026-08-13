# Build with: pyinstaller scripture_cast.spec
# Produces dist\Scripture Cast\Scripture Cast.exe (plus a support folder) - no installer,
# no Python needed on the target machine. Copy/zip the whole "Scripture Cast" folder to share it.
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
    [],
    exclude_binaries=True,
    name='Scripture Cast',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='resources/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Scripture Cast',
)
