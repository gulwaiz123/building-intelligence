#!/bin/sh
# Trainer-only: build a Windows offline bundle once, then share it on the LAN.
# Participants copy the bundle folder and double-click setup_open_webui.cmd;
# no internet is needed on their machines.
# Usage: sh open_webui_functions/build_offline_bundle.sh [bundle-dir]
set -eu

VERSION=0.11.4
PYTHON_URL=https://releases.astral.sh/github/python-build-standalone/releases/download/20260825/cpython-3.11.16%2B20260825-x86_64-pc-windows-msvc-install_only_stripped.tar.gz
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BUNDLE=${1:-"$HOME/open-webui-bundle"}
OFFLINE="$BUNDLE/offline"

mkdir -p "$OFFLINE/wheels"

# Resolve the exact Windows dependency set (Windows-only packages included).
echo "open-webui==$VERSION" | uv pip compile - --python-platform x86_64-pc-windows-msvc \
    --python-version 3.11 --no-header --no-annotate -o "$OFFLINE/requirements.txt" --quiet

# Download Windows wheels for exactly that set, 8 at a time. Source-only
# packages are built here; only pure-Python results (usable on Windows) are
# accepted.
echo "Downloading Windows packages (about 700 MB)..."
grep -v '^#' "$OFFLINE/requirements.txt" | grep . | xargs -P 8 -I {} sh -c '
    uvx pip download --no-deps --only-binary=:all: --platform win_amd64 --python-version 3.11 \
        --implementation cp "$1" -d "$2" --quiet 2>/dev/null \
    || uvx pip wheel --no-deps "$1" -w "$2" --quiet' _ {} "$OFFLINE/wheels"
if ls "$OFFLINE/wheels" | grep -q macosx; then
    echo "A package has no Windows build: $(ls "$OFFLINE/wheels" | grep macosx)" >&2
    exit 1
fi

# Portable Windows Python 3.11 (extracted to offline/python).
if [ ! -f "$OFFLINE/python/python.exe" ]; then
    curl -fsSL "$PYTHON_URL" | tar -xz -C "$OFFLINE"
fi

# Setup scripts and course Functions.
for f in setup_open_webui.cmd setup_open_webui.ps1 manage_open_webui.cmd manage_open_webui.ps1 \
    setup_open_webui.sh manage_open_webui.sh \
    provision_open_webui.py capstone_retrieval_controls.py building_with_rag_ui_preview.py; do
    cp "$HERE/$f" "$BUNDLE/"
done

echo "Bundle ready: $BUNDLE ($(du -sh "$BUNDLE" | cut -f1)). Share this folder on the LAN."
