# Launch JarvisChat from source (build venv python, no rebuild).
# Per memory: don't build for testing; only build when releasing.
$src = "$PSScriptRoot\src"
$py  = "$PSScriptRoot\build\.venv\Scripts\python.exe"
Set-Location $src
& $py jarvis_launcher.py --chat
