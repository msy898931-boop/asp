"""Tests for the deterministic near-target plan.

The plan reproduces the geometry ``research/smc_exit_study.py`` resolved and
found clearing break-even, so these pin that it *is* that geometry: a long enters
below price and stops under the zone with a 0.5R target, a short mirrors it, the
nearest zone wins, and no zone on the correct side yields no plan. It must also
stay off by default — a plan that is evidence about a measurement is opt-in. No
network, no model, no wall clock.

Run: python3 -m unittest discover -s advisor -p "test_*.py"
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import smc_plan  # noqa: E402


def facts(bias, close, atr=5.0, blocks=None, gaps=None):
    return {"M15": {"close": close, "atr": atr, "structure": {"bias": bias},
                    "order_blocks": blocks or [],
                    "fair_value_gaps": gaps or []}}


class BuildPlanTests(unittest.TestCase):
    def test_a_bullish_sheet_enters_below_price_and_stops_under_the_zone(self):
        plan = smc_plan.build_plan(facts(
            "bullish", 2010.0, atr=5.0,
            blocks=[{"kind": "bullish", "low": 1995.0, "high": 2000.0}]))
        self.assertEqual(plan["direction"], "LONG")
        self.assertEqual(plan["entry"], 1997.5)          # midpoint of the zone
        self.assertEqual(plan["stop"], 1990.0)           # low - 1 ATR
        # 0.5R of a 7.5-point risk is 3.75, rounded to two places.
        self.assertAlmostEqual(plan["target"], 2001.25, places=2)
        self.assertLess(plan["entry"], 2010.0)
        self.assertLess(plan["stop"], plan["entry"])

    def test_a_bearish_sheet_mirrors_it(self):
        plan = smc_plan.build_plan(facts(
            "bearish", 1990.0, atr=5.0,
            blocks=[{"kind": "bearish", "low": 2000.0, "high": 2005.0}]))
        self.assertEqual(plan["direction"], "SHORT")
        self.assertEqual(plan["entry"], 2002.5)
        self.assertEqual(plan["stop"], 2010.0)           # high + 1 ATR
        self.assertAlmostEqual(plan["target"], 1998.75, places=2)
        self.assertGreater(plan["entry"], 1990.0)
        self.assertGreater(plan["stop"], plan["entry"])

    def test_the_nearest_zone_is_chosen(self):
        plan = smc_plan.build_plan(facts(
            "bullish", 2010.0,
            blocks=[{"kind": "bullish", "low": 1980.0, "high": 1985.0},
                    {"kind": "bullish", "low": 1995.0, "high": 2000.0}]))
        self.assertEqual(plan["entry"], 1997.5)          # the nearer one

    def test_a_fair_value_gap_can_be_the_entry_zone(self):
        plan = smc_plan.build_plan(facts(
            "bullish", 2010.0,
            gaps=[{"kind": "bullish", "low": 1995.0, "high": 2000.0}]))
        self.assertEqual(plan["entry"], 1997.5)
        self.assertIn("fair-value gap", plan["poi"])

    def test_a_zone_on_the_wrong_side_is_ignored(self):
        # A bullish sheet with only a zone above price has nothing to buy from.
        plan = smc_plan.build_plan(facts(
            "bullish", 2000.0,
            blocks=[{"kind": "bullish", "low": 2010.0, "high": 2015.0}]))
        self.assertIsNone(plan)

    def test_a_neutral_bias_yields_no_plan(self):
        plan = smc_plan.build_plan(facts(
            "neutral", 2010.0,
            blocks=[{"kind": "bullish", "low": 1995.0, "high": 2000.0}]))
        self.assertIsNone(plan)

    def test_no_zones_yields_no_plan(self):
        self.assertIsNone(smc_plan.build_plan(facts("bullish", 2010.0)))

    def test_a_missing_frame_yields_no_plan(self):
        self.assertIsNone(smc_plan.build_plan({"H4": {}}))

    def test_a_missing_atr_yields_no_plan(self):
        self.assertIsNone(smc_plan.build_plan(
            {"M15": {"close": 2010.0, "structure": {"bias": "bullish"},
                     "order_blocks": [{"low": 1995.0, "high": 2000.0}]}}))

    def test_the_target_is_half_r_not_the_shipped_two_r(self):
        # The whole point: this plan carries the measured geometry, not the
        # 2R/3R the model is instructed to use.
        plan = smc_plan.build_plan(facts(
            "bullish", 2010.0, atr=5.0,
            blocks=[{"kind": "bullish", "low": 1995.0, "high": 2000.0}]))
        self.assertEqual(plan["measured_target_r"], 0.5)
        self.assertLess(plan["reward_risk"], 1.0)

    def test_the_plan_is_marked_as_the_measured_geometry(self):
        plan = smc_plan.build_plan(facts(
            "bullish", 2010.0,
            blocks=[{"kind": "bullish", "low": 1995.0, "high": 2000.0}]))
        self.assertEqual(plan["source"], "measured-geometry")
        self.assertFalse(plan["confidence_validated"])
        self.assertTrue(plan["anchored"])
        self.assertIn("smc_exit_study", plan["measured_record_source"])

    def test_the_plan_is_not_mutated_across_calls(self):
        f = facts("bullish", 2010.0,
                  blocks=[{"kind": "bullish", "low": 1995.0, "high": 2000.0}])
        first = smc_plan.build_plan(f)
        first["entry"] = 0.0
        self.assertNotEqual(smc_plan.build_plan(f)["entry"], 0.0)


class EnabledTests(unittest.TestCase):
    def test_off_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(smc_plan.enabled())

    def test_turned_on_by_the_flag(self):
        with mock.patch.dict(os.environ,
                             {"ASTRA_SMC_DETERMINISTIC_PLAN": "1"}, clear=True):
            self.assertTrue(smc_plan.enabled())

    def test_a_false_value_leaves_it_off(self):
        with mock.patch.dict(os.environ,
                             {"ASTRA_SMC_DETERMINISTIC_PLAN": "no"}, clear=True):
            self.assertFalse(smc_plan.enabled())


if __name__ == "__main__":
    unittest.main()
