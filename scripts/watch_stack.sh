#!/usr/bin/env bash
# Keep the local ASTRA stack up. Restarts it if either part dies.
#
#   scripts/watch_stack.sh [PUBLIC_PORT] [INTERVAL_SECONDS]
#
# Runs in the foreground (log it, or launch with nohup). It calls
# scripts/start_stack.sh, which is idempotent and only (re)starts what is down,
# so a healthy tick is a no-op. Stops when run/watchdog.stop exists, or on
# SIGTERM.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PORT="${1:-12000}"
INTERVAL="${2:-20}"
RUNDIR="$ROOT/run"
mkdir -p "$RUNDIR"
STOPFILE="$RUNDIR/watchdog.stop"
rm -f "$STOPFILE"
echo $$ > "$RUNDIR/watchdog.pid"

trap 'echo "[watch] stopping on signal"; exit 0' TERM INT

echo "[watch] guarding port :$PORT and api :8790 every ${INTERVAL}s (pid $$)"
while :; do
    [ -e "$STOPFILE" ] && { echo "[watch] stop file seen; exiting"; exit 0; }
    if ! curl -sf -m 5 "http://127.0.0.1:8790/api/v1/health" >/dev/null 2>&1 \
       || [ "$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:$PORT/)" != "200" ]; then
        echo "[watch] $(date -u +%H:%M:%S) unhealthy; restarting stack"
        "$ROOT/scripts/start_stack.sh" "$PORT" || echo "[watch] start_stack reported a problem"
    fi
    sleep "$INTERVAL"
done
