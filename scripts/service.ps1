# YourTube service management for Windows (PowerShell). NOT YET TESTED ON WINDOWS — please report problems.
#   powershell -ExecutionPolicy Bypass -File scripts\service.ps1 -Install [-Port 8765]
#   powershell -ExecutionPolicy Bypass -File scripts\service.ps1 -Uninstall
#   powershell -ExecutionPolicy Bypass -File scripts\service.ps1 -Status
#   powershell -ExecutionPolicy Bypass -File scripts\service.ps1 -Open
#   powershell -ExecutionPolicy Bypass -File scripts\service.ps1 -Restart
param(
    [Parameter(Position=0)]
    [string]$Action,
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Status,
    [switch]$Open,
    [switch]$Restart,
    [int]$Port = 8765
)
$ErrorActionPreference = "Stop"

# Find the YourTube folder: current folder, next to this script, or one level up (scripts\)
$root = @((Get-Location).Path, $PSScriptRoot, (Split-Path $PSScriptRoot -Parent)) |
  Where-Object { (Test-Path (Join-Path $_ "dashboard.py")) -and (Test-Path (Join-Path $_ "requirements.txt")) } |
  Select-Object -First 1
if (-not $root) { Write-Warning "Run this inside the YourTube folder (the one with dashboard.py)."; exit 1 }

$taskName = "YourTube dashboard"
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$python = Join-Path $root ".venv\Scripts\python.exe"
$dashboardPy = Join-Path $root "dashboard.py"

if ($Install -or $Action -match '^(?i)install$') {
    if (-not (Test-Path $pythonw)) {
        Write-Warning "Python not found at $pythonw. Run scripts\setup.ps1 first."
        exit 1
    }
    $act = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$dashboardPy`" --no-open --port $Port" -WorkingDirectory $root
    $trig = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $taskName -Action $act -Trigger $trig -Settings $settings -Description "YourTube dashboard" -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Write-Host "YourTube dashboard service installed and started."
}
elseif ($Uninstall -or $Action -match '^(?i)uninstall$') {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "YourTube dashboard service uninstalled."
}
elseif ($Status -or $Action -match '^(?i)status$') {
    Get-ScheduledTask -TaskName $taskName
}
elseif ($Open -or $Action -match '^(?i)open$') {
    if (-not (Test-Path $python)) {
        Write-Warning "Python not found at $python. Run scripts\setup.ps1 first."
        exit 1
    }
    $url = & $python $dashboardPy --print-url --port $Port
    if ($url) {
        Start-Process $url.Trim()
    }
}
elseif ($Restart -or $Action -match '^(?i)restart$') {
    if (-not (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) {
        Write-Warning "YourTube service is not installed. Run scripts\service.ps1 -Install"
        exit 1
    }
    Stop-ScheduledTask -TaskName $taskName
    Start-ScheduledTask -TaskName $taskName
    Write-Host "YourTube dashboard restarted."
}
else {
    Write-Host "Usage: powershell -ExecutionPolicy Bypass -File scripts\service.ps1 {-Install|-Uninstall|-Status|-Open|-Restart} [-Port 8765]"
    exit 2
}
