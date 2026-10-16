# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Sedabot.

Build with build.bat (one file) or build.bat onedir (faster startup).
Voice models are not bundled. The first launch downloads them next to the exe.
"""

import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

onefile = os.environ.get("SAYIT_ONEFILE", "1") != "0"
icon = "assets/sayit.ico" if os.path.exists("assets/sayit.ico") else None


def _keep(item) -> bool:
    dest = item[1].replace("\\", "/")
    return not dest.startswith("piper/train") and "/train/" not in dest


datas = [item for item in collect_data_files("customtkinter") if _keep(item)]
datas += [item for item in collect_data_files("piper") if _keep(item)]
if os.path.exists("assets/sayit.ico"):
    datas.append(("assets/sayit.ico", "assets"))

binaries = collect_dynamic_libs("piper")

hiddenimports = [
    "pystray",
    "pystray._win32",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "pygame",
    "requests",
    "onnxruntime",
    "piper",
    "piper.voice",
    "piper.config",
    "piper.const",
    "piper.phoneme_ids",
    "piper.phonemize_espeak",
    "piper.espeakbridge",
    "piper.download_voices",
    "piper.tashkeel",
]

a = Analysis(
    ["sayit.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["piper.train", "torch", "tensorflow", "matplotlib", "notebook", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe_common = dict(
    name="Sedabot",
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

if onefile:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        **exe_common,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        **exe_common,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="Sedabot",
    )
