# Windows: control only the Open WebUI process started by this script.
# Usage: manage_open_webui.ps1 start|stop|restart|status
param([Parameter(Mandatory = $true)][ValidateSet('start', 'stop', 'restart', 'status')][string]$Action)
$ErrorActionPreference = 'Stop'

$WebuiHome = if ($env:WEBUI_HOME) { $env:WEBUI_HOME } else { Join-Path $HOME 'open-webui' }
$Exe = Join-Path $WebuiHome '.venv\Scripts\open-webui.exe'
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
    if ($proc -and $proc.Path -ieq $Exe) { return $proc }
    return $null
}

function Start-WebUI {
    if (Test-Healthy) { Write-Host 'Open WebUI is already healthy at http://127.0.0.1:8080.'; return }
    if (-not (Test-Path $Exe)) { throw "Open WebUI is not installed: $Exe. Run setup_open_webui.cmd first." }
    if ((Test-Path $PidFile) -and -not (Get-OwnedProcess)) { Remove-Item $PidFile }

    # Load settings.env into this process; the server inherits it.
    foreach ($line in Get-Content (Join-Path $WebuiHome 'settings.env')) {
        if ($line -match '^\s*([A-Z_][A-Z0-9_]*)=(.*)$') {
            Set-Item "env:$($Matches[1])" ($Matches[2].Trim().Trim("'"))
        }
    }
    $env:DATA_DIR = Join-Path $WebuiHome 'data'
    $env:PYTHONUTF8 = '1'

    $proc = Start-Process -FilePath $Exe -ArgumentList 'serve', '--host', '127.0.0.1', '--port', '8080' `
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
    if (-not $proc) { Write-Host 'No Open WebUI process started by this script was found. Nothing stopped.'; return }
    # /T also stops the Python server the launcher spawned.
    & taskkill.exe /PID $proc.Id /T /F | Out-Null
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
