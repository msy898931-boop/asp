"""Tests for the live (gold-api.com + Kraken) bridge data source.

Network-backed tests are skipped when the upstream is unreachable so the suite
stays green offline; they exercise the real fetch path, not a stand-in.

Run: python3 -m unittest discover -s bridge/mt5_python -p "test_*.py"
"""

from __future__ import annotations

import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from live_client import LiveMt5Client, _TF_MINUTES  # noqa: E402
import live_client  # noqa: E402  (module handle, for stubbing the HTTP layer)
from schemas import TIMEFRAMES  # noqa: E402

_MAX_AGE_SECONDS = 399 * 24 * 60 * 60


def _upstream_reachable() -> bool:
    try:
        LiveMt5Client(candle_pair="PAXGUSD").initialize()
        return True
    except Exception:
        return False


class LiveClientConfigTests(unittest.TestCase):
    def test_maps_every_canonical_timeframe(self) -> None:
        for tf in TIMEFRAMES:
            self.assertIn(tf, _TF_MINUTES, f"{tf} has no upstream interval mapping")

    def test_reports_live_and_pair(self) -> None:
        client = LiveMt5Client(candle_pair="PAXGUSD")
        health = client.health()
        self.assertFalse(health["synthetic"])
        self.assertEqual(health["data_source"], "live")
        self.assertEqual(health["candle_pair"], "PAXGUSD")
        self.assertIn("PAXGUSD", client.broker)

    def test_unknown_timeframe_is_rejected(self) -> None:
        client = LiveMt5Client(candle_pair="PAXGUSD")
        result = client.read_candles("XAUUSD", "M7", 10)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "MARKET_DATA_INVALID")


@unittest.skipUnless(_upstream_reachable(), "live upstream unreachable")
class LiveClientNetworkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = LiveMt5Client(candle_pair="PAXGUSD", symbol="XAUUSD")
        self.assertTrue(self.client.initialize().ok)

    def test_every_timeframe_returns_decision_grade_history(self) -> None:
        now = int(time.time())
        for tf in TIMEFRAMES:
            result = self.client.read_candles("XAUUSD", tf, 500, closed_only=True)
            self.assertTrue(result.ok, f"{tf}: {result.error_code} {result.message}")
            bars = result.data["candles"]
            self.assertTrue(bars, f"{tf} returned no bars")
            # Every bar must stay inside the engine's 400-day plausibility window.
            self.assertGreaterEqual(bars[0]["time"], now - _MAX_AGE_SECONDS,
                                    f"{tf} served a bar the validator would reject")

    def test_closed_only_excludes_the_forming_bar(self) -> None:
        now = int(time.time())
        result = self.client.read_candles("XAUUSD", "M1", 10, closed_only=True)
        self.assertTrue(result.ok)
        for bar in result.data["candles"]:
            self.assertLessEqual(bar["time"] + 60, now,
                                 "a still-forming bar leaked into a closed-only read")

    def test_candles_are_ascending_and_ohlc_consistent(self) -> None:
        result = self.client.read_candles("XAUUSD", "M5", 50, closed_only=True)
        self.assertTrue(result.ok)
        bars = result.data["candles"]
        for prev, cur in zip(bars, bars[1:]):
            self.assertLess(prev["time"], cur["time"])
        for bar in bars:
            self.assertLessEqual(bar["low"], bar["open"])
            self.assertLessEqual(bar["low"], bar["close"])
            self.assertGreaterEqual(bar["high"], bar["open"])
            self.assertGreaterEqual(bar["high"], bar["close"])

    def test_tick_returns_a_positive_price(self) -> None:
        result = self.client.read_tick("XAUUSD")
        self.assertTrue(result.ok)
        self.assertGreater(result.data["last"], 0.0)

    def test_candles_are_anchored_to_the_spot_quote(self) -> None:
        # The candle proxy (PAXGUSD) and the spot quote (gold-api XAU) are two
        # quotes for the same underlying. The newest close must land on the spot
        # price, or the chart and the tick read would disagree.
        candles = self.client.read_candles("XAUUSD", "M15", 5, closed_only=True)
        tick = self.client.read_tick("XAUUSD")
        self.assertTrue(candles.ok and tick.ok)
        close = candles.data["candles"][-1]["close"]
        spot = tick.data["last"]
        self.assertLess(abs(close - spot) / spot, 0.002,
                        f"anchored close {close} strayed from spot {spot}")

    def test_health_reports_the_applied_basis(self) -> None:
        self.client.read_candles("XAUUSD", "M15", 5, closed_only=True)
        health = self.client.health()
        self.assertTrue(health["candle_anchored_to_spot"])
        self.assertLess(abs(health["last_basis_fraction"]), 0.05)


class OhlcRetryTests(unittest.TestCase):
    """A rate-limited OHLC call is retried, not surfaced as a failure."""

    def _client(self):
        return LiveMt5Client(candle_pair="PAXGUSD", symbol="XAUUSD")

    def test_retries_past_a_rate_limit(self) -> None:
        client = self._client()
        calls = {"n": 0}

        def fake(url):
            calls["n"] += 1
            if calls["n"] < 3:
                return {"error": ["EGeneral:Too many requests"]}
            return {"error": [], "result": {"PAXGUSD": [[0, "1", "1", "1", "1", "1", "1", 1]]}}

        orig_json, orig_sleep = live_client._http_json, live_client.time.sleep
        live_client._http_json = fake
        live_client.time.sleep = lambda _s: None
        try:
            doc = client._fetch_ohlc("http://example.invalid")
        finally:
            live_client._http_json = orig_json
            live_client.time.sleep = orig_sleep
        self.assertEqual(doc.get("error"), [])
        self.assertEqual(calls["n"], 3)

    def test_gives_up_and_reports_after_the_last_try(self) -> None:
        client = self._client()
        calls = {"n": 0}

        def fake(url):
            calls["n"] += 1
            return {"error": ["EGeneral:Too many requests"]}

        orig_json, orig_sleep = live_client._http_json, live_client.time.sleep
        live_client._http_json = fake
        live_client.time.sleep = lambda _s: None
        try:
            doc = client._fetch_ohlc("http://example.invalid")
        finally:
            live_client._http_json = orig_json
            live_client.time.sleep = orig_sleep
        self.assertEqual(calls["n"], live_client._OHLC_RETRIES)
        self.assertTrue(doc.get("error"))


if __name__ == "__main__":
    unittest.main()
