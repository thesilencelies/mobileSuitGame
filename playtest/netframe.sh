#!/bin/sh
# One-tap launcher for the NetFrame playtest app.
#
# Termux:  copy or symlink this into ~/.shortcuts/ and add the Termux:Widget
#          to the home screen -- tapping it starts the server and opens the
#          browser at http://127.0.0.1:8000/.
# Desktop: sh playtest/netframe.sh
#
# Deliberately POSIX sh with no shebang dependencies beyond /bin/sh, because
# Termux has no /usr/bin/env. If your Termux refuses to execute it, run it as
# `sh ~/.shortcuts/netframe` instead.
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(dirname "$HERE")
PORT="${NETFRAME_PORT:-8000}"
URL="http://127.0.0.1:${PORT}/"

PY=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)
if [ -z "$PY" ]; then
    echo "No python found. On Termux: pkg install python" >&2
    exit 1
fi

cd "$ROOT"

# Android suspends background processes aggressively; a wake lock keeps the
# server alive while you are reading a card. Harmless if not installed.
if command -v termux-wake-lock >/dev/null 2>&1; then
    termux-wake-lock || true
    trap 'termux-wake-unlock 2>/dev/null || true' EXIT INT TERM
fi

# Open the browser as soon as the port answers, then hand the terminal to the
# server so Ctrl-C (or closing the Termux session) stops it.
(
    tries=0
    while [ "$tries" -lt 40 ]; do
        if "$PY" - "$PORT" <<'PROBE' 2>/dev/null
import socket, sys
probe = socket.socket()
probe.settimeout(0.4)
sys.exit(0 if probe.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)
PROBE
        then
            if command -v termux-open-url >/dev/null 2>&1; then
                termux-open-url "$URL"
            elif command -v xdg-open >/dev/null 2>&1; then
                xdg-open "$URL" >/dev/null 2>&1
            elif command -v am >/dev/null 2>&1; then
                am start -a android.intent.action.VIEW -d "$URL" >/dev/null 2>&1
            else
                echo "Open $URL in your browser."
            fi
            exit 0
        fi
        tries=$((tries + 1))
        sleep 0.5
    done
    echo "Server did not come up on port $PORT; open $URL by hand." >&2
) &

exec "$PY" -m playtest.server --port "$PORT"
