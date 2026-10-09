"""Tests for the replay (synthetic) bridge data source.

Run: python3 -m unittest discover -s bridge/mt5_python -p "test_*.py"
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from replay_client import ReplayMt5Client  # noqa: E402
from schemas import TIMEFRAMES  # noqa: E402

_REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
_CORPUS = os.path.join(_REPO, "research", "data", "xauusd_m1", "xauusd_m1_real.csv")


@unittest.skipUnless(os.path.isfile(_CORPUS), "committed XAUUSD corpus not present")
class ReplayClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = ReplayMt5Client(_CORPUS, symbol="XAUUSD")
        self.assertTrue(self.client.initialize().ok)

    def test_reports_synthetic_and_symbol(self) -> None:
        self.assertTrue(self.client.available)
        self.assertEqual(self.client.broker, "REPLAY (synthetic)")
        self.assertEqual(self.client.resolved_symbol, "XAUUSD")
        health = self.client.health()
        self.assertTrue(health["synthetic"])
        self.assertEqual(health["data_source"], "replay")
        self.assertGreater(health["corpus_bars"], 1000)

    def test_every_timeframe_returns_bars(self) -> None:
        for tf in TIMEFRAMES:
            result = self.client.read_candles("XAUUSD", tf, 50, closed_only=True)
            self.assertTrue(result.ok, f"{tf}: {result.message}")
            self.assertGreater(result.data["count"], 0, tf)

    def test_closed_only_excludes_forming_bar(self) -> None:
        # closed_only must never offer the newest (forming) bar as a decision bar.
        open_result = self.client.read_candles("XAUUSD", "M15", 10, closed_only=False)
        closed_result = self.client.read_candles("XAUUSD", "M15", 10, closed_only=True)
        self.assertTrue(open_result.ok and closed_result.ok)
        newest_open = open_result.data["candles"][-1]["time"]
        newest_closed = closed_result.data["candles"][-1]["time"]
        self.assertLess(newest_closed, newest_open)

    def test_candles_are_ascending_and_ohlc_consistent(self) -> None:
        result = self.client.read_candles("XAUUSD", "M15", 100, closed_only=True)
        candles = result.data["candles"]
        times = [c["time"] for c in candles]
        self.assertEqual(times, sorted(times))
        for c in candles:
            self.assertLessEqual(c["low"], c["high"])
            self.assertLessEqual(c["low"], c["open"])
            self.assertLessEqual(c["low"], c["close"])
            self.assertGreaterEqual(c["high"], c["open"])
            self.assertGreaterEqual(c["high"], c["close"])

    def test_unknown_timeframe_is_rejected(self) -> None:
        result = self.client.read_candles("XAUUSD", "M7", 10)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "MARKET_DATA_INVALID")

    def test_tick_and_symbol_spec(self) -> None:
        tick = self.client.read_tick("XAUUSD")
        self.assertTrue(tick.ok)
        self.assertGreater(tick.data["bid"], 0)
        spec = self.client.symbol_specification("XAUUSD")
        self.assertTrue(spec.ok)
        self.assertEqual(spec.data["name"], "XAUUSD")

    def test_freshness_is_measured_from_corpus_end(self) -> None:
        """A fixed corpus must not age into STALE as wall-clock time passes.

        Freshness is judged against the corpus's own newest bar, so a complete
        historical corpus reads FRESH indefinitely instead of decaying to STALE
        a few minutes after the backend starts.
        """
        res = self.client.read_candles("XAUUSD", "M1", 10, closed_only=True)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(res.data["freshness"], "FRESH")
        self.assertLessEqual(res.data["age_seconds"], 60)

    def test_empty_corpus_fails_honestly(self) -> None:
        empty = os.path.join(_REPO, "research", "data", "xauusd_m1", "__no_such__.csv")
        client = ReplayMt5Client(empty, symbol="XAUUSD")
        result = client.initialize()
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "MARKET_DATA_MISSING")


if __name__ == "__main__":
    unittest.main()
