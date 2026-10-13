"""Tests for the signal tracker's replay and scoring core.

Everything here is pure logic over synthetic bars: no network, no clock, no
key. The point is to pin the behaviours the honesty story depends on — one
live idea at a time, conservative stop precedence, the horizon, the spread
debit, and the refusal to state a rate on thin data.

Run: python3 -m unittest discover -s tracker -p "test_*.py"
"""

from __future__ import annotations

import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import replay  # noqa: E402


def make_bar(time: int, o: float, h: float, l: float, c: float) -> dict:
    return {"time": time, "open": o, "high": h, "low": l, "close": c,
            "tick_volume": 1, "spread": 0, "real_volume": 0}


def oscillate_then_trend(bars: int = 130) -> list[dict]:
    """A deterministic series: chop first, then a clean down-trend."""
    out: list[dict] = []
    price = 4000.0
    base = 1_700_000_000
    for i in range(bars):
        drift = 0.4 * math.sin(i / 6.0) + 0.15 if i < 90 else -1.2
        o, c = price, price + drift
        h = max(o, c) + 0.5 * abs(math.sin(i / 3.0)) + 0.3
        l = min(o, c) - 0.5 * abs(math.cos(i / 4.0)) - 0.3
        out.append(make_bar(base + i * 900, round(o, 2), round(h, 2),
                            round(l, 2), round(c, 2)))
        price = c
    return out


class IndicatorTests(unittest.TestCase):
    def test_rsi_all_gains_is_100(self):
        closes = [float(i) for i in range(1, 20)]
        self.assertAlmostEqual(replay._rsi(closes), 100.0)

    def test_rsi_all_losses_is_0(self):
        closes = [float(i) for i in range(20, 1, -1)]
        self.assertAlmostEqual(replay._rsi(closes), 0.0)

    def test_rsi_flat_is_50(self):
        self.assertAlmostEqual(replay._rsi([100.0] * 20), 50.0)

    def test_atr_is_mean_true_range(self):
        bars = [make_bar(0, 10, 12, 9, 11), make_bar(1, 11, 15, 11, 14)]
        # TR of bar 1 = max(15-11, |15-11|, |11-11|) = 4
        self.assertAlmostEqual(replay._atr(bars, period=1), 4.0)


class StructureAndRegimeTests(unittest.TestCase):
    def test_insufficient_swings_is_incomplete(self):
        bars = [make_bar(i, 1, 1, 1, 1) for i in range(10)]
        self.assertEqual(replay._structure(bars), "INCOMPLETE")

    def test_volatile_regime_when_returns_wide(self):
        features = {"vol": 0.05, "slope": 0.0}
        self.assertEqual(replay._regime(features, "RANGE"), "VOLATILE")

    def test_trending_needs_structure_agreement(self):
        features = {"vol": 0.001, "slope": 0.002}
        self.assertEqual(replay._regime(features, "BULLISH"), "TRENDING_UP")
        # Same slope, disagreeing structure: not a trend.
        self.assertNotEqual(replay._regime(features, "BEARISH"), "TRENDING_UP")


class AdvanceTests(unittest.TestCase):
    """Unit tests for how a single live idea meets one bar."""

    def _signal(self, direction: str) -> replay.Signal:
        if direction == "LONG":
            return replay.Signal(index=0, time=0, direction="LONG", entry=100.0,
                                 stop=99.0, target=102.0, bias="BULLISH",
                                 regime="QUIET", rsi=55.0)
        return replay.Signal(index=0, time=0, direction="SHORT", entry=100.0,
                             stop=101.0, target=98.0, bias="BEARISH",
                             regime="QUIET", rsi=45.0)

    def test_stop_wins_when_one_bar_touches_both(self):
        sig = self._signal("LONG")
        bar = make_bar(900, 100, 103, 98, 101)  # hits target 102 and stop 99
        self.assertTrue(replay._advance(sig, bar, 0.0, 32))
        self.assertEqual(sig.outcome, "STOP")

    def test_target_hit_when_only_target_touched(self):
        sig = self._signal("SHORT")
        bar = make_bar(900, 100, 100.5, 97, 97.5)
        self.assertTrue(replay._advance(sig, bar, 0.0, 32))
        self.assertEqual(sig.outcome, "TARGET")

    def test_horizon_closes_at_close(self):
        sig = self._signal("LONG")
        quiet = make_bar(900, 100, 100.5, 99.5, 100.2)  # touches neither
        for _ in range(4):
            self.assertFalse(replay._advance(sig, quiet, 0.0, 5))
        self.assertTrue(replay._advance(sig, quiet, 0.0, 5))
        self.assertEqual(sig.outcome, "TIMEOUT")
        self.assertAlmostEqual(sig.exit_price, 100.2)

    def test_spread_always_reduces_reward(self):
        sig = self._signal("SHORT")
        bar = make_bar(900, 100, 100.5, 97, 97.5)
        replay._advance(sig, bar, 0.30, 32)
        self.assertLess(sig.r_net, sig.r_gross)


class BuildSignalsTests(unittest.TestCase):
    def test_bars_shorter_than_min_bars_yield_nothing(self):
        self.assertEqual(replay.build_signals(oscillate_then_trend(30)), [])

    def test_trades_never_overlap(self):
        signals = replay.build_signals(oscillate_then_trend())
        self.assertTrue(signals, "synthetic series should trigger at least one")
        spans = [(s.time, s.resolved_time) for s in signals]
        for (_, prev_end), (next_start, _) in zip(spans, spans[1:]):
            self.assertGreaterEqual(next_start, prev_end,
                                    "a new idea opened while one was still live")

    def test_every_trade_is_resolved(self):
        signals = replay.build_signals(oscillate_then_trend())
        self.assertTrue(all(s.resolved for s in signals))

    def test_cooldown_delays_and_shrinks_reentry(self):
        bars = oscillate_then_trend()
        hot = replay.build_signals(bars, cooldown_bars=0)
        cool = replay.build_signals(bars, cooldown_bars=8)
        self.assertLessEqual(len(cool), len(hot))
        for (_, prev_end), (next_start, _) in zip(
                [(s.time, s.resolved_time) for s in cool],
                [(s.time, s.resolved_time) for s in cool][1:]):
            gap_bars = (next_start - prev_end) // 900
            self.assertGreaterEqual(gap_bars, 8)


class SummarizeTests(unittest.TestCase):
    def _scored(self, count: int, wins: int, r: float = 1.6) -> list[replay.Signal]:
        out = []
        for i in range(count):
            s = replay.Signal(index=i, time=i, direction="LONG", entry=100.0,
                              stop=99.0, target=102.0, bias="BULLISH",
                              regime="QUIET", rsi=55.0)
            s.resolved = True
            if i < wins:
                s.outcome, s.r_net, s.r_gross = "TARGET", r - 0.3, r
            else:
                s.outcome, s.r_net, s.r_gross = "STOP", -1.0 - 0.3, -1.0
            out.append(s)
        return out

    def test_thin_data_refuses_to_state_a_rate(self):
        summary = replay.summarize(self._scored(10, 6), min_scored=30)
        self.assertFalse(summary["sufficient"])
        self.assertEqual(summary["verdict"], "INSUFFICIENT_DATA")
        self.assertNotIn("win_rate", summary)

    def test_enough_data_states_rate_and_average(self):
        summary = replay.summarize(self._scored(40, 20), min_scored=30)
        self.assertTrue(summary["sufficient"])
        self.assertEqual(summary["verdict"], "SCORED")
        self.assertAlmostEqual(summary["win_rate"], 50.0)
        self.assertIn("avg_r_net", summary)

    def test_buckets_split_hit_rate_by_regime_and_direction(self):
        signals = []
        for i in range(20):
            trending = i < 10
            s = replay.Signal(index=i, time=i,
                              direction="LONG" if trending else "SHORT",
                              entry=100.0, stop=99.0, target=102.0,
                              bias="BULLISH",
                              regime="TRENDING_UP" if trending else "RANGING",
                              rsi=55.0)
            s.resolved = True
            # First ten (LONG, trending) win; the rest lose.
            s.outcome = "TARGET" if trending else "STOP"
            signals.append(s)
        summary = replay.summarize(signals, min_scored=30)
        self.assertEqual(summary["by_regime"]["TRENDING_UP"]["scored"], 10)
        self.assertEqual(summary["by_direction"]["LONG"]["wins"], 10)
        self.assertEqual(summary["by_direction"]["SHORT"]["wins"], 0)
        self.assertIn("hit_rate", summary["by_regime"]["TRENDING_UP"])

    def test_a_small_bucket_states_no_rate(self):
        summary = replay.summarize(self._scored(6, 4), min_scored=30)
        self.assertNotIn("hit_rate", summary["by_regime"]["QUIET"])
        self.assertEqual(summary["by_regime"]["QUIET"]["scored"], 6)

    def test_max_loss_streak_counts_consecutive_losses(self):
        out = []
        outcomes = ["TARGET", "STOP", "STOP", "STOP", "TARGET", "STOP"]
        for i, o in enumerate(outcomes):
            s = replay.Signal(index=i, time=i, direction="LONG", entry=100.0,
                              stop=99.0, target=102.0, bias="BULLISH",
                              regime="QUIET", rsi=55.0)
            s.resolved, s.outcome, s.resolved_time = True, o, i
            out.append(s)
        self.assertEqual(replay.max_loss_streak(out), 3)


class ExplainLastBarTests(unittest.TestCase):
    """Every bar gets an answer, including the quiet ones."""

    def test_always_states_a_decision(self):
        status = replay.explain_last_bar(oscillate_then_trend())
        self.assertIn(status["decision"], ("TRADE", "NO_TRADE"))
        self.assertIn("reason", status)
        self.assertEqual(status["bar_time"], oscillate_then_trend()[-1]["time"])

    def test_no_data_on_a_short_series(self):
        status = replay.explain_last_bar(oscillate_then_trend(30))
        self.assertEqual(status["decision"], "NO_TRADE")
        self.assertEqual(status["reason"], "NO_DATA")

    def test_a_trade_carries_its_reason(self):
        # Scan the series until a bar actually opens an idea, then check the
        # status names the read behind it.
        bars = oscillate_then_trend()
        seen_trade = False
        for end in range(60, len(bars) + 1):
            status = replay.explain_last_bar(bars[:end])
            if status["decision"] == "TRADE":
                seen_trade = True
                self.assertIn("read", status)
                self.assertIn(status["read"]["regime"],
                              ("TRENDING_UP", "TRENDING_DOWN", "RANGING", "QUIET"))
                self.assertEqual(status["signal"]["time"], bars[end - 1]["time"])
                break
        self.assertTrue(seen_trade, "synthetic series never produced a trade")

    def test_account_plan_reports_the_minimum_lot_reality(self):
        # A 6.44 stop on a $200 account: 1% ($2) buys 0.31 oz, below gold's
        # 1 oz minimum, so the floor is what would actually be traded.
        plan = replay.account_plan(entry=4185.0, stop=4178.56,
                                   account_usd=200.0, risk_pct=1.0)
        self.assertTrue(plan["below_min_lot"])
        self.assertAlmostEqual(plan["risk_usd"], 2.0, places=2)
        self.assertAlmostEqual(plan["min_risk_usd"], 6.44, places=2)
        self.assertGreater(plan["min_risk_pct"], plan["risk_pct"])

    def test_account_plan_honours_the_budget_when_lot_is_large_enough(self):
        plan = replay.account_plan(entry=4000.0, stop=3900.0,
                                   account_usd=10000.0, risk_pct=1.0)
        self.assertFalse(plan["below_min_lot"])
        self.assertAlmostEqual(plan["position_oz"], 1.0, places=3)


if __name__ == "__main__":
    unittest.main()
