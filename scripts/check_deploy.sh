#!/usr/bin/env bash
# Verify a deployed ASTRA instance end to end.
#
#   scripts/check_deploy.sh https://your-app.onrender.com
#
# Checks the phone app, the backend API, a live reading, and — when a bot token
# is in the environment — whether the deployed bot is the one polling Telegram.
# On the free plan the first request may take ~1 minute: the service cold-starts.
set -u

BASE="${1:-${ASTRA_URL:-https://astra-xlp9.onrender.com}}"
BASE="${BASE%/}"
pass=0
fail=0

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; pass=$((pass + 1)); }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; fail=$((fail + 1)); }

echo "ASTRA deploy check -> $BASE"
echo

echo "phone app (/)"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 90 "$BASE/")
[ "$code" = "200" ] && ok "200" || bad "expected 200, got $code"

echo "backend api (/api/v1/health)"
body=$(curl -s --max-time 90 "$BASE/api/v1/health")
echo "$body" | grep -q '"aggregate":"ONLINE"' \
    && ok "aggregate ONLINE" || bad "not ONLINE: ${body:0:120}"

echo "live reading (/bridge/v1/candles)"
body=$(curl -s --max-time 90 "$BASE/bridge/v1/candles?tf=M15&limit=2")
echo "$body" | grep -q '"close"' \
    && ok "$(echo "$body" | python3 -c 'import sys,json;print("last_close",json.load(sys.stdin)["payload"]["bars"][-1]["close"])' 2>/dev/null || echo "candles returned")" \
    || bad "no candles: ${body:0:120}"

echo "bot polling telegram"
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
    # A long poll from the deployed bot means our own getUpdates is the *newer*
    # call: we get 200 and the bot is bumped with a 409. So a 200 here proves
    # nothing about the bot, and a 409 means we lost the race to a second
    # poller — the genuine split-brain failure. Treat 200 as inconclusive.
    code=$(curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getUpdates?timeout=1" \
        | python3 -c 'import sys,json;print(json.load(sys.stdin).get("error_code","200"))' 2>/dev/null)
    if [ "$code" = "409" ]; then
        bad "split-brain: another poller holds the token (409)"
    else
        ok "no split-brain detected (external probe is inconclusive; see host logs)"
    fi
else
    echo "  (set TELEGRAM_BOT_TOKEN to check for a split-brain)"
fi

echo
echo "passed=$pass failed=$fail"
[ "$fail" -eq 0 ]
