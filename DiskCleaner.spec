# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        # Всё содержимое папки assets — внутрь exe под тем же именем
        ('assets', 'assets'),
    ],
    hiddenimports=[
        'cleaners',
        'cleaners.utils',
        'cleaners.safety',
        'cleaners.smart_rules',
        'cleaners.disk_scan',
        'cleaners.report',
        'cleaners.duplicate_finder',
        'cleaners.system_cleaner',
        'cleaners.full_check',
        'cleaners.registry_cleaner',
        'cleaners.uninstaller',
        'cleaners.startup',
        'ui.analyzer_tab',
        'ui.startup_tab',
        'ui.registry_tab',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DiskCleaner',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)