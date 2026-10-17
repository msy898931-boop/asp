#!/usr/bin/env python3
"""T06 - Real MT5 bridge integration test (no broker required).

Boots the actual bridge_service.py as a subprocess with a stub MetaTrader5
module on PYTHONPATH, then exercises the real HTTP loopback contract:

  1. bind: loopback-only enforced (0.0.0.0 refused)
  2. nine timeframes + M15/H4 authority in the handshake
  3. closed-bar semantics: the forming bar (index 0) is never returned
  4. freshness: a current feed reads FRESH
  5. structured errors: MT5_UNAVAILABLE, STALE_DATA, INSUFFICIENT_HISTORY
  6. version handshake: protocol/schema mismatch rejected with 400
  7. clean shutdown on SIGTERM

Exit code 0 = all passed. This test does NOT modify production src/.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
BRIDGE_DIR = os.path.join(REPO, "bridge", "mt5_python")
FAKE_MT5 = os.path.join(HERE, "fake_mt5")

TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]
M15_SECONDS = 900

_results = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Bridge:
    def __init__(self, port: int, env_extra: dict) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = FAKE_MT5 + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONUNBUFFERED"] = "1"
        env.update(env_extra)
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(BRIDGE_DIR, "bridge_service.py"),
             "--host", "127.0.0.1", "--port", str(port), "--symbol", "XAUUSD"],
            cwd=BRIDGE_DIR, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.port = port

    def wait_ready(self, timeout: float = 10.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                self.get("/v1/handshake")
                return True
            except Exception:  # noqa: BLE001
                time.sleep(0.1)
        return False

    def get(self, path: str, headers: dict | None = None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def stop_sigterm(self) -> tuple[bool, float]:
        t0 = time.time()
        self.proc.send_signal(signal.SIGTERM)
        while time.time() - t0 < 5:
            if self.proc.poll() is not None:
                return True, time.time() - t0
            time.sleep(0.02)
        self.proc.kill()
        return False, time.time() - t0


def scenario_fresh():
    print("Scenario: fresh feed, closed-bar semantics, 9 timeframes")
    port = free_port()
    last_time = int(time.time())
    b = Bridge(port, {"FAKE_MT5_AVAILABLE": "1", "FAKE_MT5_TOTAL_BARS": "300",
                      "FAKE_MT5_LAST_TIME": str(last_time)})
    try:
        assert b.wait_ready(), "bridge did not become ready"
        _, hs = b.get("/v1/handshake")
        p = hs["payload"]
        check("handshake lists 9 timeframes", p["timeframes"] == TIMEFRAMES)
        check("M15 primary operational", p["primary_operational_timeframe"] == "M15")
        check("H4 primary structural", p["primary_structural_timeframe"] == "H4")
        check("handshake loopback_only", p["loopback_only"] is True)

        _, c = b.get("/v1/candles?symbol=XAUUSD&timeframe=M15&count=5&closed_only=true")
        payload = c["payload"]
        check("candles status OK", c["status"] == "OK", c.get("error", {}).get("code", ""))
        check("returned requested count", payload.get("count") == 5)
        aligned = last_time - (last_time % M15_SECONDS)
        expected_newest_closed = aligned - M15_SECONDS
        check("forming bar (index 0) excluded",
              payload.get("newest_closed_time") == expected_newest_closed,
              f"newest_closed={payload.get('newest_closed_time')} expected={expected_newest_closed}")
        check("closed_only echoed true", payload.get("closed_only") is True)
        check("fresh feed reads FRESH", payload.get("freshness") == "FRESH",
              str(payload.get("freshness")))
        # Oldest-first, strictly increasing, all closed (< forming time).
        times = [x["time"] for x in payload["candles"]]
        check("candles strictly increasing", all(times[i] < times[i + 1] for i in range(len(times) - 1)))
        check("no bar reaches the forming bar", max(times) < aligned)
    finally:
        b.stop_sigterm()


def scenario_stale():
    print("Scenario: stale feed")
    port = free_port()
    stale_last = int(time.time()) - 10 * M15_SECONDS
    b = Bridge(port, {"FAKE_MT5_AVAILABLE": "1", "FAKE_MT5_TOTAL_BARS": "300",
                      "FAKE_MT5_LAST_TIME": str(stale_last)})
    try:
        assert b.wait_ready(), "bridge did not become ready"
        _code, c = b.get("/v1/candles?symbol=XAUUSD&timeframe=M15&count=5")
        check("stale feed rejected", c["status"] == "ERROR" and
              c.get("error", {}).get("code") == "MARKET_DATA_STALE",
              str(c.get("error", {}).get("code")))
    finally:
        b.stop_sigterm()


def scenario_insufficient():
    print("Scenario: insufficient closed-bar history")
    port = free_port()
    b = Bridge(port, {"FAKE_MT5_AVAILABLE": "1", "FAKE_MT5_TOTAL_BARS": "5",
                      "FAKE_MT5_LAST_TIME": str(int(time.time()))})
    try:
        assert b.wait_ready(), "bridge did not become ready"
        _code, c = b.get("/v1/candles?symbol=XAUUSD&timeframe=M15&count=9&min_count=9")
        check("insufficient history rejected",
              c["status"] == "ERROR" and
              c.get("error", {}).get("code") == "INSUFFICIENT_HISTORY",
              str(c.get("error", {}).get("code")))
    finally:
        b.stop_sigterm()


def scenario_mt5_unavailable():
    print("Scenario: MetaTrader5 unavailable")
    port = free_port()
    b = Bridge(port, {"FAKE_MT5_AVAILABLE": "0"})
    try:
        assert b.wait_ready(), "bridge did not become ready"
        _, h = b.get("/v1/health")
        check("health reports package importable", h["payload"]["package_available"] is True)
        check("health initialized false (terminal down)", h["payload"]["initialized"] is False)
        check("health mt5_ready false", h["payload"]["mt5_ready"] is False)
        check("health quality UNKNOWN", h["quality"] == "UNKNOWN")
        _code, c = b.get("/v1/candles?symbol=XAUUSD&timeframe=M15&count=5")
        check("candles fail with MT5_TERMINAL_UNAVAILABLE",
              c.get("error", {}).get("code") == "MT5_TERMINAL_UNAVAILABLE",
              str(c.get("error", {}).get("code")))
        check("no fabricated candles", c["payload"] == {})
    finally:
        b.stop_sigterm()


def scenario_versions():
    print("Scenario: version handshake")
    port = free_port()
    b = Bridge(port, {"FAKE_MT5_AVAILABLE": "1"})
    try:
        assert b.wait_ready(), "bridge did not become ready"
        status, r = b.get("/v1/handshake", {"X-AURA-Protocol": "2.0"})
        check("protocol mismatch -> 400 + code", status == 400 and
              r.get("error", {}).get("code") == "BRIDGE_PROTOCOL_MISMATCH",
              f"http={status}")
        status, r = b.get("/v1/handshake", {"X-AURA-Schema": "9.9"})
        check("schema mismatch -> 400 + code", status == 400 and
              r.get("error", {}).get("code") == "BRIDGE_SCHEMA_MISMATCH",
              f"http={status}")
        status, _ = b.get("/v1/handshake", {"X-AURA-Protocol": "1.7", "X-AURA-Schema": "1.9"})
        check("compatible 1.x accepted", status == 200, f"http={status}")
    finally:
        b.stop_sigterm()


def scenario_bind_and_shutdown():
    print("Scenario: loopback bind + SIGTERM shutdown")
    # Non-loopback host must be refused outright.
    r = subprocess.run(
        [sys.executable, os.path.join(BRIDGE_DIR, "bridge_service.py"),
         "--host", "0.0.0.0", "--port", str(free_port())],
        cwd=BRIDGE_DIR, capture_output=True, text=True, timeout=10)
    check("0.0.0.0 bind refused", r.returncode == 2 and "loopback" in r.stderr.lower(),
          f"rc={r.returncode}")

    port = free_port()
    b = Bridge(port, {"FAKE_MT5_AVAILABLE": "1"})
    assert b.wait_ready(), "bridge did not become ready"
    # Confirm the socket is bound to loopback, not 0.0.0.0.
    bound_loopback = False
    try:
        with open("/proc/net/tcp") as fh:
            for line in fh.read().splitlines()[1:]:
                f = line.split()
                ip_hex, port_hex = f[1].split(":")
                if int(port_hex, 16) == port:
                    bound_loopback = ip_hex == "0100007F"  # 127.0.0.1
    except OSError:
        pass
    check("listening on 127.0.0.1 only", bound_loopback)
    ok, latency = b.stop_sigterm()
    check("SIGTERM exits cleanly", ok, f"{latency:.3f}s")


def main() -> int:
    print(f"Repo: {REPO}")
    for fn in (scenario_fresh, scenario_stale, scenario_insufficient,
               scenario_mt5_unavailable, scenario_versions,
               scenario_bind_and_shutdown):
        try:
            fn()
        except AssertionError as exc:
            check(fn.__name__, False, f"setup failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            check(fn.__name__, False, f"exception: {exc}")

    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
