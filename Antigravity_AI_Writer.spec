# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

import PyQt6

from cloud_config import assert_release_config_buildable


config_source = 'release_cloud_config.json'
assert_release_config_buildable(config_source)
config_datas = [(config_source, '.')] if Path(config_source).is_file() else []

# PyInstaller also collects the older VC++ runtime shipped beside python.exe.
# A one-file build loads DLLs from its extraction root before Qt's bin folder,
# so QtWidgets can bind to that older runtime and fail with ERROR_PROC_NOT_FOUND.
# Put the redistributable set shipped with this exact Qt wheel at the root.
qt_bin = Path(PyQt6.__file__).resolve().parent / 'Qt6' / 'bin'
qt_runtime_names = (
    'MSVCP140.dll',
    'MSVCP140_1.dll',
    'MSVCP140_2.dll',
    'msvcp140_atomic_wait.dll',
    'msvcp140_codecvt_ids.dll',
    'VCRUNTIME140.dll',
    'VCRUNTIME140_1.dll',
    'vcruntime140_threads.dll',
)
qt_root_runtime_binaries = [
    (str(qt_bin / name), '.')
    for name in qt_runtime_names
    if (qt_bin / name).is_file()
]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=qt_root_runtime_binaries,
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

# Qt 6 uses Windows' unversioned System32 ICU shim. A developer PATH can also
# contain an unrelated versioned ICU build under the same ``icuuc.dll`` name
# (for example Poppler's ICU 78); PyInstaller would otherwise bundle that DLL
# and QtCore would fail with ERROR_PROC_NOT_FOUND before main starts. System
# DLLs belong to the target OS, so never carry a root ICU capture in this exe.
a.binaries = [
    entry for entry in a.binaries
    if Path(entry[0]).name.casefold() != 'icuuc.dll'
    and not Path(entry[0]).name.casefold().startswith('icudt')
]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Antigravity_AI_Writer',
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
