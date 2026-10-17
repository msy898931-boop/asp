#!/usr/bin/env python3
"""PY-0007 - Unit test for the ASTRA candle-series route.

The MetaTrader5 package is Windows-only, so it is replaced here by an in-process
fake injected into ``sys.modules`` before the bridge imports it. The *real*
``bridge_service.py`` routing, validation, closed-bar and cache logic is then
exercised over a real loopback HTTP server -- no broker, no terminal.

Checks:
  1. canonical /v1/candles?timeframe&count still works (no regression)
  2. chart alias /v1/candles?tf&limit returns {bars,timeframe,symbol,count}
  3. every bar carries the full OHLC/volume/spread shape
  4. an unknown timeframe -> 400 BAD_REQUEST
  5. limit out of range / non-integer -> 400
  6. closed-bar only: the forming bar is never returned
  7. the 5s cache absorbs an identical second request (no extra MT5 read)
  8. MT5 unavailable -> structured error, no fabricated bars

Run: python3 bridge/tests/test_candles_route.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import types
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE_DIR = os.path.abspath(os.path.join(HERE, "..", "mt5_python"))
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)

TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]
M15_SECONDS = 900

_results = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))


# --------------------------------------------------------------------------
# A minimal MetaTrader5 double (injected into sys.modules).
# --------------------------------------------------------------------------

class _Account:
    company = "FakeBroker Ltd"
    server = "FakeServer-Demo"


class _Symbol:
    def __init__(self, name: str) -> None:
        self.name = name


class _DType:
    names = ("time", "open", "high", "low", "close",
             "tick_volume", "spread", "real_volume")


class _Rates:
    """MT5-shaped rates array: ascending, index 0 is the forming bar."""

    dtype = _DType()

    def __init__(self, rows):
        self._rows = rows

    def __len__(self):
        return len(self._rows)

    def __iter__(self):
        return iter(self._rows)


def _make_fake_mt5(available: bool = True, total: int = 300,
                   last_time: int | None = None) -> types.ModuleType:
    mod = types.ModuleType("MetaTrader5")
    mod.TIMEFRAME_M1 = 1
    mod.TIMEFRAME_M5 = 5
    mod.TIMEFRAME_M15 = 15
    mod.TIMEFRAME_M30 = 30
    mod.TIMEFRAME_H1 = 16385
    mod.TIMEFRAME_H4 = 16388
    mod.TIMEFRAME_D1 = 16408
    mod.TIMEFRAME_W1 = 32769
    mod.TIMEFRAME_MN1 = 49153

    _seconds = {1: 60, 5: 300, 15: 900, 30: 1800, 16385: 3600,
                16388: 14400, 16408: 86400, 32769: 604800, 49153: 2592000}

    newest = last_time if last_time is not None else int(time.time())

    def initialize(*a, **k):
        return available

    def shutdown():
        return None

    def version():
        return (5000, 3800, "01 Jan 2026")

    def last_error():
        return (0, "no error")

    def account_info():
        return _Account()

    def symbols_get():
        return [_Symbol("XAUUSD.vx"), _Symbol("XAUUSD")]

    def symbol_select(name, enable=True):
        return True

    def symbol_info(symbol):
        return type("_Info", (), {"name": symbol, "digits": 2, "point": 0.01})()

    def symbol_info_tick(symbol):
        t = type("_Tick", (), {})()
        t.time = int(time.time())
        t.bid = t.ask = t.last = 2000.0
        t.volume = 0.0
        t.flags = 6
        return t

    def copy_rates_from_pos(symbol, timeframe, start_pos, count):
        if not available:
            return None
        interval = _seconds.get(timeframe, 900)
        anchor = newest - (newest % interval)
        bars = []
        for i in range(total):
            t = anchor - i * interval
            bars.append({
                "time": t,
                "open": 2000.0 + i,
                "high": 2001.0 + i,
                "low": 1999.0 + i,
                "close": 2000.5 + i,
                "tick_volume": 10 + i,
                "spread": 20,
                "real_volume": 0,
            })
        bars.reverse()  # ascending; bars[-1] is the forming bar
        if start_pos < 0 or count <= 0:
            return []
        end = len(bars) - start_pos
        if end <= 0:
            return []
        start = max(0, end - count)
        return _Rates(bars[start:end])

    for name, fn in list(locals().items()):
        if callable(fn) and not name.startswith("_"):
            setattr(mod, name, fn)
    return mod


# --------------------------------------------------------------------------
# Server fixture.
# --------------------------------------------------------------------------

class Server:
    def __init__(self, fake_mt5: types.ModuleType) -> None:
        sys.modules["MetaTrader5"] = fake_mt5
        # Force a fresh import so mt5_client binds the injected module.
        for stale in ("bridge_service", "mt5_client", "schemas"):
            sys.modules.pop(stale, None)
        import bridge_service

        self.bridge_service = bridge_service
        self.state = bridge_service.BridgeState("XAUUSD")
        self.state.bootstrap()
        self.server = bridge_service.BridgeServer(("127.0.0.1", 0), self.state)
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def get(self, path: str):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def scenario_happy_and_cache() -> None:
    print("Scenario: candle series, shape, closed-bar, cache")
    s = Server(_make_fake_mt5())
    try:
        # Count real MT5 reads so the cache can be proven.
        calls = {"n": 0}
        original = s.state.client.read_candles

        def counting(*a, **k):
            calls["n"] += 1
            return original(*a, **k)

        s.state.client.read_candles = counting

        code, body = s.get("/v1/candles?tf=M15&limit=10")
        check("chart alias status 200", code == 200, f"http={code}")
        check("envelope status OK", body.get("status") == "OK")
        payload = body.get("payload", {})
        check("payload has bars", isinstance(payload.get("bars"), list))
        check("payload count == 10", payload.get("count") == 10)
        check("payload timeframe", payload.get("timeframe") == "M15")
        check("payload symbol", payload.get("symbol") == "XAUUSD")
        check("10 bars returned", len(payload.get("bars", [])) == 10)
        check("first MT5 read happened", calls["n"] == 1, f"reads={calls['n']}")

        bar = payload["bars"][0]
        check("bar has full shape",
              set(bar.keys()) >= {"time", "open", "high", "low", "close",
                                  "tick_volume", "spread", "real_volume"},
              str(sorted(bar.keys())))
        check("bar values are numeric",
              all(isinstance(bar[k], (int, float)) for k in bar))

        times = [b["time"] for b in payload["bars"]]
        check("bars strictly increasing",
              all(times[i] < times[i + 1] for i in range(len(times) - 1)))

        # Closed-bar only: the newest closed bar excludes the forming bar.
        newest = int(time.time())
        aligned = newest - (newest % M15_SECONDS)
        check("forming bar excluded",
              payload.get("newest_closed_time") == aligned - M15_SECONDS,
              f"newest={payload.get('newest_closed_time')} expected={aligned - M15_SECONDS}")
        check("no bar reaches the forming bar", times[-1] <= aligned - M15_SECONDS)

        # Cache: an identical request within TTL must not read MT5 again.
        code2, body2 = s.get("/v1/candles?tf=M15&limit=10")
        check("cached request 200", code2 == 200)
        check("cache served (no extra MT5 read)", calls["n"] == 1,
              f"reads={calls['n']}")
        check("cache returns same count",
              body2.get("payload", {}).get("count") == 10)

        # A different limit is a different cache key -> one more read.
        code3, _ = s.get("/v1/candles?tf=M15&limit=20")
        check("different limit reads MT5 again", calls["n"] == 2,
              f"reads={calls['n']}")
        check("different limit 200", code3 == 200)

        # Canonical params must still work (no regression).
        code4, body4 = s.get("/v1/candles?symbol=XAUUSD&timeframe=M15&count=5&closed_only=true")
        check("canonical route still 200", code4 == 200, f"http={code4}")
        check("canonical route count 5",
              body4.get("payload", {}).get("count") == 5)
        check("canonical route has candles key",
              isinstance(body4.get("payload", {}).get("candles"), list))
    finally:
        s.stop()


def scenario_validation() -> None:
    print("Scenario: tf/limit validation")
    s = Server(_make_fake_mt5())
    try:
        code, body = s.get("/v1/candles?tf=XYZ&limit=10")
        check("unknown tf -> 400", code == 400, f"http={code}")
        check("unknown tf code BAD_REQUEST",
              body.get("error", {}).get("code") == "BAD_REQUEST")

        code, body = s.get("/v1/candles?tf=M15&limit=0")
        check("limit 0 -> 400", code == 400, f"http={code}")

        code, body = s.get("/v1/candles?tf=M15&limit=1001")
        check("limit 1001 -> 400", code == 400, f"http={code}")

        code, body = s.get("/v1/candles?tf=M15&limit=abc")
        check("non-integer limit -> 400", code == 400, f"http={code}")

        # default limit when omitted
        code, body = s.get("/v1/candles?tf=M15")
        check("omitted limit defaults (200)", code == 200, f"http={code}")
    finally:
        s.stop()


def scenario_mt5_unavailable() -> None:
    print("Scenario: MT5 unavailable")
    s = Server(_make_fake_mt5(available=False))
    try:
        _code, body = s.get("/v1/candles?tf=M15&limit=10")
        check("unavailable -> structured error", body.get("status") == "ERROR",
              str(body.get("error", {}).get("code")))
        check("unavailable code MT5_TERMINAL_UNAVAILABLE",
              body.get("error", {}).get("code") == "MT5_TERMINAL_UNAVAILABLE")
        check("no fabricated bars", body.get("payload") in ({}, None))
    finally:
        s.stop()


def main() -> int:
    print(f"Repo bridge dir: {BRIDGE_DIR}")
    scenario_happy_and_cache()
    scenario_validation()
    scenario_mt5_unavailable()
    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print(f"{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
