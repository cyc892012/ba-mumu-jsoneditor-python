# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

spec_root = Path(SPECPATH).resolve()

a = Analysis(
    [str(spec_root / 'main.py')],
    pathex=[str(spec_root)],
    binaries=[],
    datas=[(str(spec_root / 'ui' / 'assets'), 'ui/assets'), (str(spec_root / 'presets'), 'presets'), (str(spec_root / 'tools' / 'adb'), 'tools/adb')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5.QtQml', 'PyQt5.QtQuick', 'PyQt5.QtWebEngine', 'PyQt5.QtWebEngineCore', 'PyQt5.QtWebEngineWidgets'],
    noarchive=False,
    optimize=0,
)

a.binaries = [entry for entry in a.binaries if not any(
    name in entry[0].replace('\\', '/') for name in (
        'qwebgl.dll', 'qoffscreen.dll', 'qminimal.dll', 'Qt5Qml.dll',
        'Qt5QmlModels.dll', 'Qt5Quick.dll', 'Qt5WebSockets.dll'))]
a.datas = [entry for entry in a.datas if '/translations/' not in entry[0].replace('\\', '/')]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='摸点小助手',
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
    icon=[str(spec_root / 'ui' / 'assets' / 'app_icon.ico')],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='摸点小助手',
)
