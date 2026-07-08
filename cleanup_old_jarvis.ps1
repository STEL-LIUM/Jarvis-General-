# JarvisChat Cleanup Script
# Removes outdated Jarvis installations from C: drive
# ONLY RUN THIS AFTER CONFIRMING E:\AI\JarvisChat WORKS!

param([switch]$DryRun)

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  JARVISCHAT CLEANUP SCRIPT" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

if ($DryRun) {
    Write-Host "[DRY RUN MODE] - No files will be deleted" -ForegroundColor Yellow
    Write-Host ""
}

# Outdated locations to remove
$outdated = @(
    @{Path="C:\Users\Aryan\OneDrive\Desktop - Copy\Desktop\Jarvis code"; Desc="Old Jarvis code (June 2026)"},
    @{Path="C:\Users\Aryan\OneDrive\Desktop - Copy\Desktop\Jarvis Agent"; Desc="Empty Jarvis Agent folder"},
    @{Path="C:\Users\Aryan\OneDrive\Desktop - Copy\Desktop\JarvisAI"; Desc="Old JarvisAI parent folder"},
    @{Path="C:\llama.cpp"; Desc="Old llama.cpp binaries"}
)

# Check current installation
Write-Host "Checking current installation..." -ForegroundColor Cyan
$current = "E:\AI\JarvisChat\jarvis_chat.py"
if (-not (Test-Path $current)) {
    Write-Host "[ERROR] E:\AI\JarvisChat not found!" -ForegroundColor Red
    Write-Host "Cannot proceed - current installation missing." -ForegroundColor Red
    exit 1
}
Write-Host "[OK] Current installation found at E:\AI\JarvisChat" -ForegroundColor Green
Write-Host ""

# Calculate space to reclaim
Write-Host "Scanning outdated locations..." -ForegroundColor Cyan
$totalSize = 0
$found = 0

foreach ($item in $outdated) {
    if (Test-Path $item.Path) {
        $size = (Get-ChildItem $item.Path -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
        $sizeMB = [math]::Round($size / 1MB, 2)
        $totalSize += $size
        $found++
        
        Write-Host "  [FOUND] $($item.Desc)" -ForegroundColor Yellow
        Write-Host "          $($item.Path)" -ForegroundColor DarkGray
        Write-Host "          Size: $sizeMB MB" -ForegroundColor DarkGray
    } else {
        Write-Host "  [SKIP] $($item.Desc) - already deleted" -ForegroundColor DarkGray
    }
}

Write-Host ""
$totalMB = [math]::Round($totalSize / 1MB, 2)
$totalGB = [math]::Round($totalSize / 1GB, 2)
Write-Host "Total space to reclaim: $totalMB MB ($totalGB GB)" -ForegroundColor Cyan
Write-Host ""

if ($found -eq 0) {
    Write-Host "Nothing to delete - already clean!" -ForegroundColor Green
    exit 0
}

# Confirmation
if (-not $DryRun) {
    Write-Host "============================================" -ForegroundColor Red
    Write-Host "  WARNING: ABOUT TO DELETE $found LOCATIONS" -ForegroundColor Red
    Write-Host "============================================" -ForegroundColor Red
    Write-Host ""
    Write-Host "This will DELETE the following:" -ForegroundColor Yellow
    foreach ($item in $outdated) {
        if (Test-Path $item.Path) {
            Write-Host "  - $($item.Path)" -ForegroundColor Red
        }
    }
    Write-Host ""
    $confirm = Read-Host "Type 'DELETE' to confirm (or anything else to cancel)"
    
    if ($confirm -ne "DELETE") {
        Write-Host ""
        Write-Host "Cancelled - no files deleted." -ForegroundColor Yellow
        exit 0
    }
}

Write-Host ""
Write-Host "Processing deletions..." -ForegroundColor Cyan

# Delete each outdated location
foreach ($item in $outdated) {
    if (Test-Path $item.Path) {
        Write-Host "  Deleting: $($item.Path)" -ForegroundColor Yellow
        
        if ($DryRun) {
            Write-Host "    [DRY RUN] Would delete" -ForegroundColor DarkGray
        } else {
            try {
                Remove-Item $item.Path -Recurse -Force -ErrorAction Stop
                Write-Host "    [DELETED]" -ForegroundColor Green
            } catch {
                Write-Host "    [ERROR] $_" -ForegroundColor Red
            }
        }
    }
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
if ($DryRun) {
    Write-Host "  DRY RUN COMPLETE" -ForegroundColor Green
    Write-Host "  Run without -DryRun to actually delete" -ForegroundColor Yellow
} else {
    Write-Host "  CLEANUP COMPLETE" -ForegroundColor Green
    Write-Host "  Reclaimed ~$totalGB GB on C: drive" -ForegroundColor Green
}
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "JarvisChat is now at: E:\AI\JarvisChat" -ForegroundColor Cyan
Write-Host "Launch with: E:\AI\JarvisChat\JarvisChat.bat" -ForegroundColor Cyan
