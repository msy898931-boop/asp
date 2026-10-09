#!/usr/bin/env bash
# TEL-0103 - Start the ASTRA backend and the Telegram bot together.
#
# Development/ops helper. The packaged .exe will instead have the host launch
# the bot as a supervised sidecar (mirroring the MT5 bridge); until then this
# script runs both from the source tree.
#
# Requires TELEGRAM_BOT_TOKEN (and optionally TELEGRAM_CHAT_ID) in the env.
set -u

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"

host_bin="${ASTRA_HOST_BIN:-}"
if [[ -z "$host_bin" ]]; then
  for candidate in \
    "$repo_root/dist/ASTRA/aura_backend_host" \
    "$repo_root/build/aura_backend_host" \
    "$repo_root/frontend/qt/build/aura_backend_host"; do
    if [[ -x "$candidate" ]]; then host_bin="$candidate"; break; fi
  done
fi
api_port="${ASTRA_API_PORT:-8790}"

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  echo "[ERROR] TELEGRAM_BOT_TOKEN is not set" >&2
  exit 2
fi

if [[ -z "$host_bin" || ! -x "$host_bin" ]]; then
  echo "[ERROR] backend host not found; build it or set ASTRA_HOST_BIN" >&2
  exit 2
fi

cleanup() {
  [[ -n "${bot_pid:-}" ]] && kill "$bot_pid" 2>/dev/null
  [[ -n "${host_pid:-}" ]] && kill "$host_pid" 2>/dev/null
  wait 2>/dev/null
}
trap cleanup EXIT INT TERM

echo "[run] starting backend on 127.0.0.1:$api_port ..."
"$host_bin" --dev-system-python --api-port "$api_port" &
host_pid=$!

# Give the loopback API a moment to bind before the bot starts polling it.
sleep 2

echo "[run] starting Telegram bot ..."
python3 "$here/astra_bot.py" --api "http://127.0.0.1:$api_port/api/v1" &
bot_pid=$!

wait -n "$host_pid" "$bot_pid"
