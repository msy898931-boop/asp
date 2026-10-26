"""Tests for the SMC zone study's pure math and plan builder.

The study's conclusion rests on the plan builder and the causal slicing, so
those are pinned here: a bullish sheet must enter below price and stop under the
zone, a bearish sheet must mirror it, the slicing must never see a bar that had
not closed, and a control plan must be symmetric. No data files, no network.

Run: python3 -m unittest discover -s research -p "test_*.py"
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from research.smc_zone_study import (  # noqa: E402
    _slice_at,
    build_plan,
    control_plan,
    evaluate_year,
    frame_step,
    resample,
)


def sheet(bias, close, atr=5.0, blocks=None, gaps=None):
    return {"timeframes": {"M15": {
        "close": close, "atr": atr, "structure": {"bias": bias},
        "order_blocks": blocks or [], "fair_value_gaps": gaps or []}}}


class BuildPlanTests(unittest.TestCase):
    def test_a_bullish_sheet_enters_below_price_and_stops_under_the_zone(self):
        s = sheet("bullish", 2010.0, atr=5.0,
                  blocks=[{"low": 1998.0, "high": 2002.0, "kind": "bullish"}])
        plan = build_plan(s)
        self.assertEqual(plan["direction"], "LONG")
        self.assertLess(plan["entry"], 2010.0)
        self.assertLess(plan["stop"], plan["entry"])
        self.assertLess(plan["entry"], plan["targets"][0])

    def test_a_bearish_sheet_enters_above_price_and_stops_over_the_zone(self):
        s = sheet("bearish", 1990.0, atr=5.0,
                  blocks=[{"low": 1998.0, "high": 2002.0, "kind": "bearish"}])
        plan = build_plan(s)
        self.assertEqual(plan["direction"], "SHORT")
        self.assertGreater(plan["entry"], 1990.0)
        self.assertGreater(plan["stop"], plan["entry"])
        self.assertGreater(plan["entry"], plan["targets"][0])

    def test_the_nearest_zone_is_chosen(self):
        s = sheet("bullish", 2010.0, atr=5.0,
                  blocks=[{"low": 1978.0, "high": 1982.0, "kind": "bullish"},
                          {"low": 1998.0, "high": 2002.0, "kind": "bullish"}])
        plan = build_plan(s)
        self.assertEqual(plan["entry"], 2000.0)     # the closer zone's midpoint

    def test_a_gap_can_be_the_entry_zone(self):
        s = sheet("bullish", 2010.0, atr=5.0,
                  gaps=[{"low": 1998.0, "high": 2002.0, "kind": "bullish"}])
        plan = build_plan(s)
        self.assertEqual(plan["zone"], "FVG")

    def test_no_zone_on_the_correct_side_yields_no_plan(self):
        s = sheet("bullish", 1990.0, atr=5.0,
                  blocks=[{"low": 1998.0, "high": 2002.0, "kind": "bullish"}])
        self.assertIsNone(build_plan(s))            # nothing below 1990

    def test_a_neutral_bias_yields_no_plan(self):
        s = sheet("neutral", 2010.0, atr=5.0,
                  blocks=[{"low": 1998.0, "high": 2002.0, "kind": "bullish"}])
        self.assertIsNone(build_plan(s))

    def test_a_missing_sheet_yields_no_plan(self):
        self.assertIsNone(build_plan({}))

    def test_the_targets_are_two_and_three_r(self):
        s = sheet("bullish", 2010.0, atr=5.0,
                  blocks=[{"low": 1998.0, "high": 2002.0, "kind": "bullish"}])
        plan = build_plan(s)
        risk = plan["entry"] - plan["stop"]
        self.assertAlmostEqual(plan["targets"][0], plan["entry"] + 2.0 * risk,
                               places=1)
        self.assertAlmostEqual(plan["targets"][1], plan["entry"] + 3.0 * risk,
                               places=1)


class ControlTests(unittest.TestCase):
    def test_a_long_control_is_symmetric(self):
        plan = control_plan(2000.0, 5.0, "LONG")
        self.assertEqual(plan["entry"], 2000.0)
        self.assertEqual(plan["stop"], 1995.0)
        self.assertEqual(plan["targets"][0], 2005.0)

    def test_a_short_control_is_symmetric(self):
        plan = control_plan(2000.0, 5.0, "SHORT")
        self.assertEqual(plan["stop"], 2005.0)
        self.assertEqual(plan["targets"][0], 1995.0)


class SlicingTests(unittest.TestCase):
    def test_slicing_never_sees_a_bar_after_the_cutoff(self):
        bars = [{"time": t} for t in (10, 20, 30, 40, 50)]
        got = _slice_at(bars, 35, 10)
        self.assertEqual([b["time"] for b in got], [10, 20, 30])

    def test_slicing_keeps_only_the_last_count(self):
        bars = [{"time": t} for t in (10, 20, 30, 40, 50)]
        got = _slice_at(bars, 50, 2)
        self.assertEqual([b["time"] for b in got], [40, 50])


class ResampleTests(unittest.TestCase):
    def test_m1_aggregates_into_m15_buckets(self):
        base = 1760000000 - (1760000000 % 900)
        m1 = []
        for k in range(15):
            m1.append({"time": base + k * 60, "open": 100.0 + k,
                       "high": 100.0 + k + 1, "low": 100.0 + k - 1,
                       "close": 100.0 + k})
        out = resample(m1, 15)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["open"], 100.0)
        self.assertEqual(out[0]["close"], 114.0)
        self.assertEqual(out[0]["high"], 115.0)
        self.assertEqual(out[0]["low"], 99.0)


class FrameStepTests(unittest.TestCase):
    """The decision frame is a knob, and M15 must keep its original cadence."""

    def test_the_default_frame_keeps_the_original_step(self):
        # The M15 run's numbers are a committed artefact; the step must not move.
        self.assertEqual(frame_step("M15"), 8)

    def test_a_slower_frame_samples_fewer_of_its_own_bars(self):
        self.assertEqual(frame_step("M30"), 4)
        self.assertEqual(frame_step("H1"), 2)
        self.assertEqual(frame_step("H4"), 1)

    def test_an_unknown_frame_still_advances(self):
        self.assertGreaterEqual(frame_step("bogus"), 1)


class EvaluateFrameTests(unittest.TestCase):
    """evaluate_year must honour the decision frame, and refuse an unknown one."""

    @staticmethod
    def _tape(days: int = 12) -> list:
        # A flat-ish M1 tape long enough for a higher frame to fill a window.
        base = 1760000000 - (1760000000 % 3600)
        bars = []
        for k in range(days * 24 * 60):
            px = 2000.0 + (k % 40) * 0.1
            bars.append({"time": base + k * 60, "open": px, "high": px + 0.5,
                         "low": px - 0.5, "close": px})
        return bars

    def test_an_unknown_frame_yields_nothing(self):
        out = evaluate_year(self._tape(), 2021, tf="bogus")
        self.assertEqual(out, {"smc": [], "confluence": [], "control": []})

    def test_the_default_frame_is_m15(self):
        # No tf passed must equal tf="M15" — the original call signature.
        tape = self._tape()
        self.assertEqual(evaluate_year(tape, 2021),
                         evaluate_year(tape, 2021, tf="M15"))

    def test_a_higher_frame_still_produces_a_control(self):
        out = evaluate_year(self._tape(), 2021, tf="H1")
        # The control does not depend on a zone being found, only on ATR > 0,
        # so a higher frame must still return a symmetric ruler to compare to.
        self.assertTrue(out["control"])


if __name__ == "__main__":
    unittest.main()
