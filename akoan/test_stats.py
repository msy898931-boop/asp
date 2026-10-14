#!/usr/bin/env python3
"""Tests for akoan's statistics — the part that must not lie.

These pin the arithmetic against known reference values, and pin the honesty
guarantees (no rate without enough data, no claim without base rate, no finding
without surviving correction). No network, no clock, no files.

Run: python3 -m unittest discover -s akoan -p "test_*.py"
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import stats as st  # noqa: E402


def row(win, **kw):
    base = {"win": win, "r_net": 1.6 if win else -1.0}
    base.update(kw)
    return base


class WilsonTests(unittest.TestCase):
    def test_reference_value(self):
        lo, hi = st.wilson_interval(5, 10)
        self.assertAlmostEqual(lo, 0.23659, places=4)
        self.assertAlmostEqual(hi, 0.76341, places=4)

    def test_stays_inside_zero_one(self):
        lo, hi = st.wilson_interval(0, 10)
        self.assertGreaterEqual(lo, 0.0)
        lo2, hi2 = st.wilson_interval(10, 10)
        self.assertLessEqual(hi2, 1.0)

    def test_none_below_min_n(self):
        self.assertIsNone(st.wilson_interval(5, 5))

    def test_none_on_impossible_counts(self):
        self.assertIsNone(st.wilson_interval(11, 10))
        self.assertIsNone(st.wilson_interval(0, 0))


class FisherTests(unittest.TestCase):
    def test_tea_tasting_reference(self):
        # The classic Fisher tea-tasting table [[3,1],[1,3]]: p = 0.4857.
        self.assertAlmostEqual(st.fisher_exact_p(3, 1, 1, 3), 0.4857, places=4)

    def test_identical_proportions_are_not_significant(self):
        self.assertGreater(st.fisher_exact_p(5, 5, 5, 5), 0.9)

    def test_clean_separation_is_significant(self):
        self.assertLess(st.fisher_exact_p(10, 0, 0, 10), 0.001)

    def test_p_is_a_probability(self):
        for table in [(1, 2, 3, 4), (0, 0, 0, 0), (7, 3, 2, 8)]:
            p = st.fisher_exact_p(*table)
            self.assertGreaterEqual(p, 0.0)
            self.assertLessEqual(p, 1.0)


class BaseRateTests(unittest.TestCase):
    def test_base_rate_is_the_overall_share(self):
        rows = [row(True)] * 3 + [row(False)] * 5
        self.assertAlmostEqual(st.base_rate(rows), 0.375, places=4)

    def test_none_when_too_thin(self):
        self.assertIsNone(st.base_rate([row(True)] * 3))

    def test_timeouts_are_excluded_from_the_base(self):
        rows = [row(True)] * 4 + [row(False)] * 4 + [row(None)] * 20
        self.assertAlmostEqual(st.base_rate(rows), 0.5, places=4)

    def test_edge_is_measured_against_the_base_not_a_half(self):
        # 6/8 wins looks like 75% (a "big edge" over 50%) but the base here is
        # 70%, so the honest edge is only +5 points.
        rows = [row(True)] * 6 + [row(False)] * 2 + [row(True)] * 8 + [row(False)] * 4
        base = st.base_rate(rows)
        self.assertAlmostEqual(base, 14 / 20, places=4)
        self.assertAlmostEqual(st.edge_vs_base(6, 8, base), 0.05, places=4)

    def test_edge_none_when_bucket_thin(self):
        self.assertIsNone(st.edge_vs_base(3, 3, 0.5))


class BenjaminiHochbergTests(unittest.TestCase):
    def test_survivors_are_the_small_p_values(self):
        keep = st.benjamini_hochberg([0.001, 0.01, 0.02, 0.04, 0.5], 0.10)
        self.assertEqual(keep, [True, True, True, True, False])

    def test_nothing_survives_when_all_p_are_large(self):
        self.assertEqual(st.benjamini_hochberg([0.4, 0.6, 0.9], 0.10),
                         [False, False, False])

    def test_order_is_preserved(self):
        keep = st.benjamini_hochberg([0.9, 0.001, 0.5], 0.10)
        self.assertEqual(len(keep), 3)
        self.assertTrue(keep[1])

    def test_single_hypothesis_is_just_a_threshold(self):
        self.assertEqual(st.benjamini_hochberg([0.05], 0.10), [True])
        self.assertEqual(st.benjamini_hochberg([0.20], 0.10), [False])

    def test_empty_input(self):
        self.assertEqual(st.benjamini_hochberg([], 0.10), [])


class PermutationTests(unittest.TestCase):
    def test_deterministic_for_a_seed(self):
        rows = ([row(True, g="a")] * 6 + [row(False, g="a")] * 2
                + [row(True, g="b")] * 2 + [row(False, g="b")] * 6)
        p1 = st.permutation_p(rows, lambda r: r["g"], trials=200)
        p2 = st.permutation_p(rows, lambda r: r["g"], trials=200)
        self.assertEqual(p1, p2)

    def test_a_clean_split_looks_unusual(self):
        rows = ([row(True, g="a")] * 12 + [row(False, g="a")] * 4
                + [row(True, g="b")] * 4 + [row(False, g="b")] * 12)
        p = st.permutation_p(rows, lambda r: r["g"], trials=500)
        self.assertLess(p, 0.05)

    def test_no_split_gives_none(self):
        rows = [row(True, g="a")] * 10
        self.assertIsNone(st.permutation_p(rows, lambda r: r["g"]))

    def test_p_is_never_zero(self):
        rows = ([row(True, g="a")] * 20 + [row(False, g="b")] * 20)
        self.assertGreater(st.permutation_p(rows, lambda r: r["g"], trials=100), 0.0)


if __name__ == "__main__":
    unittest.main()
