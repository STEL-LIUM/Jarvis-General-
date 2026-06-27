# Launch JARVIS Phone — private mobile web chat (reach it from your phone over Tailscale).
# Per memory: run from source, no rebuild needed for this.
$src = "$PSScriptRoot\src"
$py  = "$PSScriptRoot\build\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }   # fall back to system python
Set-Location $src
# Optional: set a shared secret so only someone with the link can chat.
# $env:JARVIS_PHONE_TOKEN = "pick-a-secret"
& $py jarvis_phone.py
