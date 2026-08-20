# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from cloud_config import assert_release_config_buildable


config_source = 'release_cloud_config.json'
assert_release_config_buildable(config_source)
config_datas = [(config_source, '.')] if Path(config_source).is_file() else []


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
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
