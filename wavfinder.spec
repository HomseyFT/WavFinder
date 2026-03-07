# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for WavFinder.

Build commands:
    pyinstaller wavfinder.spec
"""

import sys

block_cipher = None

a = Analysis(
    ["src/wavfinder/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=["mutagen.wave", "mutagen._riff"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="WavFinder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # windowed app, no terminal
)

# macOS .app bundle
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="WavFinder.app",
        bundle_identifier="dev.wavfinder.app",
    )
