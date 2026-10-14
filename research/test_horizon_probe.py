"""Tests for the Phase 0 probe's math.

The probe's conclusion rests on three functions, so they get pinned here: a
wrong AUC or Spearman would silently turn "no edge" into "edge" or back. Pure
functions, no data files.

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

from research.horizon_probe import (auc, directional_controls,  # noqa: E402
                                    spearman, stdev)


class StdevTests(unittest.TestCase):
    def test_sample_stdev(self):
        # sample stdev of 1..4 = sqrt(5/3)
        self.assertAlmostEqual(stdev([1, 2, 3, 4]), 1.2909944, places=6)

    def test_constant_series_is_zero(self):
        self.assertEqual(stdev([5, 5, 5]), 0.0)

    def test_too_few_points_is_zero(self):
        self.assertEqual(stdev([1]), 0.0)


class SpearmanTests(unittest.TestCase):
    def test_perfectly_increasing_is_one(self):
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0, places=9)

    def test_perfectly_decreasing_is_minus_one(self):
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [40, 30, 20, 10]), -1.0, places=9)

    def test_rank_based_not_value_based(self):
        # A monotone but wildly non-linear map still ranks perfectly.
        self.assertAlmostEqual(spearman([1, 2, 3], [1, 100, 10000]), 1.0, places=9)


class AucTests(unittest.TestCase):
    def test_perfect_separation(self):
        self.assertEqual(auc([1, 2, 3, 4], [0, 0, 1, 1]), 1.0)

    def test_perfectly_reversed(self):
        self.assertEqual(auc([4, 3, 2, 1], [0, 0, 1, 1]), 0.0)

    def test_tie_counts_as_half(self):
        self.assertEqual(auc([1, 1], [0, 1]), 0.5)

    def test_single_class_is_undefined(self):
        import math
        self.assertTrue(math.isnan(auc([1, 2], [1, 1])))


class DirectionalControlTests(unittest.TestCase):
    def test_monotone_rising_gives_high_base_rate(self):
        # Always rising: every forward move is up, so the base rate is 1.0.
        closes = [float(i) for i in range(50)]
        times = list(range(50))
        base, mom, rev = directional_controls(closes, times, horizon=1, start=0)
        self.assertEqual(base, 1.0)

    def test_alternating_series_favours_reversal(self):
        # up, down, up, down... : the next move tends to reverse the last.
        closes = [1.0, 2.0, 1.0, 2.0, 1.0, 2.0, 1.0, 2.0]
        times = list(range(len(closes)))
        base, mom, rev = directional_controls(closes, times, horizon=1, start=0)
        self.assertGreater(rev, mom)


if __name__ == "__main__":
    unittest.main()
