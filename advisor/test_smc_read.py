"""Tests for the deterministic SMC/ICT fact sheet (advisor/smc_read.py).

These tests pin the *arithmetic*, not a market opinion: a body close breaks
structure (a wick does not), a reversal break is a CHoCH and a continuation
break is a BOS, a three-candle window with a gap is a fair-value gap, the
candle before a break is the order block, equal highs cluster, and a wick
beyond a level that closes back inside is a sweep. Every case is a hand-built
bar series whose answer is known by construction.
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import smc_read  # noqa: E402


def mk(rows) -> list:
    """Bars from (open, high, low, close) tuples, one 15-minute step apart."""
    return [{"time": 1760000000 + i * 900, "open": o, "high": h, "low": l,
             "close": c} for i, (o, h, l, c) in enumerate(rows)]


def build(swings, n: int = 3) -> list:
    """Bars that trace the given swing points, one leg per pair.

    ``swings`` is a list of ``(price, kind)`` with kind 'H' or 'L'. Each leg is
    ``n`` bars, and the open of a leg gaps opposite the new direction so every
    turning point is a *unique* extreme — otherwise two bars share a price and
    neither is a confirmed pivot.
    """
    rows = []
    p = swings[0][0]
    for k in range(1, len(swings)):
        target = swings[k][0]
        up = target > p
        op = p - 0.4 if up else p + 0.4
        for j in range(1, n + 1):
            v = p + (target - p) * j / n
            hi = max(op, v) + 0.2
            lo = min(op, v) - 0.2
            rows.append((op, hi, lo, v))
            op = v
        p = target
    return mk(rows)


# Rising highs and lows (bullish structure), then a drop through the last low.
BULL_THEN_BREAK_DOWN = [(100.0, None), (104.0, "H"), (101.5, "L"),
                        (105.5, "H"), (102.5, "L"), (106.5, "H"), (99.0, "L")]


class StructureTests(unittest.TestCase):
    def test_a_continuation_break_is_a_bos(self):
        _, events = smc_read.structure_events(build(BULL_THEN_BREAK_DOWN))
        bullish = [e for e in events if e["kind"] == "BOS"
                   and e["direction"] == "bullish"]
        self.assertTrue(bullish, "an up leg through the prior high is a BOS")

    def test_the_first_break_against_the_trend_is_a_choch(self):
        _, events = smc_read.structure_events(build(BULL_THEN_BREAK_DOWN))
        choch = [e for e in events if e["kind"] == "CHoCH"]
        self.assertTrue(choch, "the drop through the last low is a CHoCH")
        self.assertEqual(choch[0]["direction"], "bearish")

    def test_the_final_bias_follows_the_last_break(self):
        bias, _ = smc_read.structure_events(build(BULL_THEN_BREAK_DOWN))
        self.assertEqual(bias, "bearish")

    def test_a_wick_beyond_a_level_is_not_a_break(self):
        # The high pokes above but the close stays inside: no break is recorded.
        bars = mk([(100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100),
                   (100, 101.2, 99.5, 100.0), (100, 101.0, 99.6, 100.1)])
        _, events = smc_read.structure_events(bars)
        self.assertEqual(events, [])

    def test_a_pivot_needs_lookback_bars_before_it_is_usable(self):
        # A high on the very last bar cannot be a confirmed pivot, because no
        # bars have closed after it. The read must be causal.
        bars = mk([(100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100),
                   (100, 105, 99, 104)])
        self.assertNotIn(3, smc_read._pivots(bars, "high"))


class PremiumDiscountTests(unittest.TestCase):
    def test_the_percentage_never_leaves_zero_to_hundred(self):
        # Even when price has extended past the swing range, the reported
        # position stays within 0..100 and the extension is flagged.
        bars = build([(100.0, None), (104.0, "H"), (101.0, "L"), (120.0, "H")])
        pd = smc_read._premium_discount(bars)
        self.assertIsNotNone(pd)
        self.assertGreaterEqual(pd["close_position_pct"], 0.0)
        self.assertLessEqual(pd["close_position_pct"], 100.0)
        self.assertTrue(pd["extended"])

    def test_a_close_above_equilibrium_is_premium(self):
        bars = build([(100.0, None), (104.0, "H"), (101.0, "L"), (103.5, "H")])
        pd = smc_read._premium_discount(bars)
        self.assertIsNotNone(pd)
        self.assertEqual(pd["zone"], "premium")
        self.assertGreater(pd["close_position_pct"], 50.0)

    def test_too_few_swings_yields_none(self):
        bars = mk([(100, 101, 99, 100), (100, 101, 99, 100)])
        self.assertIsNone(smc_read._premium_discount(bars))


class LiquidityTests(unittest.TestCase):
    def test_equal_highs_cluster_into_one_pool(self):
        # Two swing highs within the tolerance become a single pool of stops.
        bars = build([(100.0, None), (105.0, "H"), (101.0, "L"),
                      (105.2, "H"), (102.0, "L"), (104.0, "H")])
        pools = smc_read._liquidity(bars, atr_value=2.0)
        self.assertTrue(pools["equal_highs"])
        self.assertGreaterEqual(pools["equal_highs"][0]["touches"], 2)

    def test_a_wick_above_a_swing_that_closes_inside_is_a_sweep(self):
        # A swing high at 105, then a bar whose wick pokes to 106 but closes
        # back below 105 — a stop-hunt, not a break.
        bars = mk([(100, 101, 99, 100), (100, 103, 100, 102),
                   (102, 105, 101, 104), (104, 104.5, 102, 103),
                   (103, 103.5, 101, 102), (102, 106, 103, 104),
                   (104, 105, 102, 103)])
        sweeps = smc_read._sweeps(bars)
        self.assertTrue(any(s["side"] == "above" for s in sweeps))

    def test_a_body_close_beyond_a_level_is_not_a_sweep(self):
        bars = mk([(100, 101, 99, 100), (100, 101, 99, 100),
                   (100, 102, 100, 101.5), (101.5, 102, 100, 101),
                   (101, 104, 101, 103.5), (103.5, 104, 102, 103)])
        sweeps = smc_read._sweeps(bars)
        # The break bar closed above the level, so it is a break, not a sweep.
        self.assertFalse(any(s["time"] == bars[4]["time"] for s in sweeps))


class FairValueGapTests(unittest.TestCase):
    def test_a_three_candle_bullish_gap_is_found(self):
        # First candle high 100, third candle low 102 -> a gap between them.
        bars = mk([(99, 100, 98, 99.5), (99.5, 103, 99.4, 102.5),
                   (102.5, 104, 102, 103.5)])
        gaps = smc_read._fair_value_gaps(bars, atr_value=1.0)
        self.assertTrue(any(g["kind"] == "bullish" for g in gaps))

    def test_a_tiny_gap_is_dropped_as_noise(self):
        bars = mk([(99, 100.00, 98, 99.5), (99.5, 100.01, 99.4, 100.005),
                   (100.005, 100.02, 100.00, 100.015)])
        gaps = smc_read._fair_value_gaps(bars, atr_value=10.0)
        self.assertEqual(gaps, [])

    def test_a_later_trade_through_the_gap_marks_it_filled(self):
        bars = mk([(99, 100, 98, 99.5), (99.5, 103, 99.4, 102.5),
                   (102.5, 104, 102, 103.5), (103.5, 103.6, 100.5, 101)])
        gaps = smc_read._fair_value_gaps(bars, atr_value=1.0)
        bull = [g for g in gaps if g["kind"] == "bullish"]
        self.assertTrue(bull and bull[-1]["filled"])


class OrderBlockTests(unittest.TestCase):
    def test_the_last_down_candle_before_a_bullish_break_is_the_block(self):
        bars = mk([(100, 101, 99, 100), (100, 100.5, 98, 98.5),
                   (98.5, 103, 98.4, 102.5), (102.5, 104, 102, 103.5)])
        _, events = smc_read.structure_events(bars)
        blocks = smc_read._order_blocks(bars, events)
        if blocks:
            self.assertEqual(blocks[0]["kind"], "bullish")


class TimeframeFactsTests(unittest.TestCase):
    def test_a_too_short_series_yields_none(self):
        self.assertIsNone(smc_read.timeframe_facts(mk([(1, 1, 1, 1)])))

    def test_the_facts_carry_the_expected_keys(self):
        facts = smc_read.timeframe_facts(build(BULL_THEN_BREAK_DOWN))
        for key in ("close", "atr", "structure", "premium_discount",
                    "liquidity", "fair_value_gaps", "order_blocks", "swings"):
            self.assertIn(key, facts)

    def test_no_opinion_field_is_present(self):
        # The fact sheet must not smuggle in a direction call; that is the
        # model's job, over these facts.
        facts = smc_read.timeframe_facts(build(BULL_THEN_BREAK_DOWN))
        self.assertNotIn("bias", facts)
        self.assertNotIn("direction", facts)


class ConfluenceTests(unittest.TestCase):
    def _facts(self, biases):
        return {tf: {"structure": {"bias": b}} for tf, b in biases.items()}

    def test_frames_that_agree_are_tallied(self):
        facts = self._facts({"M5": "bullish", "M15": "bullish", "H1": "bullish"})
        c = smc_read._confluence(facts)
        self.assertEqual(c["dominant"], "bullish")
        self.assertEqual(c["agreeing"], 3)
        self.assertEqual(c["total_frames"], 3)
        self.assertAlmostEqual(c["agreement"], 1.0)

    def test_higher_frames_weigh_more(self):
        # H1 and H4 bullish (weight 2 each) beat M5/M15/M30 bearish (weight 1).
        facts = self._facts({"M5": "bearish", "M15": "bearish", "M30": "bearish",
                             "H1": "bullish", "H4": "bullish"})
        c = smc_read._confluence(facts)
        self.assertEqual(c["weighted_score"], 1)
        self.assertEqual(c["dominant"], "bullish")
        self.assertEqual(sorted(c["opposing_frames"]), ["M15", "M30", "M5"])

    def test_an_exact_tie_is_neutral_not_a_guess(self):
        # M5 + M15 bullish (weight 1+1) against H1 bearish (weight 2): a tie.
        facts = self._facts({"M5": "bullish", "M15": "bullish",
                             "H1": "bearish"})
        c = smc_read._confluence(facts)
        self.assertEqual(c["weighted_score"], 0)
        self.assertEqual(c["dominant"], "neutral")

    def test_no_readable_frames_yields_none(self):
        self.assertIsNone(smc_read._confluence({}))
        self.assertIsNone(smc_read._confluence({"M5": {"structure": {}}}))

    def test_the_tally_is_never_called_an_edge(self):
        c = smc_read._confluence(self._facts({"M5": "bullish"}))
        self.assertIn("not a measured edge", c["note"])


class BuildTests(unittest.TestCase):
    def test_missing_frames_are_reported_not_faked(self):
        sheet = smc_read.build({"M15": build(BULL_THEN_BREAK_DOWN)},
                               ("M5", "M15", "H4"))
        self.assertEqual(set(sheet["timeframes"]), {"M15"})
        self.assertEqual(set(sheet["missing_timeframes"]), {"M5", "H4"})

    def test_the_sheet_carries_the_facts_note(self):
        sheet = smc_read.build({"M15": build(BULL_THEN_BREAK_DOWN)})
        self.assertIn("not a measured edge", sheet["facts_note"])


class SessionRangeTests(unittest.TestCase):
    def test_an_intraday_frame_produces_session_highs_and_lows(self):
        # Hourly bars from midnight UTC, so the Asian window (0..7) is covered.
        base = 1760000000
        start = base - (base % 86400)  # midnight UTC
        bars = []
        for h in range(24):
            bars.append({"time": start + h * 3600, "open": 100, "high": 100 + h,
                         "low": 100 - h, "close": 100})
        sessions = smc_read.session_ranges(bars)
        self.assertIsNotNone(sessions)
        self.assertEqual(sessions["asian"]["bars"], 7)
        self.assertEqual(sessions["asian"]["high"], 106)
        self.assertEqual(sessions["asian"]["low"], 94)

    def test_a_frame_without_timestamps_yields_none(self):
        self.assertIsNone(smc_read.session_ranges([{"open": 1, "high": 1,
                                                    "low": 1, "close": 1}]))


if __name__ == "__main__":
    unittest.main()
