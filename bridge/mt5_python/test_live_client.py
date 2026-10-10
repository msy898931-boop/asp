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


if __name__ == "__main__":
    unittest.main()
