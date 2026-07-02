# Launch JARVIS from source (build venv python, no rebuild).
# Per memory: don't build for testing; only build when releasing.
# Opens the WEB HUD (the current UI). The old Tkinter chat is retired.
$src = "$PSScriptRoot\src"
$py  = "$PSScriptRoot\build\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Build venv python not found at $py" -ForegroundColor Red
    Write-Host "Create it / run the build once, then retry." -ForegroundColor Yellow
    exit 1
}
$env:PYTHONPATH = $src
# Agent lane uses qwen3:14b (same as chat -> no swap; 16k ctx for code work).
$env:JARVIS_AGENT_MODEL = "qwen3:14b"
Set-Location $src
& $py jarvis_launcher.py --hud
