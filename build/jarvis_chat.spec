# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — produces JarvisChat.exe (launcher) and JarvisSetup.exe.
# Both are GUI apps (no console window). Build with:
#   pyinstaller --clean --noconfirm --workpath build\work --distpath build\dist build\jarvis_chat.spec

from pathlib import Path

SPEC_DIR = Path(SPECPATH).resolve()
ROOT     = SPEC_DIR.parent
SRC      = ROOT / "src"
ICON     = SPEC_DIR / "icon.ico"
icon_arg = str(ICON) if ICON.is_file() else None

block_cipher = None

# --- Launcher (default entry) ---------------------------------------------
launcher_a = Analysis(
    [str(SRC / "jarvis_launcher.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[],
    hiddenimports=["PIL", "PIL.ImageGrab", "PIL.Image",
                   "jarvis_chat", "jarvis_setup"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["numpy", "scipy", "matplotlib", "pandas", "torch"],
    cipher=block_cipher,
    noarchive=False,
)
launcher_pyz = PYZ(launcher_a.pure, launcher_a.zipped_data, cipher=block_cipher)
launcher_exe = EXE(
    launcher_pyz,
    launcher_a.scripts,
    [],
    exclude_binaries=True,
    name="JarvisChat",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=icon_arg,
)

# --- Setup wizard (separate exe) -----------------------------------------
setup_a = Analysis(
    [str(SRC / "jarvis_setup.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["numpy", "scipy", "matplotlib", "pandas", "torch"],
    cipher=block_cipher,
    noarchive=False,
)
setup_pyz = PYZ(setup_a.pure, setup_a.zipped_data, cipher=block_cipher)
setup_exe = EXE(
    setup_pyz,
    setup_a.scripts,
    [],
    exclude_binaries=True,
    name="JarvisSetup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=icon_arg,
)

# --- Single collected folder ---------------------------------------------
coll = COLLECT(
    launcher_exe, launcher_a.binaries, launcher_a.zipfiles, launcher_a.datas,
    setup_exe,    setup_a.binaries,    setup_a.zipfiles,    setup_a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="JarvisChat",
)
