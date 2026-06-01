# Build script for JARVIS Chat
# Run from the JarvisChat-Release folder:  .\build\build.ps1
#
# Produces:  build\Output\JarvisChat-Setup.exe

$ErrorActionPreference = "Stop"

$repo  = Resolve-Path (Join-Path $PSScriptRoot "..")
$build = Join-Path $repo "build"
$venv  = Join-Path $build ".venv"
$pyExe = Join-Path $venv "Scripts\python.exe"

Write-Host "==> Repo:  $repo" -ForegroundColor Cyan
Write-Host "==> Build: $build" -ForegroundColor Cyan

# --- 1. Pick a Python --------------------------------------------------------
$pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
$pythonFallback = Get-Command python -ErrorAction SilentlyContinue
if ($pythonLauncher) {
    $pyCmd  = "py"
    $pyArgs = @("-3")
} elseif ($pythonFallback) {
    $pyCmd  = "python"
    $pyArgs = @()
} else {
    throw "No Python found on PATH. Install Python 3.11+ from python.org and try again."
}

# --- 2. Build venv + install deps -------------------------------------------
if (-not (Test-Path $pyExe)) {
    Write-Host "==> Creating virtualenv at $venv" -ForegroundColor Cyan
    & $pyCmd @pyArgs -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
}

Write-Host "==> Installing build deps (pyinstaller, pillow, tkinterdnd2)" -ForegroundColor Cyan
& $pyExe -m pip install --upgrade pip
& $pyExe -m pip install pyinstaller pillow tkinterdnd2
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "==> Installing voice deps (faster-whisper, openwakeword, piper-tts, sounddevice)" -ForegroundColor Cyan
# Voice subsystem is optional at runtime but bundled by default. If any
# package fails to install (no 3.14 wheel, MSVC missing, etc.), we warn
# and continue. The .exe still builds; /voice reports "unavailable".
# Each dep is installed in its own block so a single failure doesn't kill
# the rest.

function Try-Pip($pkgs, $label) {
    Write-Host "  ... $label" -ForegroundColor DarkGray
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $pyExe -m pip install @pkgs 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "    $label failed (exit $LASTEXITCODE). Voice will be partial."
            return $false
        }
        return $true
    } finally {
        $ErrorActionPreference = $prev
        $global:LASTEXITCODE = 0
    }
}

Try-Pip @("sounddevice", "numpy", "scipy") "sounddevice + numpy + scipy" | Out-Null
Try-Pip @("faster-whisper")                "faster-whisper (STT)"        | Out-Null
Try-Pip @("openwakeword")                  "openwakeword"                | Out-Null
$piperOK = Try-Pip @("piper-tts")     "piper-tts"
if (-not $piperOK) {
    Try-Pip @("piper-tts-python")     "piper-tts-python (alt)" | Out-Null
}
# scikit-learn: runtime dep for the PROMETHEUS router (unpickle SVC + scaler).
Try-Pip @("scikit-learn")             "scikit-learn (router)"       | Out-Null
$global:LASTEXITCODE = 0

# --- 3. Run PyInstaller ------------------------------------------------------
$workPath = Join-Path $build "work"
$distPath = Join-Path $build "dist"
$spec     = Join-Path $build "jarvis_chat.spec"

Write-Host "==> Running PyInstaller" -ForegroundColor Cyan
& $pyExe -m PyInstaller --clean --noconfirm `
    --workpath $workPath --distpath $distPath $spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$exeOut = Join-Path $distPath "JarvisChat\JarvisChat.exe"
if (-not (Test-Path $exeOut)) {
    throw "PyInstaller didn't produce $exeOut"
}
Write-Host "==> Built: $exeOut" -ForegroundColor Green

# --- 3b. Prune bundled test suites (scipy/sklearn/numpy ship their full test
#         trees via collect_all; they're never imported at runtime). Saves
#         ~30-50 MB off the installer. Safe: these dirs are pure test code.
$internal = Join-Path $distPath "JarvisChat\_internal"
if (Test-Path $internal) {
    Write-Host "==> Pruning bundled test modules" -ForegroundColor Cyan
    $before = (Get-ChildItem $internal -Recurse -File -ErrorAction SilentlyContinue |
               Measure-Object Length -Sum).Sum
    foreach ($pkg in @("scipy", "sklearn", "numpy", "onnxruntime")) {
        $pkgDir = Join-Path $internal $pkg
        if (Test-Path $pkgDir) {
            Get-ChildItem $pkgDir -Recurse -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -eq "tests" -or $_.Name -eq "test" } |
                ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
        }
    }
    $after = (Get-ChildItem $internal -Recurse -File -ErrorAction SilentlyContinue |
              Measure-Object Length -Sum).Sum
    $saved = [math]::Round(($before - $after) / 1MB, 1)
    Write-Host "    pruned ~$saved MB of test modules" -ForegroundColor Cyan
}

# --- 4. Compile Inno Setup installer ----------------------------------------
$issPath = Join-Path $build "installer.iss"
$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $iscc) {
    Write-Warning ".exe is built, but Inno Setup 6 was not found."
    Write-Warning "Install Inno Setup from https://jrsoftware.org/isinfo.php, then run:"
    Write-Warning "  & `"C:\Program Files (x86)\Inno Setup 6\ISCC.exe`" `"$issPath`""
    return
}

Write-Host "==> Compiling installer with $iscc" -ForegroundColor Cyan
Push-Location $build
try {
    & $iscc "installer.iss"
    if ($LASTEXITCODE -ne 0) { throw "ISCC failed" }
} finally {
    Pop-Location
}

$installer = Join-Path $build "Output\JarvisChat-Setup.exe"
if (Test-Path $installer) {
    Write-Host ""
    Write-Host "==> SUCCESS" -ForegroundColor Green
    Write-Host "    Installer: $installer" -ForegroundColor Green
    Write-Host "    Upload that file as the asset on a GitHub Release." -ForegroundColor Green
} else {
    throw "Installer wasn't produced at $installer"
}
