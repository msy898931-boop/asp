#!/bin/sh
# Container entrypoint for a single-service host (phone app + always-on bot).
#
# The platform gives us one public port ($PORT). This script starts the backend
# host — which itself supervises the MT5 bridge and (when a token is present)
# the Telegram bot — then serves the phone app on $PORT, reverse-proxying
# /api/v1 and /bridge to the loopback backend. One service, one port, both jobs.
#
# tini is PID 1 and reaps the backend/bridge/bot children.
set -e

PORT="${PORT:-8080}"
API_PORT="${API_PORT:-8790}"
BRIDGE_PORT="${BRIDGE_PORT:-8791}"

echo "[entry] backend host starting (api=${API_PORT})"
/app/aura_backend_host --api-port "${API_PORT}" --interval-ms 5000 &

# Wait for the loopback API before opening the public port, so the first phone
# hit already sees a live reading instead of a 503.
i=0
while [ "$i" -lt 30 ]; do
    if python3 -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:${API_PORT}/api/v1/health', timeout=2)" 2>/dev/null; then
        echo "[entry] backend API is up"
        break
    fi
    i=$((i + 1))
    sleep 1
done

echo "[entry] serving phone app on 0.0.0.0:${PORT}"
exec python3 /app/mobile/serve_mobile.py \
    --host 0.0.0.0 \
    --port "${PORT}" \
    --api-port "${API_PORT}" \
    --bridge-port "${BRIDGE_PORT}"
