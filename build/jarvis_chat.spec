# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — produces JarvisChat.exe (launcher) and JarvisSetup.exe.
# Both are GUI apps (no console window). Build with:
#   pyinstaller --clean --noconfirm --workpath build\work --distpath build\dist build\jarvis_chat.spec

from pathlib import Path
from PyInstaller.utils.hooks import collect_all

SPEC_DIR = Path(SPECPATH).resolve()
ROOT     = SPEC_DIR.parent
SRC      = ROOT / "src"
ICON     = SPEC_DIR / "icon.ico"
icon_arg = str(ICON) if ICON.is_file() else None
icon_data = [(str(ICON), ".")] if ICON.is_file() else []

# tkinterdnd2 bundles native Tcl/Tk extension binaries that PyInstaller
# only finds via collect_all. Optional: if the package isn't installed,
# the chat falls back to /attach <path> instead of drag-and-drop.
try:
    dnd_datas, dnd_binaries, dnd_hidden = collect_all("tkinterdnd2")
except Exception:
    dnd_datas, dnd_binaries, dnd_hidden = [], [], []

# Voice subsystem deps. Each is optional — collect_all returns ([],[],[]) when
# the package isn't installed. Doing this individually (vs. one big block) so
# a missing piece doesn't take down the whole bundle.
def _try_collect(name):
    try:
        return collect_all(name)
    except Exception:
        return [], [], []

voice_datas, voice_binaries, voice_hidden = [], [], []
for _pkg in ("faster_whisper", "ctranslate2", "openwakeword",
             "onnxruntime", "sounddevice", "piper",
             "piper_tts", "piper_phonemize", "tokenizers", "numpy",
             "scipy", "huggingface_hub"):
    d, b, h = _try_collect(_pkg)
    voice_datas    += d
    voice_binaries += b
    voice_hidden   += h

# scikit-learn is needed at RUNTIME to unpickle the router model (SVC +
# MinMaxScaler). Collect it like the voice deps.
router_datas, router_binaries, router_hidden = [], [], []
for _pkg in ("sklearn", "scipy", "joblib", "threadpoolctl"):
    d, b, h = _try_collect(_pkg)
    router_datas    += d
    router_binaries += b
    router_hidden   += h

# The learned-router model files (ONNX embedder + tokenizer + pickled PROMETHEUS).
# jarvis_router.py loads these from a router_models/ dir next to itself, so they
# must land in the bundle root (where _internal modules resolve __file__).
_RM = SRC / "router_models"
router_model_datas = [(str(p), "router_models") for p in _RM.glob("*")] if _RM.is_dir() else []

block_cipher = None

# --- Launcher (default entry) ---------------------------------------------
launcher_a = Analysis(
    [str(SRC / "jarvis_launcher.py")],
    pathex=[str(SRC)],
    binaries=dnd_binaries + voice_binaries + router_binaries,
    datas=icon_data + dnd_datas + voice_datas + router_datas + router_model_datas,
    hiddenimports=["PIL", "PIL.ImageGrab", "PIL.Image",
                   "jarvis_chat", "jarvis_setup", "jarvis_voice",
                   "jarvis_router", "jarvis_intent", "jarvis_council",
                   "jarvis_license", "jarvis_self", "jarvis_design_memory",
                   "jarvis_idle", "jarvis_recipes", "jarvis_search",
                   "core", "council",
                   "sklearn", "sklearn.svm", "sklearn.preprocessing",
                   "sklearn.linear_model"]
                   + dnd_hidden + voice_hidden + router_hidden,
    hookspath=[],
    runtime_hooks=[],
    # numpy + scipy + sklearn needed by voice/router; only exclude what's safe
    excludes=["matplotlib", "pandas", "torch"],
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
    datas=icon_data,
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
