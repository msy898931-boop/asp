"""Unit tests for the Phase 1 volatility lab's pure math.

These pin the two things that would silently corrupt the OOS numbers: the
causal rolling median (must not see the future) and the label definition
(forward vol vs trailing median). The model fit itself is exercised by
scripts/volatility_lab.py; here we test the parts that can be checked without
the corpus.
"""

from __future__ import annotations

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.volatility_lab import (
    rolling_median,
    stdev,
    vol_series,
)


class RollingMedianTest(unittest.TestCase):
    def test_window_of_one_is_the_value_itself(self):
        self.assertEqual(rolling_median([3.0, 1.0, 2.0], 1), [3.0, 1.0, 2.0])

    def test_matches_a_naive_recompute(self):
        values = [5.0, 3.0, 8.0, 1.0, 9.0, 2.0, 7.0]
        for lookback in (1, 2, 3, 5, 20):
            got = rolling_median(values, lookback)
            want = []
            for i in range(len(values)):
                lo = max(0, i - lookback + 1)
                window = sorted(values[lo:i + 1])
                want.append(window[len(window) // 2])
            self.assertEqual(got, want, f"lookback={lookback}")

    def test_is_causal_earlier_values_do_not_change(self):
        # Appending a large future value must not alter earlier medians.
        base = rolling_median([1.0, 2.0, 3.0, 4.0], 3)
        extended = rolling_median([1.0, 2.0, 3.0, 4.0, 100.0], 3)
        self.assertEqual(base, extended[:4])


class StdevTest(unittest.TestCase):
    def test_sample_stdev_matches_hand_value(self):
        self.assertAlmostEqual(stdev([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]),
                               math.sqrt(32.0 / 7.0), places=12)

    def test_degenerate_inputs_return_zero(self):
        self.assertEqual(stdev([]), 0.0)
        self.assertEqual(stdev([1.0]), 0.0)
        self.assertEqual(stdev([2.0, 2.0, 2.0]), 0.0)


class VolSeriesTest(unittest.TestCase):
    def test_log_returns_of_a_constant_series_are_zero(self):
        self.assertEqual(vol_series([100.0, 100.0, 100.0]), [0.0, 0.0, 0.0])

    def test_first_return_is_zero_by_convention(self):
        self.assertEqual(vol_series([100.0, 110.0])[0], 0.0)
        self.assertAlmostEqual(vol_series([100.0, 110.0])[1], math.log(1.1))


if __name__ == "__main__":
    unittest.main()
