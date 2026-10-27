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
TRACKER_PORT="${TRACKER_PORT:-8793}"
ADVISOR_PORT="${ADVISOR_PORT:-8792}"
ENGINE_PORT="${ENGINE_PORT:-8794}"
SMC_PORT="${SMC_PORT:-8795}"

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

# The signal tracker is core to the phone app's Signals tab and matches what the
# local stack starts, so it runs unconditionally as a loopback sidecar.
echo "[entry] signal tracker starting (tracker=${TRACKER_PORT})"
python3 /app/tracker/signal_tracker.py --host 127.0.0.1 --port "${TRACKER_PORT}" &

# The AI advisor is an isolated second opinion behind the app's own tab. It
# always starts, and answers "not configured" until GROQ_API_KEY (or the Gemini
# equivalent) is set on the host — the app degrades instead of erroring.
# Set ASTRA_DISABLE_ADVISOR=1 to run without it: the SMC/ICT engine imports
# ai_advisor as a library and does not need the advisor's own port, so the one
# engine can stay up while this sidecar and its tab are switched off.
if [ "${ASTRA_DISABLE_ADVISOR:-0}" = "1" ]; then
    echo "[entry] AI advisor off (ASTRA_DISABLE_ADVISOR=1)"
else
    echo "[entry] AI advisor starting (advisor=${ADVISOR_PORT})"
    python3 /app/advisor/ai_advisor.py --port "${ADVISOR_PORT}" &
fi

# The AI analysis engine is a second, isolated opinion behind its own tab. Like
# the advisor it always starts and answers "not configured" until the provider
# key is set, so the tab degrades instead of erroring. It reads the same
# loopback surfaces and never touches the engine or the ledger.
# Set ASTRA_DISABLE_ENGINE=1 to run without it.
if [ "${ASTRA_DISABLE_ENGINE:-0}" = "1" ]; then
    echo "[entry] AI analysis engine off (ASTRA_DISABLE_ENGINE=1)"
else
    echo "[entry] AI analysis engine starting (engine=${ENGINE_PORT})"
    python3 /app/advisor/ai_engine.py --port "${ENGINE_PORT}" &
fi

# The SMC/ICT engine is a third isolated analyst behind its own tab. It is
# opt-in (ASTRA_ENABLE_SMC=1) and, like the others, answers "not configured"
# until the provider key is set. It computes its fact sheet from closed bars and
# never touches the engine or the ledger.
if [ "${ASTRA_ENABLE_SMC:-0}" = "1" ] && [ "${ASTRA_DISABLE_SMC:-0}" != "1" ]; then
    echo "[entry] SMC engine starting (smc=${SMC_PORT})"
    python3 /app/advisor/smc_engine.py --port "${SMC_PORT}" &
else
    echo "[entry] SMC engine off (set ASTRA_ENABLE_SMC=1 to enable)"
fi

echo "[entry] serving phone app on 0.0.0.0:${PORT}"
exec python3 /app/mobile/serve_mobile.py \
    --host 0.0.0.0 \
    --port "${PORT}" \
    --api-port "${API_PORT}" \
    --bridge-port "${BRIDGE_PORT}" \
    --tracker-port "${TRACKER_PORT}" \
    --advisor-port "${ADVISOR_PORT}" \
    --engine-port "${ENGINE_PORT}" \
    --smc-port "${SMC_PORT}"
