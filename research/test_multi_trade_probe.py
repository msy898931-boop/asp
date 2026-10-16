"""Tests for the concurrency probe.

The probe's conclusion — concurrency multiplies trades without improving them —
rests on two claims worth pinning: that its N=1 replay really is the live rule,
and that its metrics are honest. Pure functions, no data files.

Run: python3 -m unittest discover -s research -p "test_*.py"
"""

from __future__ import annotations

import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from research.multi_trade_probe import (load_m15, metrics,  # noqa: E402
                                        precompute, replay_multi)
from tracker import replay  # noqa: E402


def make_bar(time, o, h, l, c):
    return {"time": time, "open": o, "high": h, "low": l, "close": c,
            "tick_volume": 1, "spread": 0, "real_volume": 0}


def oscillate_then_trend(bars=130):
    """The same deterministic series test_replay.py uses."""
    out = []
    price = 4000.0
    base = 1_700_000_000
    for i in range(bars):
        drift = 0.4 * math.sin(i / 6.0) + 0.15 if i < 90 else -1.2
        o, c = price, price + drift
        h = max(o, c) + 0.5 * abs(math.sin(i / 3.0)) + 0.3
        l = min(o, c) - 0.5 * abs(math.cos(i / 3.0)) - 0.3
        out.append(make_bar(base + i * 900, o, h, l, c))
        price = c
    return out


class ReplayMultiMatchesLiveRuleTests(unittest.TestCase):
    def test_n1_with_full_window_is_the_live_rule(self):
        bars = oscillate_then_trend()
        # window >= series length => full-history decisions, exactly the live rule
        decisions = precompute(bars, window=len(bars))
        live = replay.build_signals(bars)
        mine = replay_multi(bars, decisions, max_concurrent=1)
        self.assertEqual(len(mine), len(live), "idea count must match the live rule")
        for a, b in zip(mine, live):
            self.assertEqual((a.index, a.direction, a.outcome),
                             (b.index, b.direction, b.outcome))

    def test_more_concurrency_opens_at_least_as_many(self):
        bars = oscillate_then_trend()
        decisions = precompute(bars, window=len(bars))
        one = replay_multi(bars, decisions, 1)
        three = replay_multi(bars, decisions, 3)
        self.assertGreaterEqual(len(three), len(one))

    def test_concurrent_ideas_may_overlap_in_time(self):
        bars = oscillate_then_trend()
        decisions = precompute(bars, window=len(bars))
        many = replay_multi(bars, decisions, 5)
        spans = [(s.time, s.resolved_time) for s in many]
        overlapped = any(next_start < prev_end
                         for (_, prev_end), (next_start, _) in zip(spans, spans[1:]))
        self.assertTrue(overlapped, "5 concurrent should produce overlapping ideas")


class MetricsTests(unittest.TestCase):
    def _signal(self, time, outcome, r_net, resolved=True):
        s = replay.Signal(index=0, time=time, direction="LONG", entry=1.0,
                          stop=0.0, target=2.0, bias="BULLISH", regime="TREND",
                          rsi=50.0)
        s.outcome = outcome
        s.resolved = resolved
        s.r_net = r_net
        return s

    def test_win_rate_and_mean_r(self):
        sigs = [self._signal(1, "TARGET", 1.5),
                self._signal(2, "STOP", -1.0),
                self._signal(3, "TARGET", 1.5),
                self._signal(4, "STOP", -1.0)]
        m = metrics(sigs)
        self.assertEqual(m["resolved"], 4)
        self.assertEqual(m["wins"], 2)
        self.assertAlmostEqual(m["win_rate"], 50.0)
        self.assertAlmostEqual(m["total_r"], 1.0)
        self.assertAlmostEqual(m["mean_r"], 0.25)

    def test_max_loss_streak_counts_consecutive_stops(self):
        sigs = [self._signal(1, "STOP", -1.0),
                self._signal(2, "STOP", -1.0),
                self._signal(3, "TARGET", 1.5),
                self._signal(4, "STOP", -1.0),
                self._signal(5, "STOP", -1.0),
                self._signal(6, "STOP", -1.0)]
        self.assertEqual(metrics(sigs)["max_loss_streak"], 3)

    def test_unresolved_ideas_are_not_scored(self):
        sigs = [self._signal(1, "OPEN", None, resolved=False),
                self._signal(2, "TARGET", 1.5)]
        m = metrics(sigs)
        self.assertEqual(m["resolved"], 1)
        self.assertEqual(m["win_rate"], 100.0)


class LoadM15Tests(unittest.TestCase):
    def test_four_m1_rows_become_one_m15_bar(self):
        import tempfile
        rows = ["timestamp,open,high,low,close,volume",
                "2026-01-01 00:00:00,100,101,99,100.5,10",
                "2026-01-01 00:05:00,100.5,103,100,102,20",
                "2026-01-01 00:10:00,102,104,101,103,30",
                "2026-01-01 00:15:00,103,105,98,104,40"]
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            fh.write("\n".join(rows))
            path = fh.name
        try:
            bars = load_m15(path)
        finally:
            os.unlink(path)
        self.assertEqual(len(bars), 2)
        first = bars[0]
        self.assertEqual(first["open"], 100)          # first open
        self.assertEqual(first["high"], 104)          # max high
        self.assertEqual(first["low"], 99)            # min low
        self.assertEqual(first["close"], 103)         # last close
        self.assertEqual(first["tick_volume"], 60)    # summed volume


if __name__ == "__main__":
    unittest.main()
