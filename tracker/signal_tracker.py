#!/usr/bin/env python3
"""ASTRA signal tracker: clean trade ideas plus an honest scoreboard.

Two jobs, both deliberately separate from the decision engine:

  1. Replay the engine chain over closed bars and emit trade ideas. The rules
     are the same ones the C++ engines use (FeatureEngine: ema20/50, rsi14,
     atr14, stdev20; StructureEngine: swing lookback 2; RegimeEngine thresholds
     0.0008/0.012/0.0015; SignalEngine: rsi 52/48, stop 1.5*ATR, target
     2.5*ATR), so an idea matches what the engine would decide on those bars.

  2. Score those ideas against what price actually did, and report the result
     with an explicit "not enough data yet" state rather than a fabricated win
     rate.

What makes the count honest:

  * One idea per direction at a time. The raw chain re-votes on every bar, so
    a trend can emit the same trade many times. A repeat is the *same* trade,
    never a new one, so it is suppressed while one is live.

  * A repeat in the opposite direction closes the live idea as "reversed"
    before opening the new one, so the ledger never drifts from the read.

  * Exits mirror the shadow simulator: stop takes precedence over target when
    both are touched in one bar (conservative), and a horizon caps how long an
    idea may stay open.

  * Spread is charged on resolution, because a gross edge is not a net one.

Nothing here writes to the engine's prediction ledger. Every record is stamped
unvalidated, and the summary refuses to state a rate below MIN_SCORED trades.

Run: python3 tracker/signal_tracker.py --port 8793
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

from replay import (  # noqa: E402
    DEFAULT_COOLDOWN_BARS,
    DEFAULT_COST_USD,
    DEFAULT_HORIZON_BARS,
    DEFAULT_MIN_SCORED,
    Signal,
    build_signals,
    explain_last_bar,
    summarize,
)

API_BASE = os.environ.get("ASTRA_TRACKER_API", "http://127.0.0.1:8790")
BRIDGE_BASE = os.environ.get("ASTRA_TRACKER_BRIDGE", "http://127.0.0.1:8791")
SYMBOL = os.environ.get("ASTRA_TRACKER_SYMBOL", "XAUUSD")
TIMEFRAME = os.environ.get("ASTRA_TRACKER_TIMEFRAME", "M15")
LOOKBACK_BARS = int(os.environ.get("ASTRA_TRACKER_LOOKBACK", "200"))
POLL_SECONDS = float(os.environ.get("ASTRA_TRACKER_POLL", "20"))
HTTP_TIMEOUT = float(os.environ.get("ASTRA_TRACKER_TIMEOUT", "15"))
COST_USD = float(os.environ.get("ASTRA_TRACKER_SPREAD", str(DEFAULT_COST_USD)))
HORIZON_BARS = int(os.environ.get("ASTRA_TRACKER_HORIZON", str(DEFAULT_HORIZON_BARS)))
COOLDOWN_BARS = int(os.environ.get("ASTRA_TRACKER_COOLDOWN", str(DEFAULT_COOLDOWN_BARS)))
MIN_SCORED = int(os.environ.get("ASTRA_TRACKER_MIN_SCORED", str(DEFAULT_MIN_SCORED)))
ACCOUNT_USD = float(os.environ.get("ASTRA_ACCOUNT_USD", "200"))
RISK_PCT = float(os.environ.get("ASTRA_RISK_PCT", "1.0"))
LOG_PATH = os.environ.get("ASTRA_TRACKER_LOG",
                          os.path.join(ROOT, "run", "signal_trades.jsonl"))
_USER_AGENT = os.environ.get("ASTRA_TRACKER_USER_AGENT", "ASTRA-tracker/1.0")

SCHEMA_VERSION = "1.0"


def _get_json(url: str) -> dict | None:
    try:
        req = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def fetch_bars() -> list[dict]:
    """Closed bars from the bridge, oldest first. Empty on any failure."""
    env = _get_json("%s/v1/candles?tf=%s&limit=%d"
                    % (BRIDGE_BASE, TIMEFRAME, LOOKBACK_BARS))
    if not env or not isinstance(env.get("payload"), dict):
        return []
    bars = env["payload"].get("bars") or []
    return [b for b in bars if isinstance(b, dict)]


def append_log(records: list[dict]) -> None:
    if not records:
        return
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass  # Logging is best-effort; it must never break the response.


class Tracker:
    """Polls the bridge, keeps the latest clean signal set, never blocks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._result: dict = {
            "schema_version": SCHEMA_VERSION,
            "symbol": SYMBOL,
            "timeframe": TIMEFRAME,
            "generated_at_utc": 0,
            "bars": 0,
            "signals": [],
            "summary": summarize([], MIN_SCORED),
            "note": "tracker starting; no bars read yet",
        }

    def current(self) -> dict:
        with self._lock:
            return self._result

    def cycle(self) -> None:
        bars = fetch_bars()
        if not bars:
            with self._lock:
                self._result["note"] = "bridge unreachable or no closed bars"
            return
        signals: list[Signal] = build_signals(
            bars, cost_usd=COST_USD, horizon_bars=HORIZON_BARS,
            cooldown_bars=COOLDOWN_BARS)
        payload = [s.as_dict() for s in signals]
        summary = summarize(signals, MIN_SCORED)
        status = explain_last_bar(
            bars, cost_usd=COST_USD, horizon_bars=HORIZON_BARS,
            cooldown_bars=COOLDOWN_BARS, account_usd=ACCOUNT_USD,
            risk_pct=RISK_PCT)
        with self._lock:
            self._result = {
                "schema_version": SCHEMA_VERSION,
                "symbol": SYMBOL,
                "timeframe": TIMEFRAME,
                "generated_at_utc": int(time.time()),
                "bars": len(bars),
                "cost_usd": COST_USD,
                "horizon_bars": HORIZON_BARS,
                "cooldown_bars": COOLDOWN_BARS,
                "account_usd": ACCOUNT_USD,
                "risk_pct": RISK_PCT,
                "signals": payload,
                "summary": summary,
                "last_bar": status,
                "note": ("Trade ideas replayed from closed bars. Unvalidated, "
                         "not financial advice."),
            }
        # Only resolved records are appended, so the log stays a ledger.
        append_log([s.as_dict() for s in signals if s.resolved])

    def run_forever(self) -> None:
        while True:
            try:
                self.cycle()
            except Exception as exc:  # never let one bad cycle kill the poller
                sys.stderr.write("[tracker] cycle error: %r\n" % (exc,))
            time.sleep(POLL_SECONDS)


class Handler(BaseHTTPRequestHandler):
    tracker = Tracker()

    def log_message(self, fmt, *args):  # quieter logs
        sys.stderr.write("[tracker] %s\n" % (fmt % args))

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/v1/signals", "/signals"):
            self._json(200, {"tracker": self.tracker.current()})
        elif path in ("/v1/summary", "/summary"):
            self._json(200, {"summary": self.tracker.current()["summary"]})
        elif path in ("/health", "/v1/health"):
            self._json(200, {"service": "signal-tracker", "status": "OK",
                             "symbol": SYMBOL, "timeframe": TIMEFRAME})
        else:
            self._json(404, {"error": "unknown route", "path": path})

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def _json(self, status: int, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ASTRA signal tracker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8793)
    args = parser.parse_args(argv)

    tracker = Handler.tracker
    tracker.cycle()  # prime the cache before serving
    thread = threading.Thread(target=tracker.run_forever, daemon=True)
    thread.start()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    sys.stderr.write("[tracker] listening on http://%s:%d (symbol=%s tf=%s)\n"
                     % (args.host, args.port, SYMBOL, TIMEFRAME))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
