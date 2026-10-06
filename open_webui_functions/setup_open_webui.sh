#!/bin/sh
# One-shot classroom setup: install pinned Open WebUI, write its settings,
# start it, and load the RAG options controls. Safe to re-run; it never
# deletes chats or accounts in $WEBUI_HOME/data.
set -eu

VERSION=0.11.4
WEBUI_HOME=${WEBUI_HOME:-"$HOME/open-webui"}
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VENV="$WEBUI_HOME/.venv"
export WEBUI_HOME

mkdir -p "$WEBUI_HOME"

# 1. Install Open WebUI (skipped when the pinned version is already present).
if [ "$("$VENV/bin/python" -c 'import importlib.metadata as m; print(m.version("open-webui"))' 2>/dev/null || true)" != "$VERSION" ]; then
    echo "Installing Open WebUI $VERSION (first run downloads about 1 GB)..."
    if command -v uv >/dev/null 2>&1; then
        uv venv --allow-existing --python 3.11 "$VENV"
        uv pip install --python "$VENV/bin/python" "open-webui==$VERSION"
    elif command -v python3.11 >/dev/null 2>&1; then
        python3.11 -m venv "$VENV"
        "$VENV/bin/pip" install --upgrade pip
        "$VENV/bin/pip" install "open-webui==$VERSION"
    else
        echo "Installing uv (Python installer from astral.sh)..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        PATH="$HOME/.local/bin:$PATH"
        uv venv --allow-existing --python 3.11 "$VENV"
        uv pip install --python "$VENV/bin/python" "open-webui==$VERSION"
    fi
    echo "open-webui==$VERSION" > "$WEBUI_HOME/installed-version.txt"
fi

# 2. Settings and launcher (always rewritten so every learner matches).
cat > "$WEBUI_HOME/settings.env" <<'EOF'
# Written by building-with-rag open_webui_functions/setup_open_webui.sh.
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
EOF

cat > "$WEBUI_HOME/start.sh" <<'EOF'
#!/bin/sh
set -eu
OPEN_WEBUI_HOME=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$OPEN_WEBUI_HOME"
set -a
. "$OPEN_WEBUI_HOME/settings.env"
set +a
export DATA_DIR="$OPEN_WEBUI_HOME/data"
exec "$OPEN_WEBUI_HOME/.venv/bin/open-webui" serve --host 127.0.0.1 --port 8080
EOF
chmod 700 "$WEBUI_HOME/start.sh"

# 3. Start (first start can take a few minutes while the database is created).
STARTUP_WAIT=${STARTUP_WAIT:-300} sh "$HERE/manage_open_webui.sh" start

# 4. Load the controls, create the preview model, hide the duplicate.
"$VENV/bin/python" "$HERE/provision_open_webui.py"

echo "Done. Open http://127.0.0.1:8080 and pick 'Building with RAG'."
