"""Unit tests for the multi-timeframe trade card (no network, no LLM)."""

from __future__ import annotations

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mtf_read
from mtf_read import MIN_AGREE, TIMEFRAMES, build, timeframe_read


def _series(n, start=1800.0, step=0.5, spread=0.3):
    """A strictly monotonic series: step>0 trends up, step<0 trends down."""
    bars = []
    price = start
    for _ in range(n):
        open_ = price
        price = price + step
        close = price
        bars.append({
            "open": open_,
            "high": max(open_, close) + spread,
            "low": min(open_, close) - spread,
            "close": close,
        })
    return bars


def _zigzag(n, step=0.5, amp=3.0, period=10, spread=0.3):
    """Drift plus a sine wave: real swing highs and lows, drifting up/down."""
    bars = []
    for i in range(n):
        base = 1800.0 + step * i
        wave = amp * math.sin(2.0 * math.pi * i / period)
        close = base + wave
        prev_wave = amp * math.sin(2.0 * math.pi * (i - 1) / period) if i else wave
        open_ = base + prev_wave
        bars.append({
            "open": open_,
            "high": max(open_, close) + spread,
            "low": min(open_, close) - spread,
            "close": close,
        })
    return bars


def _flat(n, price=1800.0, spread=0.5):
    return [{"open": price, "high": price + spread, "low": price - spread,
             "close": price} for _ in range(n)]


def _all_tfs(step, n=120):
    return {tf: _series(n, step=step) for tf in TIMEFRAMES}


class TimeframeReadTest(unittest.TestCase):
    def test_too_few_bars_is_none(self):
        self.assertIsNone(timeframe_read(_series(5)))

    def test_uptrend_reads_long(self):
        read = timeframe_read(_series(120, step=0.5))
        self.assertEqual(read["direction"], "LONG")
        self.assertGreater(read["rsi"], 52.0)
        self.assertGreater(read["ema_fast"], read["ema_slow"])

    def test_downtrend_reads_short(self):
        read = timeframe_read(_series(120, step=-0.5))
        self.assertEqual(read["direction"], "SHORT")
        self.assertLess(read["rsi"], 48.0)

    def test_zigzag_up_has_bullish_structure(self):
        self.assertEqual(timeframe_read(_zigzag(160, step=0.4))["structure"], 1)

    def test_zigzag_down_has_bearish_structure(self):
        self.assertEqual(timeframe_read(_zigzag(160, step=-0.4))["structure"], -1)

    def test_rsi_is_bounded(self):
        for step in (0.5, -0.5, 0.0):
            read = timeframe_read(_series(120, step=step))
            self.assertGreaterEqual(read["rsi"], 0.0)
            self.assertLessEqual(read["rsi"], 100.0)


class CardTest(unittest.TestCase):
    def test_full_agreement_yields_a_long_card_with_exact_numbers(self):
        bars_by_tf = _all_tfs(step=0.5)
        result = build(bars_by_tf)
        self.assertEqual(result["verdict"], "LONG")
        self.assertEqual(result["agreement"]["long"], len(TIMEFRAMES))
        card = result["card"]
        self.assertIsNotNone(card)

        # Recompute the numbers independently from the operational bars.
        op = bars_by_tf[mtf_read.OPERATIONAL_TF]
        trs = []
        for i in range(1, len(op)):
            h, l, pc = op[i]["high"], op[i]["low"], op[i - 1]["close"]
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        atr = sum(trs[-mtf_read.ATR_PERIOD:]) / mtf_read.ATR_PERIOD
        entry = op[-1]["close"]
        anchor = min(b["low"] for b in op[-mtf_read.STOP_SWING_BARS:])
        stop = anchor - mtf_read.STOP_BUFFER_ATR * atr
        risk = entry - stop
        target = entry + mtf_read.REWARD_R * risk

        self.assertAlmostEqual(card["entry"], round(entry, 2), places=2)
        self.assertAlmostEqual(card["stop"], round(stop, 2), places=2)
        self.assertAlmostEqual(card["target"], round(target, 2), places=2)
        self.assertLess(card["stop"], card["entry"])
        self.assertLess(card["entry"], card["target"])
        self.assertAlmostEqual(card["rr"], mtf_read.REWARD_R, places=2)

    def test_full_agreement_down_yields_a_short_card(self):
        result = build(_all_tfs(step=-0.5))
        self.assertEqual(result["verdict"], "SHORT")
        card = result["card"]
        self.assertGreater(card["stop"], card["entry"])
        self.assertGreater(card["entry"], card["target"])
        self.assertAlmostEqual(card["rr"], mtf_read.REWARD_R, places=2)

    def test_structural_opposition_stands_aside(self):
        bars_by_tf = _all_tfs(step=0.5)
        bars_by_tf[mtf_read.STRUCTURAL_TF] = _series(120, step=-0.5)
        result = build(bars_by_tf)
        self.assertEqual(result["verdict"], "NEUTRAL")
        self.assertIsNone(result["card"])
        self.assertIn("H4", result["reason"])

    def test_no_majority_stands_aside(self):
        bars_by_tf = {
            "M5": _series(120, step=0.5),
            "M15": _series(120, step=0.5),
            "M30": _series(120, step=-0.5),
            "H1": _series(120, step=-0.5),
            "H4": _series(120, step=0.0),
        }
        result = build(bars_by_tf)
        self.assertEqual(result["verdict"], "NEUTRAL")
        self.assertIsNone(result["card"])

    def test_insufficient_data_is_unavailable(self):
        result = build({tf: _series(5) for tf in TIMEFRAMES})
        self.assertFalse(result["available"])
        self.assertEqual(result["verdict"], "NEUTRAL")
        self.assertIsNone(result["card"])
        self.assertEqual(result["missing"], list(TIMEFRAMES))

    def test_flat_market_never_emits_a_card(self):
        result = build({tf: _flat(120) for tf in TIMEFRAMES})
        self.assertEqual(result["verdict"], "NEUTRAL")
        self.assertIsNone(result["card"])

    def test_every_result_is_marked_unvalidated(self):
        for step in (0.5, -0.5, 0.0):
            self.assertFalse(build(_all_tfs(step=step))["validated"])

    def test_partial_timeframes_still_read(self):
        # Only the operational and structural frames are present: the read is
        # still available, and agreement is counted over what exists.
        bars_by_tf = {
            "M15": _series(120, step=0.5),
            "H4": _series(120, step=0.5),
        }
        result = build(bars_by_tf)
        self.assertTrue(result["available"])
        self.assertIn("M5", result["missing"])
        # Two agreeing frames is below MIN_AGREE, so it stands aside.
        self.assertLess(2, MIN_AGREE)
        self.assertEqual(result["verdict"], "NEUTRAL")


class FormatTest(unittest.TestCase):
    def test_card_text_has_the_numbers_verbatim(self):
        result = build(_all_tfs(step=0.5))
        text = mtf_read.format_card(result)
        card = result["card"]
        self.assertIn(str(card["entry"]), text)
        self.assertIn(str(card["stop"]), text)
        self.assertIn(str(card["target"]), text)
        self.assertIn("شراء", text)

    def test_no_trade_text_when_neutral(self):
        result = build({tf: _flat(120) for tf in TIMEFRAMES})
        text = mtf_read.format_card(result)
        self.assertIn("لا صفقة", text)

    def test_veto_text_names_the_suppressed_sell(self):
        # Lower timeframes all fall, H4 rises: a genuine sell is suppressed by
        # the structural frame. The reader must see *that*, not a bare "no
        # trade", or it looks like the system never sees a sell at all.
        bars = {tf: _series(120, step=-0.5) for tf in TIMEFRAMES}
        bars["H4"] = _series(120, step=0.5)
        result = build(bars)
        self.assertIsNone(result["card"])
        text = mtf_read.format_card(result)
        self.assertIn("بيع", text)
        self.assertIn("H4", text)
        self.assertIn("صاعد", text)

    def test_veto_text_names_the_suppressed_buy(self):
        bars = {tf: _series(120, step=0.5) for tf in TIMEFRAMES}
        bars["H4"] = _series(120, step=-0.5)
        result = build(bars)
        text = mtf_read.format_card(result)
        self.assertIn("شراء", text)
        self.assertIn("هابط", text)

    def test_unavailable_text_when_no_data(self):
        text = mtf_read.format_card({"available": False})
        self.assertIn("لا تتوفّر", text)


if __name__ == "__main__":
    unittest.main()
