#!/bin/sh
# Control only an Open WebUI process started by this script. It never kills a
# process merely because it happens to use port 8080.
set -eu

WEBUI_HOME=${WEBUI_HOME:-"$HOME/open-webui"}
LAUNCHER="$WEBUI_HOME/start.sh"
PID_FILE="$WEBUI_HOME/server.pid"
LOG_FILE="$WEBUI_HOME/server.log"
STARTUP_WAIT=${STARTUP_WAIT:-15}
HEALTH_URL=${OPEN_WEBUI_HEALTH_URL:-"http://127.0.0.1:8080/api/version"}

is_healthy() {
    curl -fsS --max-time 3 "$HEALTH_URL" >/dev/null 2>&1
}

owned_pid() {
    [ -f "$PID_FILE" ] || return 1
    pid=$(tr -d '[:space:]' < "$PID_FILE")
    case "$pid" in
        ''|*[!0-9]*) return 1 ;;
    esac
    command=$(ps -p "$pid" -o command= 2>/dev/null || true)
    case "$command" in
        *"$WEBUI_HOME/.venv/bin/open-webui"*) printf '%s\n' "$pid" ;;
        *) return 1 ;;
    esac
}

start() {
    if is_healthy; then
        echo "Open WebUI is already healthy at http://127.0.0.1:8080."
        return 0
    fi
    if [ ! -x "$LAUNCHER" ]; then
        echo "Open WebUI launcher not found or not executable: $LAUNCHER" >&2
        exit 1
    fi
    if [ -f "$PID_FILE" ] && ! owned_pid >/dev/null; then
        echo "Refusing to replace an unverified PID file: $PID_FILE" >&2
        echo "Remove it only after checking that it is stale." >&2
        exit 1
    fi
    nohup "$LAUNCHER" >>"$LOG_FILE" 2>&1 &
    pid=$!
    printf '%s\n' "$pid" > "$PID_FILE"
    tries=0
    while [ "$tries" -lt "$STARTUP_WAIT" ]; do
        if is_healthy; then
            echo "Open WebUI started at http://127.0.0.1:8080 (PID $pid)."
            return 0
        fi
        sleep 1
        tries=$((tries + 1))
    done
    echo "Open WebUI did not become healthy; inspect $LOG_FILE" >&2
    exit 1
}

stop() {
    if ! pid=$(owned_pid); then
        echo "No Open WebUI process started by this script was found. Nothing stopped."
        return 0
    fi
    kill -TERM "$pid"
    tries=0
    while kill -0 "$pid" 2>/dev/null && [ "$tries" -lt 15 ]; do
        sleep 1
        tries=$((tries + 1))
    done
    if kill -0 "$pid" 2>/dev/null; then
        echo "Open WebUI PID $pid did not stop; inspect it before taking further action." >&2
        exit 1
    fi
    rm -f "$PID_FILE"
    echo "Open WebUI stopped."
}

status() {
    if is_healthy; then
        echo "Open WebUI is healthy at http://127.0.0.1:8080."
    else
        echo "Open WebUI is not responding at http://127.0.0.1:8080."
        return 1
    fi
}

case "${1:-}" in
    start) start ;;
    stop) stop ;;
    restart) stop; start ;;
    status) status ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}" >&2
        exit 64
        ;;
esac
