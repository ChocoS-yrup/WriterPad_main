# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

import PyQt6

from cloud_config import assert_release_config_buildable


config_source = 'release_cloud_config.json'
assert_release_config_buildable(config_source)
config_datas = [(config_source, '.')] if Path(config_source).is_file() else []

# Qt 6.9+ is built against the newer split MSVC runtime. PyInstaller's clean
# dependency scan can classify those DLLs as system libraries and omit them,
# while also collecting Python's older root VCRUNTIME. QtWidgets then resolves
# the older root copy first and fails with "procedure not found" before main
# can start. Keep one complete, version-matched runtime set beside the exe.
qt_bin = Path(PyQt6.__file__).resolve().parent / 'Qt6' / 'bin'
qt_runtime_names = (
    'MSVCP140.dll',
    'MSVCP140_1.dll',
    'MSVCP140_2.dll',
    'VCRUNTIME140.dll',
    'VCRUNTIME140_1.dll',
    'msvcp140_atomic_wait.dll',
    'msvcp140_codecvt_ids.dll',
    'vcruntime140_threads.dll',
)
missing_qt_runtimes = [name for name in qt_runtime_names if not (qt_bin / name).is_file()]
if missing_qt_runtimes:
    raise RuntimeError(f'Missing Qt MSVC runtimes: {missing_qt_runtimes!r}')
qt_runtime_binaries = [(str(qt_bin / name), '.') for name in qt_runtime_names]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=qt_runtime_binaries,
    datas=[
        ('style.qss', '.'),
        ('app_icon.ico', '.'),
        ('model_catalog.json', '.'),
        # When present, this validated file contains URL and publishable key
        # fields only. User sessions remain in Windows Credential Manager.
    ] + config_datas,
    hiddenimports=['markdown', 'keyring', 'unicodedata2'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# The desktop agent process can put its document/PDF runtime on PATH. A clean
# PyInstaller scan then mistakes Poppler's unversioned ICU DLL for an app
# dependency and places it at the extraction root, where it shadows Qt's DLL
# dependency resolution and makes QtWidgets fail with error 127. WriterPad must
# never inherit build-host tooling binaries.
def is_codex_runtime_binary(entry):
    source = str(entry[1]).replace('/', '\\').casefold()
    return '\\.cache\\codex-runtimes\\' in source


a.binaries = [entry for entry in a.binaries if not is_codex_runtime_binary(entry)]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='작가님 힘내세요',
    icon='app_icon.ico',
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
