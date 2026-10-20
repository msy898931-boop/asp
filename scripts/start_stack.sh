#!/usr/bin/env bash
# Start (or restart) the ASTRA stack locally and verify it end to end.
#
#   scripts/start_stack.sh [PUBLIC_PORT]
#
# PUBLIC_PORT defaults to 12000.
#
# The Telegram bot is OFF unless ASTRA_ENABLE_BOT=1 is set explicitly. This is
# deliberate: a token in the environment (e.g. the one the sandbox injects for
# secrets) must NOT silently start a second poller, because the deployed
# instance already owns the token and Telegram answers 409 and starves both.
# Set ASTRA_ENABLE_BOT=1 only when THIS machine is meant to own the bot.
#
# Idempotent: already-running parts are left alone. PID files live in run/.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PORT="${1:-12000}"
API_PORT=8790
BRIDGE_PORT=8791
ADVISOR_PORT=8792
ENGINE_PORT=8794
TRACKER_PORT=8793
RUNDIR="$ROOT/run"
mkdir -p "$RUNDIR"

# Load local secrets (gitignored) so a restart does not need the key on the
# command line. Existing environment values win, so an injected secret is not
# overridden by a stale file.
if [ -f "$ROOT/.env" ]; then
    while IFS='=' read -r _k _v; do
        case "$_k" in ''|'#'*) continue ;; esac
        [ -n "${!_k:-}" ] && continue
        _v="${_v%\"}"; _v="${_v#\"}"
        export "$_k=$_v"
    done < "$ROOT/.env"
fi

alive() {
    [ -n "${1:-}" ] || return 1
    kill -0 "$1" 2>/dev/null || return 1
    # A zombie still answers kill -0; treat Z/X (dead) states as not alive.
    st="$(ps -o stat= -p "$1" 2>/dev/null | tr -d ' ')"
    case "$st" in Z*|X*) return 1 ;; esac
    return 0
}
pid_of() { [ -f "$RUNDIR/$1.pid" ] && cat "$RUNDIR/$1.pid" || echo ""; }

# The host resolves resources/ next to its own binary. Real directories, not
# symlinks: tests/integration stages this tree by copying from the repo, and a
# symlink back to the repo turns that copy into a same-file error.
mkdir -p build/resources/python build/resources/bridge
[ -e build/resources/bridge/mt5_python ] || cp -R "$ROOT/bridge/mt5_python" \
    build/resources/bridge/
[ -e build/resources/python/python3 ] || ln -sfn "$(command -v python3)" \
    build/resources/python/python3

BIN="$ROOT/build/aura_backend_host"
[ -x "$BIN" ] || { echo "ERROR: $BIN not built (see docs/RUNBOOK.md)"; exit 2; }

# --- backend host (bridge + optional bot) --------------------------------
if alive "$(pid_of host)"; then
    echo "[start] host already running (pid $(pid_of host))"
else
    # Opt-in only: a token present in the environment is not enough.
    if [ "${ASTRA_ENABLE_BOT:-0}" = "1" ] && [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
        echo "[start] host WITH telegram bot (ASTRA_ENABLE_BOT=1)"
    else
        TELEGRAM_BOT_TOKEN=""
        echo "[start] host without telegram bot (set ASTRA_ENABLE_BOT=1 to own it)"
    fi
    TELEGRAM_BOT_TOKEN="$TELEGRAM_BOT_TOKEN" ASTRA_DATA_SOURCE="${ASTRA_DATA_SOURCE:-live}" \
        setsid nohup "$BIN" --dev-system-python \
            --api-port "$API_PORT" --interval-ms 5000 \
            > "$RUNDIR/host.log" 2>&1 < /dev/null &
    echo $! > "$RUNDIR/host.pid"
    echo "[start] host launched (pid $!)"
fi

# --- phone app / PWA -----------------------------------------------------
if alive "$(pid_of mobile)"; then
    echo "[start] mobile already running (pid $(pid_of mobile))"
else
    setsid nohup python3 "$ROOT/mobile/serve_mobile.py" \
        --host 0.0.0.0 --port "$PORT" \
        --api-port "$API_PORT" --bridge-port "$BRIDGE_PORT" \
        --advisor-port "$ADVISOR_PORT" --tracker-port "$TRACKER_PORT" \
        --engine-port "$ENGINE_PORT" \
        > "$RUNDIR/mobile.log" 2>&1 < /dev/null &
    echo $! > "$RUNDIR/mobile.pid"
    echo "[start] mobile launched on :$PORT (pid $!)"
fi

# --- AI advisor (isolated, optional) -------------------------------------
# Its own process and port. It never touches the engine or the ledger, and if
# it fails to start the rest of the stack is unaffected. Opt out with
# ASTRA_DISABLE_ADVISOR=1.
if [ "${ASTRA_DISABLE_ADVISOR:-0}" = "1" ]; then
    echo "[start] ai advisor disabled (ASTRA_DISABLE_ADVISOR=1)"
elif alive "$(pid_of advisor)"; then
    echo "[start] ai advisor already running (pid $(pid_of advisor))"
else
    setsid nohup python3 "$ROOT/advisor/ai_advisor.py" --port "$ADVISOR_PORT" \
        > "$RUNDIR/advisor.log" 2>&1 < /dev/null &
    echo $! > "$RUNDIR/advisor.pid"
    # Groq is the default provider (global free tier); Gemini and Mistral are
    # opt-in. The key name follows the provider.
    _prov="${ASTRA_ADVISOR_PROVIDER:-groq}"
    case "$_prov" in
        gemini)  _key="${GEMINI_API_KEY:-}";  _keyname="GEMINI_API_KEY" ;;
        mistral) _key="${MISTRAL_API_KEY:-}"; _keyname="MISTRAL_API_KEY" ;;
        *)       _key="${GROQ_API_KEY:-}";    _keyname="GROQ_API_KEY" ;;
    esac
    if [ -n "$_key" ]; then
        echo "[start] ai advisor launched on :$ADVISOR_PORT (pid $!) - provider $_prov, configured"
    else
        echo "[start] ai advisor launched on :$ADVISOR_PORT (pid $!) - provider $_prov, not configured (set $_keyname)"
    fi
fi

# --- AI engine (isolated, opt-in) ----------------------------------------
# Its own process and port, separate from the advisor. It reads every timeframe
# and the news and asks the model for trades; it never touches the engine or the
# ledger. Off by default so it is switched on deliberately; opt in with
# ASTRA_ENABLE_ENGINE=1 and out with ASTRA_DISABLE_ENGINE=1.
if [ "${ASTRA_DISABLE_ENGINE:-0}" = "1" ]; then
    echo "[start] ai engine disabled (ASTRA_DISABLE_ENGINE=1)"
elif [ "${ASTRA_ENABLE_ENGINE:-0}" != "1" ]; then
    echo "[start] ai engine off (set ASTRA_ENABLE_ENGINE=1 to enable)"
elif alive "$(pid_of engine)"; then
    echo "[start] ai engine already running (pid $(pid_of engine))"
else
    setsid nohup python3 "$ROOT/advisor/ai_engine.py" --port "$ENGINE_PORT" \
        > "$RUNDIR/engine.log" 2>&1 < /dev/null &
    echo $! > "$RUNDIR/engine.pid"
    _prov="${ASTRA_ADVISOR_PROVIDER:-groq}"
    case "$_prov" in
        gemini)  _key="${GEMINI_API_KEY:-}";  _keyname="GEMINI_API_KEY" ;;
        mistral) _key="${MISTRAL_API_KEY:-}"; _keyname="MISTRAL_API_KEY" ;;
        *)       _key="${GROQ_API_KEY:-}";    _keyname="GROQ_API_KEY" ;;
    esac
    if [ -n "$_key" ]; then
        echo "[start] ai engine launched on :$ENGINE_PORT (pid $!) - provider $_prov, configured"
    else
        echo "[start] ai engine launched on :$ENGINE_PORT (pid $!) - provider $_prov, not configured (set $_keyname)"
    fi
fi

# --- signal tracker (isolated, optional) ---------------------------------
# Replays the engine chain over closed bars and scores the trade ideas. Its
# own process and port, like the advisor: it never touches the engine or the
# ledger. Opt out with ASTRA_DISABLE_TRACKER=1.
if [ "${ASTRA_DISABLE_TRACKER:-0}" = "1" ]; then
    echo "[start] signal tracker disabled (ASTRA_DISABLE_TRACKER=1)"
elif alive "$(pid_of tracker)"; then
    echo "[start] signal tracker already running (pid $(pid_of tracker))"
else
    setsid nohup python3 "$ROOT/tracker/signal_tracker.py" --port "$TRACKER_PORT" \
        > "$RUNDIR/tracker.log" 2>&1 < /dev/null &
    echo $! > "$RUNDIR/tracker.pid"
    echo "[start] signal tracker launched on :$TRACKER_PORT (pid $!)"
fi

# --- telegram notifier (optional, opt-in) --------------------------------
# Off unless ASTRA_ENABLE_BOT=1 so a bot that talks to a chat is switched on
# deliberately. Needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
if [ "${ASTRA_ENABLE_BOT:-0}" != "1" ]; then
    echo "[start] telegram bot off (set ASTRA_ENABLE_BOT=1 to enable)"
elif alive "$(pid_of bot)"; then
    echo "[start] telegram bot already running (pid $(pid_of bot))"
elif [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
    echo "[start] telegram bot not started: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID unset"
else
    setsid nohup python3 "$ROOT/bot/telegram_bot.py" \
        > "$RUNDIR/bot.log" 2>&1 < /dev/null &
    echo $! > "$RUNDIR/bot.pid"
    echo "[start] telegram bot launched (pid $!)"
fi

# --- wait for readiness --------------------------------------------------
echo "[start] waiting for the backend API ..."
for _ in $(seq 1 30); do
    if curl -sf -m 3 "http://127.0.0.1:$API_PORT/api/v1/health" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

echo "[start] waiting for the app on :$PORT ..."
code=000
for _ in $(seq 1 15); do
    code=$(curl -s -o /dev/null -w '%{http_code}' -m 3 "http://127.0.0.1:$PORT/")
    [ "$code" = "200" ] && break
    sleep 1
done

echo
echo "[check] app  :$PORT        -> $code"
health=$(curl -s -m 10 "http://127.0.0.1:$API_PORT/api/v1/health" || echo "")
echo "$health" | grep -q '"aggregate":"ONLINE"' \
    && echo "[check] api $API_PORT -> ONLINE" \
    || echo "[check] api $API_PORT -> NOT ONLINE: ${health:0:80}"
[ "$code" = "200" ] || { echo; echo "stack did not come up; see run/host.log run/mobile.log"; exit 1; }
echo "[ok] ASTRA stack is up."
