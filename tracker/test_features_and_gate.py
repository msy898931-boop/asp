"""Tests for the feature extractor and the promotion gate.

Pure logic over synthetic bars and rows: no network, no clock, no key. The
point is to pin the two properties every honest number downstream depends on —
no lookahead in the features, and a gate that refuses a good-looking number
when the evidence behind it is thin.

Run: python3 -m unittest discover -s tracker -p "test_*.py"
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import features  # noqa: E402
import validation_gate as gate  # noqa: E402


def bar(time: int, o: float, h: float, l: float, c: float) -> dict:
    return {"time": time, "open": o, "high": h, "low": l, "close": c,
            "tick_volume": 1, "spread": 0, "real_volume": 0}


def ramp(n: int, step: float = 1.0, base_time: int = 1_700_000_000,
         period: int = 900) -> list[dict]:
    """A clean up-trend: close rises by ``step`` each bar."""
    out = []
    price = 4000.0
    for i in range(n):
        o = price
        c = price + step
        out.append(bar(base_time + i * period, round(o, 2),
                       round(max(o, c) + 0.2, 2), round(min(o, c) - 0.2, 2),
                       round(c, 2)))
        price = c
    return out


def row(outcome: str, r: float, time: int = 1_700_000_000) -> dict:
    return {"outcome": outcome, "r_net": r, "time": time}


# ───────────────────────────── features ─────────────────────────────────────


class SessionTests(unittest.TestCase):
    def test_session_buckets_cover_the_day(self):
        # 2023-11-14 09:00 UTC -> LONDON
        self.assertEqual(features.session_of(1_699_952_400), "LONDON")

    def test_weekday_is_named(self):
        self.assertIn(features.weekday_of(1_699_952_400), features.WEEKDAYS)


class H4Tests(unittest.TestCase):
    def test_h4_resample_groups_four_hours(self):
        # Start on an H4 boundary so the four bars form exactly one bucket.
        bars = ramp(4, period=900, base_time=1_699_948_800)
        h4 = features.h4_bars(bars)
        self.assertEqual(len(h4), 1)
        self.assertEqual(h4[0]["open"], bars[0]["open"])
        self.assertEqual(h4[0]["close"], bars[3]["close"])
        self.assertEqual(h4[0]["high"], max(b["high"] for b in bars))

    def test_h4_resample_splits_across_boundaries(self):
        # 20 M15 bars = 5 hours, so it must straddle an H4 boundary.
        bars = ramp(20, period=900, base_time=1_699_948_800)
        self.assertEqual(len(features.h4_bars(bars)), 2)

    def test_h4_alignment_follows_the_trend(self):
        # 900 M15 bars = 225 hours ~= 56 H4 bars, enough for the EMA50.
        bars = ramp(900, step=1.0, period=900, base_time=1_699_948_800)
        sig = {"index": len(bars) - 1, "direction": "LONG"}
        self.assertEqual(features.h4_alignment(bars, "LONG"), "ALIGNED")
        self.assertEqual(features.h4_alignment(bars, "SHORT"), "AGAINST")


class NoLookaheadTests(unittest.TestCase):
    def test_extract_ignores_bars_after_entry(self):
        bars = ramp(300, step=1.0, period=900)
        index = 200
        sig = {"index": index, "direction": "LONG", "entry": bars[index]["close"],
               "stop": bars[index]["close"] - 10.0, "time": bars[index]["time"],
               "regime": "TRENDING_UP", "bias": "BULLISH", "rsi": 60.0,
               "outcome": "TARGET", "r_net": 1.5}
        first = features.extract(bars, sig)
        # Mutating every bar *after* entry must not change the row.
        poisoned = [dict(b) for b in bars]
        for b in poisoned[index + 1:]:
            b["high"] *= 100.0
            b["low"] *= 100.0
            b["close"] *= 100.0
        second = features.extract(poisoned, sig)
        self.assertEqual(first, second)

    def test_extract_is_json_safe(self):
        import json
        bars = ramp(300, period=900)
        sig = {"index": 250, "direction": "LONG", "entry": bars[250]["close"],
               "stop": bars[250]["close"] - 5.0, "time": bars[250]["time"],
               "regime": "QUIET", "bias": "BULLISH", "rsi": 55.0,
               "outcome": "STOP", "r_net": -1.0}
        json.dumps(features.extract(bars, sig))   # must not raise


# ──────────────────────────── the gate ──────────────────────────────────────


class WilsonTests(unittest.TestCase):
    def test_wilson_stays_inside_unit_interval(self):
        for k, n in ((0, 1), (1, 1), (0, 10), (10, 10), (3, 7)):
            lo, hi = gate.wilson(k, n)
            self.assertGreaterEqual(lo, 0.0)
            self.assertLessEqual(hi, 1.0)
            self.assertLessEqual(lo, hi)

    def test_wilson_low_is_below_the_point_estimate(self):
        lo, hi = gate.wilson(40, 100)
        self.assertLess(lo, 0.40)
        self.assertGreater(hi, 0.40)

    def test_wilson_empty_is_zero(self):
        self.assertEqual(gate.wilson(0, 0), (0.0, 0.0))


class BreakEvenTests(unittest.TestCase):
    def test_break_even_matches_rr(self):
        # +1.667R win, -1R loss -> need ~37.5% to break even
        be = gate.break_even_win_rate(1.6667, -1.0)
        self.assertAlmostEqual(be, 0.375, places=3)

    def test_no_solution_when_wins_are_not_bigger(self):
        # Wins smaller in magnitude than losses: no win rate breaks even.
        self.assertIsNone(gate.break_even_win_rate(-2.0, -1.0))


class EvaluateTests(unittest.TestCase):
    def test_timeouts_are_excluded_from_the_rate(self):
        rows = [row("TARGET", 1.5), row("STOP", -1.0), row("TIMEOUT", 0.2)]
        ev = gate.evaluate(rows)
        self.assertEqual(ev["n"], 2)
        self.assertEqual(ev["timeouts"], 1)
        self.assertAlmostEqual(ev["win_rate"], 0.5)

    def test_empty_half_is_safe(self):
        ev = gate.evaluate([])
        self.assertEqual(ev["n"], 0)
        self.assertIsNone(ev["win_rate"])


class GateTests(unittest.TestCase):
    def _half(self, k, n, total_r, avg_win=1.5, avg_loss=-1.0):
        rows = ([row("TARGET", avg_win) for _ in range(k)]
                + [row("STOP", avg_loss) for _ in range(n - k)])
        return gate.evaluate(rows)

    def test_thin_sample_is_insufficient_not_a_pass(self):
        verdict = gate.gate(self._half(20, 40, 10.0), self._half(20, 40, 10.0),
                            min_n=100)
        self.assertEqual(verdict["verdict"], "INSUFFICIENT")

    def test_strong_both_halves_promotes(self):
        # 50% and 54% win rates, both with Wilson lows above the 40% break-even.
        is_half = self._half(60, 120, 0)
        oos_half = self._half(65, 120, 0)
        verdict = gate.gate(is_half, oos_half, min_n=100)
        self.assertEqual(verdict["verdict"], "PROMOTE")

    def test_in_sample_only_is_rejected(self):
        is_half = self._half(60, 120, 30.0)
        oos_half = self._half(30, 120, -20.0)   # 25% -> below break-even
        verdict = gate.gate(is_half, oos_half, min_n=100)
        self.assertEqual(verdict["verdict"], "REJECT")
        self.assertTrue(any("out-of-sample" in r for r in verdict["reasons"]))

    def test_win_rate_above_break_even_but_negative_r_is_rejected(self):
        # 40% on +1R/-1R with break-even 50%: rate clears nothing, and R is down.
        is_half = self._half(48, 120, -24.0, avg_win=1.0, avg_loss=-1.0)
        oos_half = self._half(48, 120, -24.0, avg_win=1.0, avg_loss=-1.0)
        verdict = gate.gate(is_half, oos_half, min_n=100)
        self.assertEqual(verdict["verdict"], "REJECT")


class SplitTests(unittest.TestCase):
    def test_split_partitions_by_year(self):
        rows = [row("TARGET", 1.0, time=1_600_000_000),    # 2020
                row("STOP", -1.0, time=1_700_000_000)]      # 2023
        is_rows, oos_rows = gate.split_rows(rows, 2022)
        self.assertEqual(len(is_rows), 1)
        self.assertEqual(len(oos_rows), 1)


if __name__ == "__main__":
    unittest.main()
