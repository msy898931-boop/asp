#!/usr/bin/env bash
# Stop the local ASTRA stack started by scripts/start_stack.sh.
#
#   scripts/stop_stack.sh
#
# Only the PIDs recorded in run/*.pid are signalled, so unrelated processes are
# never touched.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNDIR="$ROOT/run"

stop_one() {
    name="$1"
    pidfile="$RUNDIR/$name.pid"
    [ -f "$pidfile" ] || { echo "[stop] $name: no pid file"; return; }
    pid="$(cat "$pidfile")"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        # start_stack used setsid, so the process leads its own group.
        kill "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null
        for _ in 1 2 3 4 5; do
            kill -0 "$pid" 2>/dev/null || break
            sleep 1
        done
        if kill -0 "$pid" 2>/dev/null; then
            kill -KILL "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null
            echo "[stop] $name: forced (pid $pid)"
        else
            echo "[stop] $name: stopped (pid $pid)"
        fi
    else
        echo "[stop] $name: not running"
    fi
    rm -f "$pidfile"
}

stop_one mobile
stop_one bot
stop_one tracker
stop_one advisor
stop_one host
sleep 2
echo "[stop] done. Remaining astra processes:"
ps -ef | grep -E "[a]ura_backend_host|[s]erve_mobile.py|[b]ridge_service.py|[a]i_advisor.py|[s]ignal_tracker.py|[t]elegram_bot.py" || echo "  none"
