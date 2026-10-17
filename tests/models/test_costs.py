"""Deterministic tests for the T20 RULE B cost-tier model."""

from __future__ import annotations

import unittest

from src.models.costs import (
    CostAssumptions,
    cost_tiers,
    net_expectancy_r,
    tier_by_name,
)
from src.models.splits import SplitError


class AssumptionsTest(unittest.TestCase):
    def test_defaults_match_mission_numbers(self):
        a = CostAssumptions()
        self.assertEqual(a.spread, 0.30)
        self.assertEqual(a.commission, 0.10)
        self.assertEqual(a.slippage, 0.20)
        self.assertAlmostEqual(a.floor(), 0.40)
        self.assertAlmostEqual(a.conservative(), 0.60)

    def test_negative_component_rejected(self):
        with self.assertRaises(SplitError):
            CostAssumptions(spread=-0.01).validate()

    def test_zero_costs_allowed(self):
        a = CostAssumptions(0.0, 0.0, 0.0)
        self.assertAlmostEqual(a.floor(), 0.0)
        self.assertAlmostEqual(a.conservative(), 0.0)

    def test_nan_and_inf_rejected(self):
        # F20-1: `NaN < 0` is False, so non-finite costs must be rejected
        # explicitly, not allowed to reach a decision-grade number.
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(SplitError):
                CostAssumptions(spread=bad).validate()
        with self.assertRaises(SplitError):
            cost_tiers(CostAssumptions(0.1, float("nan"), 0.2))


class TiersTest(unittest.TestCase):
    def test_three_tiers_named_and_ordered(self):
        tiers = cost_tiers()
        self.assertEqual([t.name for t in tiers], ["zero", "floor", "conservative"])
        self.assertLess(tiers[0].round_trip, tiers[1].round_trip)
        self.assertLess(tiers[1].round_trip, tiers[2].round_trip)

    def test_only_zero_is_reference(self):
        tiers = cost_tiers()
        self.assertFalse(tiers[0].decision_grade)
        self.assertTrue(tiers[1].decision_grade)
        self.assertTrue(tiers[2].decision_grade)

    def test_dead_band_equals_round_trip(self):
        for tier in cost_tiers():
            self.assertEqual(tier.dead_band(), tier.round_trip)

    def test_tier_by_name(self):
        self.assertAlmostEqual(tier_by_name("conservative").round_trip, 0.60)

    def test_unknown_tier_rejected(self):
        with self.assertRaises(SplitError):
            tier_by_name("free")

    def test_tiers_scale_with_assumptions(self):
        tiers = cost_tiers(CostAssumptions(spread=1.0, commission=0.0, slippage=0.5))
        self.assertAlmostEqual(tiers[1].round_trip, 1.0)
        self.assertAlmostEqual(tiers[2].round_trip, 1.5)


class CostRTest(unittest.TestCase):
    def test_cost_in_r_units(self):
        conservative = tier_by_name("conservative")
        self.assertAlmostEqual(conservative.cost_r(risk_distance=3.0), 0.20)

    def test_zero_risk_distance_rejected(self):
        with self.assertRaises(SplitError):
            tier_by_name("floor").cost_r(0.0)

    def test_net_expectancy(self):
        self.assertAlmostEqual(net_expectancy_r(0.75, 0.20), 0.55)
        self.assertLess(net_expectancy_r(0.10, 0.20), 0.0)


if __name__ == "__main__":
    unittest.main()
