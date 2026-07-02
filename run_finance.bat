@echo off
REM ==== JARVIS Finance (standalone dashboard window) ====
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
cd /d "%ROOT%src"
start "" "%PY%" jarvis_finance_ui.py
endlocal
