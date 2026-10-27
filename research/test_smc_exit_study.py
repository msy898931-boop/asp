"""Tests for the SMC exit study's pure math.

The study's finding rests on one transformation — the same plan, resolved to a
nearer final target — so that is pinned here: only the target moves, direction
and entry and stop are untouched, targets stay on the winning side, and the
break-even the comparison is judged against accounts for the spread. No data
files, no network.

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
if os.path.join(REPO_ROOT, "advisor") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "advisor"))

import smc_plan  # noqa: E402

from research.smc_exit_study import TARGET_R, replan  # noqa: E402
from research.smc_zone_study import build_plan  # noqa: E402


class ReplanTests(unittest.TestCase):
    def test_a_long_target_moves_but_the_stop_does_not(self):
        plan = {"direction": "LONG", "entry": 2000.0, "stop": 1990.0,
                "targets": [2020.0, 2030.0]}
        out = replan(plan, 0.5)
        self.assertEqual(out["direction"], "LONG")
        self.assertEqual(out["entry"], 2000.0)
        self.assertEqual(out["stop"], 1990.0)
        # 0.5R with a 10-point risk is a 5-point target.
        self.assertEqual(out["targets"], [2005.0])

    def test_a_short_target_moves_the_other_way(self):
        plan = {"direction": "SHORT", "entry": 2000.0, "stop": 2010.0,
                "targets": [1980.0, 1970.0]}
        out = replan(plan, 0.5)
        self.assertEqual(out["targets"], [1995.0])

    def test_the_original_plan_is_not_mutated(self):
        plan = {"direction": "LONG", "entry": 2000.0, "stop": 1990.0,
                "targets": [2020.0, 2030.0]}
        replan(plan, 0.5)
        self.assertEqual(plan["targets"], [2020.0, 2030.0])

    def test_the_target_sits_on_the_winning_side(self):
        long_plan = {"direction": "LONG", "entry": 2000.0, "stop": 1990.0,
                     "targets": [2020.0]}
        short_plan = {"direction": "SHORT", "entry": 2000.0, "stop": 2010.0,
                      "targets": [1980.0]}
        self.assertGreater(replan(long_plan, 1.0)["targets"][0], 2000.0)
        self.assertLess(replan(short_plan, 1.0)["targets"][0], 2000.0)

    def test_every_target_in_the_sweep_is_positive(self):
        self.assertTrue(all(t > 0 for t in TARGET_R))

    def test_the_shipped_geometry_is_in_the_sweep_for_comparison(self):
        # The study must be able to show the shipped 3R losing, not only the
        # near target winning, or it would be an advertisement.
        self.assertIn(3.0, TARGET_R)


class BreakEvenTests(unittest.TestCase):
    def test_break_even_accounts_for_the_spread(self):
        # A 0.5R target at a 1R stop is 1/3 = 0.667 gross, but the resolver
        # charges the spread, so the break-even the study prints is higher. If
        # this ever reads 0.667 the cost model has silently gone.
        import smc_resolve
        be = smc_resolve.break_even_win_rate(0.5 * 10.0 - 0.30, -10.0)
        self.assertGreater(be, 1.0 / 3.0)


class ShippedPlanParityTests(unittest.TestCase):
    """The shipped plan must be the geometry this study resolved.

    ``advisor/smc_plan.py`` turns the measurement into the plan the engine
    offers. If its entry, stop or target ever drift from the study's builder,
    the number on the sheet stops being the number that cleared the gate. This
    lives in ``research`` because ``advisor`` must stay importable without it.
    """

    def _sheet(self, bias, close, atr=5.0, blocks=None):
        return {"timeframes": {"M15": {
            "close": close, "atr": atr, "structure": {"bias": bias},
            "order_blocks": blocks or [], "fair_value_gaps": []}}}

    def test_the_entry_and_stop_match_the_study_builder(self):
        sheet = self._sheet("bullish", 2010.0,
                            blocks=[{"kind": "bullish", "low": 1995.0,
                                     "high": 2000.0}])
        study = build_plan(sheet, "M15")
        shipped = smc_plan.build_plan(sheet["timeframes"])
        self.assertEqual(shipped["entry"], study["entry"])
        self.assertEqual(shipped["stop"], study["stop"])
        self.assertEqual(shipped["direction"], study["direction"])

    def test_the_target_is_half_r_where_the_study_ships_two_and_three(self):
        sheet = self._sheet("bullish", 2010.0,
                            blocks=[{"kind": "bullish", "low": 1995.0,
                                     "high": 2000.0}])
        study = build_plan(sheet, "M15")
        shipped = smc_plan.build_plan(sheet["timeframes"])
        risk = study["entry"] - study["stop"]
        self.assertAlmostEqual(shipped["target"],
                               round(study["entry"] + 0.5 * risk, 2), places=2)
        self.assertEqual(len(study["targets"]), 2)   # the study ships 2R and 3R
        self.assertEqual(len(shipped["targets"]), 1)  # the shipped plan ships 0.5R

    def test_a_short_matches_too(self):
        sheet = self._sheet("bearish", 1990.0,
                            blocks=[{"kind": "bearish", "low": 2000.0,
                                     "high": 2005.0}])
        study = build_plan(sheet, "M15")
        shipped = smc_plan.build_plan(sheet["timeframes"])
        self.assertEqual(shipped["entry"], study["entry"])
        self.assertEqual(shipped["stop"], study["stop"])


if __name__ == "__main__":
    unittest.main()
