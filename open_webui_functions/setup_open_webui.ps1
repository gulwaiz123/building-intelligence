# Windows one-shot classroom setup: install pinned Open WebUI, write its
# settings, start it, and load the RAG options controls. Safe to re-run;
# it never deletes chats in %USERPROFILE%\open-webui\data.
$ErrorActionPreference = 'Stop'

$Version = '0.11.4'
$WebuiHome = if ($env:WEBUI_HOME) { $env:WEBUI_HOME } else { Join-Path $HOME 'open-webui' }
$Here = $PSScriptRoot
$Venv = Join-Path $WebuiHome '.venv'
$Python = Join-Path $Venv 'Scripts\python.exe'
$env:WEBUI_HOME = $WebuiHome
$env:PYTHONUTF8 = '1'
New-Item -ItemType Directory -Force -Path $WebuiHome | Out-Null

# 1. Install Open WebUI (skipped when the pinned version is already present).
$installed = ''
if (Test-Path $Python) {
    try { $installed = & $Python -c "import importlib.metadata as m; print(m.version('open-webui'))" 2>$null } catch { }
}
$Offline = Join-Path $Here 'offline'
if ($installed -ne $Version -and (Test-Path (Join-Path $Offline 'python\python.exe'))) {
    # LAN bundle from build_offline_bundle.sh: no internet needed.
    Write-Host "Installing Open WebUI $Version from the shared bundle (takes a few minutes)..."
    $BasePython = Join-Path $WebuiHome 'python'
    if (-not (Test-Path (Join-Path $BasePython 'python.exe'))) { Copy-Item -Recurse (Join-Path $Offline 'python') $BasePython }
    & (Join-Path $BasePython 'python.exe') -m venv $Venv
    if ($LASTEXITCODE) { throw 'Could not create the Python environment.' }
    & $Python -m pip install --no-index --find-links (Join-Path $Offline 'wheels') -r (Join-Path $Offline 'requirements.txt')
    if ($LASTEXITCODE) { throw 'Open WebUI install failed.' }
    Set-Content -Path (Join-Path $WebuiHome 'installed-version.txt') -Value "open-webui==$Version"
}
elseif ($installed -ne $Version) {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Host 'Installing uv (Python installer from astral.sh)...'
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        $env:Path = "$HOME\.local\bin;$env:Path"
    }
    Write-Host "Installing Open WebUI $Version (first run downloads about 1 GB)..."
    & uv venv --allow-existing --python 3.11 $Venv
    if ($LASTEXITCODE) { throw 'Could not create the Python 3.11 environment.' }
    & uv pip install --python $Python "open-webui==$Version"
    if ($LASTEXITCODE) { throw 'Open WebUI install failed.' }
    Set-Content -Path (Join-Path $WebuiHome 'installed-version.txt') -Value "open-webui==$Version"
}

# 2. Settings (always rewritten so every learner matches).
@'
# Written by building-with-rag open_webui_functions/setup_open_webui.ps1.
ENABLE_OPENAI_API=true
OPENAI_API_BASE_URLS=http://127.0.0.1:8000/v1
OPENAI_API_KEYS=''
ENABLE_OLLAMA_API=false
ENABLE_DIRECT_CONNECTIONS=false
ENABLE_DIRECT_INTEGRATIONS=false
ENABLE_WEB_SEARCH=false
ENABLE_CODE_EXECUTION=false
ENABLE_CODE_INTERPRETER=false
ENABLE_IMAGE_GENERATION=false
ENABLE_TITLE_GENERATION=false
ENABLE_TAGS_GENERATION=false
ENABLE_FOLLOW_UP_GENERATION=false
ENABLE_SEARCH_QUERY_GENERATION=false
ENABLE_RETRIEVAL_QUERY_GENERATION=false
ENABLE_AUTOCOMPLETE_GENERATION=false
BYPASS_EMBEDDING_AND_RETRIEVAL=true
RAG_EMBEDDING_ENGINE=openai
RAG_EMBEDDING_MODEL_AUTO_UPDATE=false
WHISPER_MODEL_AUTO_UPDATE=false
OFFLINE_MODE=true
HF_HUB_OFFLINE=1
ANONYMIZED_TELEMETRY=false
DO_NOT_TRACK=true
SCARF_NO_ANALYTICS=true
CORS_ALLOW_ORIGIN='http://127.0.0.1:8080;http://localhost:8080'
WEBUI_AUTH=false
USER_PERMISSIONS_WORKSPACE_KNOWLEDGE_ACCESS=false
USER_PERMISSIONS_WORKSPACE_TOOLS_ACCESS=false
USER_PERMISSIONS_CHAT_FILE_UPLOAD=false
USER_PERMISSIONS_CHAT_WEB_UPLOAD=false
USER_PERMISSIONS_FEATURES_DIRECT_TOOL_SERVERS=false
'@ | Set-Content -Path (Join-Path $WebuiHome 'settings.env') -Encoding ascii

# 3. Start (first start can take a few minutes while the database is created).
$env:STARTUP_WAIT = '300'
& (Join-Path $Here 'manage_open_webui.ps1') start

# 4. Load the controls, create the preview model, hide the duplicate.
& $Python (Join-Path $Here 'provision_open_webui.py')
if ($LASTEXITCODE) { throw 'Loading the RAG options controls failed.' }

Write-Host "Done. Open http://127.0.0.1:8080 and pick 'Building with RAG'."
Start-Process 'http://127.0.0.1:8080'
