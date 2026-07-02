@echo off
REM ==== JARVIS console (no HUD / no WebView2) ====
REM Use this when the web HUD won't open. Same brain, terminal interface.
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%build\.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo Build venv python not found under "%ROOT%build\.venv".
    pause
    exit /b 1
)
set "PYTHONPATH=%ROOT%src"
set "JARVIS_AGENT_MODEL=qwen3:14b"
cd /d "%ROOT%src"
"%PY%" jarvis_console.py
pause
endlocal
