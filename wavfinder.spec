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
        info_plist={
            # Without this the window renders at 1x and looks blurry on any
            # Retina display.
            "NSHighResolutionCapable": True,
            "CFBundleShortVersionString": "0.2.0",
            # Reading a sound library that lives in Documents/Desktop/an
            # external volume triggers a macOS consent prompt; this is the text
            # shown in it.
            "NSDesktopFolderUsageDescription": "WavFinder scans your sound libraries for .wav files.",
            "NSDocumentsFolderUsageDescription": "WavFinder scans your sound libraries for .wav files.",
            "NSRemovableVolumesUsageDescription": "WavFinder scans sound libraries on external drives.",
        },
    )
