@echo off
REM ==== JARVIS (Qt HUD) — the exact holographic HUD via Qt's bundled Chromium ====
REM No dependency on the system Edge WebView2 runtime (which was hanging on init).
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%build\.venv\Scripts\pythonw.exe"
if not exist "%PY%" set "PY=%ROOT%build\.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo Build venv python not found under "%ROOT%build\.venv".
    pause
    exit /b 1
)
set "PYTHONPATH=%ROOT%src"
set "JARVIS_AGENT_MODEL=qwen3:14b"
cd /d "%ROOT%src"
start "" "%PY%" jarvis_launcher.py --hud
endlocal
