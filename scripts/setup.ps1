# YourTube setup for Windows (PowerShell). NOT YET TESTED ON WINDOWS — please report problems. Guide: docs/SETUP.md
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1            # app only
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -LocalAI   # also Ollama + a model for this PC
#   add -Service to keep the dashboard running (starts at logon; untested)
# Safe to run again; asks before installing anything system-wide.
param([switch]$LocalAI, [switch]$Yes, [switch]$Service)
$ErrorActionPreference = "Stop"
# Find the YourTube folder: the current folder, next to this script, or one level up (scripts\)
$root = @((Get-Location).Path, $PSScriptRoot, (Split-Path $PSScriptRoot -Parent)) |
  Where-Object { (Test-Path (Join-Path $_ "dashboard.py")) -and (Test-Path (Join-Path $_ "requirements.txt")) } |
  Select-Object -First 1
if (-not $root) { Write-Warning "Run this inside the YourTube folder (the one with dashboard.py)."; exit 1 }
Set-Location $root
function Ask($q) { if ($Yes) { return $true }; return (Read-Host "  $q [y/N]") -match '^[Yy]' }

Write-Host "`n1. Python" -ForegroundColor White
$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }   # Windows PowerShell 5.1 has no ?? operator
if (-not $py) { Write-Warning "Python 3.10+ not found. Install it from python.org, then run this again."; exit 1 }
& $py.Source -c "import sys; sys.exit(sys.version_info < (3, 10))"; if ($LASTEXITCODE) { Write-Warning "Python 3.10+ is needed."; exit 1 }

Write-Host "`n2. Virtual environment and dependencies" -ForegroundColor White
if (-not (Test-Path .venv\Scripts\python.exe)) { & $py.Source -m venv .venv }
& .venv\Scripts\python.exe -m pip install -q -r requirements.txt

Write-Host "`n3. ffmpeg" -ForegroundColor White
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) { Write-Host "  ffmpeg found" }
else { Write-Warning "ffmpeg not found (needed later). Install with: winget install Gyan.FFmpeg" }

if ($LocalAI) {
  Write-Host "`n4. Ollama" -ForegroundColor White
  if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    if (Ask "Install Ollama with winget (winget install Ollama.Ollama)?") { winget install --id Ollama.Ollama -e }
    else { Write-Warning "Skipped. Install Ollama from https://ollama.com/download, then run this again."; exit 0 }
    Write-Warning "Open a new PowerShell window so 'ollama' is on PATH, then run this script again."; exit 0
  }
  try { Invoke-RestMethod http://localhost:11434/api/version -TimeoutSec 3 | Out-Null }
  catch { Start-Process ollama -ArgumentList serve -WindowStyle Hidden; Start-Sleep 5 }

  Write-Host "`n4b. Local transcription (faster-whisper)" -ForegroundColor White
  & .venv\Scripts\python.exe -c "import faster_whisper" 2>$null
  if ($LASTEXITCODE -and (Ask "Install local transcription (faster-whisper, about 1.2 GB with NVIDIA libraries)?")) {
    & .venv\Scripts\python.exe -m pip install -q -r requirements-local.txt
  }

  Write-Host "`n5. Model sized for this computer" -ForegroundColor White
  $model = if ($env:YTKB_MODEL) { $env:YTKB_MODEL } else { & .venv\Scripts\python.exe local_setup.py --recommended }
  Write-Host "  Recommended: $model"
  if (Ask "Download $model now?") {
    & .venv\Scripts\python.exe local_setup.py --pull $model
    if (-not $LASTEXITCODE) { & .venv\Scripts\python.exe local_setup.py --use $model }
  }

  Write-Host "`n6. Embedding model for questions and meaning-based search" -ForegroundColor White
  $embed = & .venv\Scripts\python.exe local_setup.py --embed-model
  if (Ask "Download $embed (0.64 GB) so questions and search match by meaning, in any language?") {
    & .venv\Scripts\python.exe local_setup.py --pull $embed
  }
}

Write-Host "`nDone" -ForegroundColor White
& .venv\Scripts\python.exe local_setup.py
if ($Service) {
    & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "service.ps1") -Install
    & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "service.ps1") -Open
} else {
    Write-Host "`n  Start the dashboard:  .venv\Scripts\python.exe dashboard.py"
    Write-Host "  Keep it running:      powershell -ExecutionPolicy Bypass -File scripts\service.ps1 -Install"
}
