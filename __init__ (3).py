# -*- mode: python ; coding: utf-8 -*-
"""Configuration PyInstaller pour Printou."""

from pathlib import Path

block_cipher = None
# SPECPATH pointe vers le dossier du .spec (build_tools/).
# La racine du projet est un cran au-dessus.
project_root = Path(SPECPATH).resolve().parent

# Données à embarquer (assets, templates par défaut)
datas = []
assets_dir = project_root / "assets"
if assets_dir.exists():
    datas.append((str(assets_dir), "assets"))
templates_dir = project_root / "templates"
if templates_dir.exists():
    datas.append((str(templates_dir), "templates"))

# Imports cachés (PySide6 + watchdog ont parfois besoin d'aide)
hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "watchdog.observers",
    "watchdog.events",
    "PIL._tkinter_finder",
]

a = Analysis(
    [str(project_root / "printou" / "__main__.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # On retire les modules Qt qu'on n'utilise pas pour réduire la taille
        "PySide6.QtNetwork",
        "PySide6.QtSql",
        "PySide6.QtTest",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtCharts",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DRender",
        "tkinter",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Printou",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # UPX peut faire planter sur Windows Defender, on évite
    console=False,       # Pas de console noire derrière (mode windowed)
    icon=str(project_root / "assets" / "printou.ico") if (project_root / "assets" / "printou.ico").exists() else None,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Printou",
)
