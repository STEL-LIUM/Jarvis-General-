# Building JARVIS Chat

This explains how to compile a Windows `.exe` and the `.exe`-based installer
from source.

## Prerequisites

1. **Python 3.11 or 3.12** (3.13 also works). Install from
   [python.org](https://www.python.org/downloads/) — tick "Add Python to PATH".
2. **Inno Setup 6** — installer compiler. Download from
   [jrsoftware.org](https://jrsoftware.org/isinfo.php) and accept the default
   install path (`C:\Program Files (x86)\Inno Setup 6`).
3. A PowerShell window in the `JarvisChat-Release` folder.

## One-shot build

```powershell
.\build\build.ps1
```

That script does the whole pipeline:

1. Creates a clean virtualenv in `build\.venv`
2. `pip install pyinstaller pillow`
3. Runs PyInstaller on `build\jarvis_chat.spec` → produces
   `build\dist\JarvisChat\JarvisChat.exe` (+ supporting files) and
   `build\dist\JarvisSetup.exe`
4. Compiles `build\installer.iss` with Inno Setup → produces
   `build\Output\JarvisChat-Setup.exe`

## Manual build

If you want to drive each step yourself:

```powershell
# 1. Build the exes
py -m venv build\.venv
build\.venv\Scripts\Activate.ps1
pip install pyinstaller pillow
pyinstaller --clean --noconfirm --workpath build\work --distpath build\dist build\jarvis_chat.spec

# 2. Compile the installer
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" build\installer.iss
```

The final installer lands at `build\Output\JarvisChat-Setup.exe`. Drop that
file as a GitHub release asset.

## Release checklist

1. Bump the `MyAppVersion` define near the top of `build\installer.iss`.
2. Run `.\build\build.ps1`.
3. Smoke-test the installer on a clean Windows VM if you can — confirm:
   - It installs to `Program Files\JarvisChat`
   - Start Menu shortcut appears
   - First launch shows the setup wizard
   - Setup detects (or installs) Ollama, pulls the selected model pack
   - Chat panel opens after setup finishes
4. Tag the commit (e.g. `v1.0.0`), push, create a GitHub Release, upload
   `JarvisChat-Setup.exe` as the asset.

## Optional: app icon

Drop a square `icon.ico` (256×256 recommended) into `build\` to give the
installer and exes a custom icon. Then uncomment the
`SetupIconFile=icon.ico` line in `build\installer.iss`. PyInstaller picks
the same file up automatically.

## Notes / gotchas

- **Inno Setup path varies** — a per-user install lands at
  `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe` rather than
  `C:\Program Files (x86)\...`. Use whichever exists on your machine.
- **OneDrive-synced checkouts** — if the repo lives inside a OneDrive folder,
  delete `build\work` and `build\dist` before rebuilding; OneDrive can keep a
  handle open on the old files and make PyInstaller fail mid-build.
- **PyInstaller + Pillow** — the spec file already lists `PIL` as a hidden
  import; no extra config needed.
- **Antivirus false positives** — unsigned PyInstaller binaries sometimes
  trigger heuristic detection. For a polished release, code-sign the
  installer (`signtool.exe`).
- **Tkinter** — bundled with the Python.org installer. If you used a
  minimal Python build that omits tkinter, install Python from python.org
  instead.

## Opt-in feedback backend (`hf_space/`)

`hf_space/` is the FastAPI proxy that receives opt-in submissions from
JarvisChat and writes them to the
[`STEL-LIUM/jarvis-feedback`](https://huggingface.co/datasets/STEL-LIUM/jarvis-feedback)
HF dataset. It is **not** part of the .exe build — it is deployed to a
Hugging Face Space (`STEL-LIUM/jarvis-feedback-api`).

To update the backend:

1. Edit `hf_space/app.py`.
2. Push the contents of `hf_space/` to the Space's git repo (HF Spaces are
   git repos under the hood), or upload via the web UI.
3. The Space rebuilds automatically (Docker SDK).

The client URL is set by the `TELEMETRY_URL` constant in
`src/jarvis_chat.py` and can be overridden at runtime with the
`JARVIS_TELEMETRY_URL` env var (useful for testing against a staging
Space).
