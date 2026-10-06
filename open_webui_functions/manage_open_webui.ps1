# Windows: control only the Open WebUI process started by this script.
# Usage: manage_open_webui.ps1 start|stop|restart|status
param([Parameter(Mandatory = $true)][ValidateSet('start', 'stop', 'restart', 'status')][string]$Action)
$ErrorActionPreference = 'Stop'

$WebuiHome = if ($env:WEBUI_HOME) { $env:WEBUI_HOME } else { Join-Path $HOME 'open-webui' }
$Exe = Join-Path $WebuiHome '.venv\Scripts\open-webui.exe'
$Python = Join-Path $WebuiHome '.venv\Scripts\python.exe'
$PidFile = Join-Path $WebuiHome 'server.pid'
$LogFile = Join-Path $WebuiHome 'server.log'
$ErrFile = Join-Path $WebuiHome 'server.err.log'
$HealthUrl = 'http://127.0.0.1:8080/api/version'
$StartupWait = if ($env:STARTUP_WAIT) { [int]$env:STARTUP_WAIT } else { 60 }

function Test-Healthy {
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 $HealthUrl | Out-Null; return $true } catch { return $false }
}

function Get-OwnedProcess {
    if (-not (Test-Path $PidFile)) { return $null }
    $id = (Get-Content $PidFile -Raw).Trim()
    if ($id -notmatch '^\d+$') { return $null }
    $proc = Get-Process -Id ([int]$id) -ErrorAction SilentlyContinue
    if (-not $proc) { return $null }
    # The console-script launcher can hand work to python.exe. Check the
    # command line as well as the executable so the PID remains verifiable.
    $commandLine = (Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue).CommandLine
    if (($proc.Path -ieq $Exe) -or ($proc.Path -ieq $Python -and $commandLine -match '(?i)open[_-]webui')) { return $proc }
    return $null
}

function Remove-StalePidFile {
    if (-not (Test-Path $PidFile)) { return $false }
    $id = (Get-Content $PidFile -Raw).Trim()
    $proc = if ($id -match '^\d+$') { Get-Process -Id ([int]$id) -ErrorAction SilentlyContinue } else { $null }
    if (-not $proc) {
        Remove-Item $PidFile -Force
        Write-Host "Removed stale PID file: $PidFile"
        return $true
    }
    return $false
}

function Start-WebUI {
    if (Test-Healthy) { Write-Host 'Open WebUI is already healthy at http://127.0.0.1:8080.'; return }
    if (-not (Test-Path $Python)) { throw "Open WebUI is not installed: $Python. Run setup_open_webui.cmd first." }
    $owned = Get-OwnedProcess
    if ($owned) { throw "Open WebUI process $($owned.Id) is still running but is not healthy; run '$PSCommandPath restart' or inspect $ErrFile." }
    if ((Test-Path $PidFile) -and -not (Remove-StalePidFile)) { throw "Refusing to replace a PID file for a live, unverified process: $PidFile" }

    # Load settings.env into this process; the server inherits it.
    foreach ($line in Get-Content (Join-Path $WebuiHome 'settings.env')) {
        if ($line -match '^\s*([A-Z_][A-Z0-9_]*)=(.*)$') {
            Set-Item "env:$($Matches[1])" ($Matches[2].Trim().Trim("'"))
        }
    }
    $env:DATA_DIR = Join-Path $WebuiHome 'data'
    $env:PYTHONUTF8 = '1'

    # Invoke Python directly so the recorded PID is the long-lived server.
    $proc = Start-Process -FilePath $Python -ArgumentList '-m', 'open_webui', 'serve', '--host', '127.0.0.1', '--port', '8080' `
        -WorkingDirectory $WebuiHome -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $LogFile -RedirectStandardError $ErrFile
    Set-Content -Path $PidFile -Value $proc.Id
    for ($i = 0; $i -lt $StartupWait; $i++) {
        if (Test-Healthy) { Write-Host "Open WebUI started at http://127.0.0.1:8080 (PID $($proc.Id))."; return }
        if ($proc.HasExited) { break }
        Start-Sleep -Seconds 1
    }
    throw "Open WebUI did not become healthy; see $ErrFile"
}

function Stop-WebUI {
    $proc = Get-OwnedProcess
    if (-not $proc) {
        if (Test-Path $PidFile) {
            if (-not (Remove-StalePidFile)) { throw "Refusing to stop the live process recorded in an unverified PID file: $PidFile" }
        }
        Write-Host 'No Open WebUI process started by this script was found. Nothing stopped.'
        return
    }
    # /T ensures no child process from this server is left behind.
    & taskkill.exe /PID $proc.Id /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Open WebUI PID $($proc.Id) could not be stopped." }
    Remove-Item $PidFile -ErrorAction SilentlyContinue
    Write-Host 'Open WebUI stopped.'
}

switch ($Action) {
    'start' { Start-WebUI }
    'stop' { Stop-WebUI }
    'restart' { Stop-WebUI; Start-WebUI }
    'status' {
        if (Test-Healthy) { Write-Host 'Open WebUI is healthy at http://127.0.0.1:8080.' }
        else { Write-Host 'Open WebUI is not responding at http://127.0.0.1:8080.'; exit 1 }
    }
}
