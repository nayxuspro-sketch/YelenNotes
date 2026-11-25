# -*- mode: python ; coding: utf-8 -*-
"""Spécification PyInstaller ONEDIR pour Yelen Notes (Windows 10/11 64 bits — Python 3.12).

Produit un dossier portable autonome `dist/YELEN-NOTES/` contenant :
- YelenNotes.exe
- _internal/ (Python 3.12 et bibliothèques embarquées)
- resources/, config/, migrations/, README.txt, VERSION.txt
Les données scolaires sont stockées séparément dans C:\\ProgramData\\YelenNotes\\.
"""

from pathlib import Path

ROOT_DIR = Path(SPECPATH).resolve().parent

block_cipher = None

added_files = [
    (str(ROOT_DIR / "resources"), "resources"),
    (str(ROOT_DIR / "migrations"), "migrations"),
    (str(ROOT_DIR / "config" / "yelen_notes.example.ini"), "config"),
    (str(ROOT_DIR / "VERSION.txt"), "."),
    (str(ROOT_DIR / "README.txt"), "."),
]

hidden_imports = [
    "waitress",
    "sqlalchemy.dialects.sqlite",
    "flask_migrate",
    "alembic",
    "openpyxl",
    "reportlab",
    "reportlab.pdfbase.ttfonts",
    "PIL",
]

a = Analysis(
    [str(ROOT_DIR / "run.py")],
    pathex=[str(ROOT_DIR)],
    binaries=[],
    datas=added_files,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy", "numpy", "pandas"],
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
    name="YelenNotes",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="YELEN-NOTES",
)
