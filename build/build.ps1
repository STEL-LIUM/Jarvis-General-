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

Write-Host "==> Installing build deps (pyinstaller, pillow)" -ForegroundColor Cyan
& $pyExe -m pip install --upgrade pip
& $pyExe -m pip install pyinstaller pillow
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

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
