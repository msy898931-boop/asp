"""Deterministic tests for the calibration utilities.

Expected values are hand-computed in the comments so the tests double as a
specification of each metric.
"""

from __future__ import annotations

import unittest

from src.models.calibration import (
    CalibrationReport,
    brier_score,
    brier_skill_score,
    calibration_report,
    coverage_analysis,
    direction_breakdown,
    expected_calibration_error,
    maximum_calibration_error,
    reliability_diagram,
    threshold_coverage,
    threshold_coverage_suite,
)
from src.models.splits import SplitError


class BrierTest(unittest.TestCase):
    def test_uninformative_forecast_scores_the_baseline(self):
        # p=0.5 for both; outcomes 0 and 1.
        # (0.5-0)^2 + (0.5-1)^2 = 0.25 + 0.25 = 0.5; /2 = 0.25
        self.assertAlmostEqual(brier_score([0.5, 0.5], [0, 1]), 0.25)

    def test_perfect_confident_forecast_scores_zero(self):
        self.assertAlmostEqual(brier_score([1.0, 0.0], [1, 0]), 0.0)

    def test_perfectly_wrong_forecast_scores_one(self):
        self.assertAlmostEqual(brier_score([1.0, 0.0], [0, 1]), 1.0)

    def test_known_mixed_case(self):
        # p=0.9 twice with y=1: 0.01 each; p=0.1 twice with y=0: 0.01 each.
        # total 0.04; /4 = 0.01
        self.assertAlmostEqual(brier_score([0.9, 0.9, 0.1, 0.1], [1, 1, 0, 0]), 0.01)

    def test_skill_zero_at_baseline(self):
        self.assertAlmostEqual(brier_skill_score([0.5, 0.5], [0, 1]), 0.0)

    def test_skill_one_at_perfection(self):
        self.assertAlmostEqual(brier_skill_score([1.0, 0.0], [1, 0]), 1.0)


class EceTest(unittest.TestCase):
    def test_perfectly_calibrated_constant_half(self):
        self.assertAlmostEqual(expected_calibration_error([0.5, 0.5], [0, 1]), 0.0)

    def test_known_ece(self):
        # bin(0.9): pred 0.9, emp 1.0, gap 0.1, weight 0.5 -> 0.05
        # bin(0.1): pred 0.1, emp 0.0, gap 0.1, weight 0.5 -> 0.05
        # ECE = 0.10
        self.assertAlmostEqual(
            expected_calibration_error([0.9, 0.9, 0.1, 0.1], [1, 1, 0, 0]), 0.10
        )

    def test_maximum_error_is_worst_bin(self):
        self.assertAlmostEqual(
            maximum_calibration_error([0.9, 0.9, 0.1, 0.1], [1, 1, 0, 0]), 0.10
        )

    def test_overconfident_bin_has_negative_gap(self):
        # Single sample p=1.0, outcome 0 -> gap = 0 - 1 = -1
        diagram = reliability_diagram([1.0], [0])
        self.assertEqual(len(diagram), 1)
        self.assertAlmostEqual(diagram[0].gap, -1.0)

    def test_empty_bins_are_omitted(self):
        diagram = reliability_diagram([0.9, 0.9, 0.1, 0.1], [1, 1, 0, 0], bins=10)
        self.assertEqual([b.index for b in diagram], [1, 9])

    def test_probability_one_lands_in_last_bin(self):
        diagram = reliability_diagram([1.0], [1], bins=10)
        self.assertEqual(diagram[0].index, 9)


class CoverageTest(unittest.TestCase):
    def test_tier_assignment(self):
        cov = {c.tier: c for c in coverage_analysis([0.1, 0.5, 0.9], [0, 1, 1])}
        self.assertEqual(cov["low"].count, 1)
        self.assertEqual(cov["medium"].count, 1)
        self.assertEqual(cov["high"].count, 1)

    def test_coverage_fractions_sum_to_one(self):
        probs = [0.1, 0.2, 0.5, 0.7, 0.9]
        outcomes = [0, 0, 1, 1, 1]
        total = sum(c.coverage for c in coverage_analysis(probs, outcomes))
        self.assertAlmostEqual(total, 1.0)

    def test_empty_tier_is_reported_not_hidden(self):
        cov = {c.tier: c for c in coverage_analysis([0.9, 0.9], [1, 1])}
        self.assertEqual(cov["low"].count, 0)
        self.assertEqual(cov["low"].coverage, 0.0)
        self.assertEqual(cov["high"].count, 2)


class ReportTest(unittest.TestCase):
    def test_meets_target(self):
        report = calibration_report([1.0, 0.0], [1, 0])
        self.assertIsInstance(report, CalibrationReport)
        self.assertTrue(report.meets_target())
        self.assertFalse(report.is_failure())

    def test_uninformative_is_failure(self):
        report = calibration_report([0.5, 0.5], [0, 1])
        self.assertFalse(report.meets_target())
        self.assertTrue(report.is_failure())  # brier == baseline -> no skill

    def test_report_is_deterministic(self):
        probs = [0.9, 0.9, 0.1, 0.1]
        outcomes = [1, 1, 0, 0]
        self.assertEqual(
            calibration_report(probs, outcomes), calibration_report(probs, outcomes)
        )


class ValidationTest(unittest.TestCase):
    def test_length_mismatch_rejected(self):
        with self.assertRaises(SplitError):
            brier_score([0.5], [0, 1])

    def test_empty_sample_rejected(self):
        with self.assertRaises(SplitError):
            brier_score([], [])

    def test_out_of_range_probability_rejected(self):
        with self.assertRaises(SplitError):
            brier_score([1.5], [1])

    def test_non_binary_outcome_rejected(self):
        with self.assertRaises(SplitError):
            brier_score([0.5], [2])

    def test_non_positive_bins_rejected(self):
        with self.assertRaises(SplitError):
            reliability_diagram([0.5], [1], bins=0)


if __name__ == "__main__":
    unittest.main()


class ThresholdCoverageTest(unittest.TestCase):
    def test_counts_and_gap_are_exact(self):
        probs = [0.9, 0.62, 0.58, 0.4, 0.3]
        outcomes = [1, 1, 0, 1, 0]
        p55 = threshold_coverage(probs, outcomes, 0.55)
        self.assertEqual(p55.count, 3)
        self.assertAlmostEqual(p55.coverage, 3 / 5)
        self.assertAlmostEqual(p55.accuracy, 2 / 3)
        self.assertAlmostEqual(p55.mean_probability, (0.9 + 0.62 + 0.58) / 3)
        p65 = threshold_coverage(probs, outcomes, 0.65)
        self.assertEqual(p65.count, 1)
        self.assertEqual(p65.accuracy, 1.0)

    def test_empty_threshold_is_visible_not_hidden(self):
        tc = threshold_coverage([0.5, 0.4], [1, 0], 0.9)
        self.assertEqual(tc.count, 0)
        self.assertEqual(tc.coverage, 0.0)
        self.assertEqual(tc.accuracy, 0.0)

    def test_suite_is_ordered_and_defaults(self):
        suite = threshold_coverage_suite([0.9, 0.6, 0.5], [1, 1, 0])
        self.assertEqual([t.tier for t in suite], ["p>=0.55", "p>=0.60", "p>=0.65"])
        covs = [t.coverage for t in suite]
        self.assertEqual(covs, sorted(covs, reverse=True))


class DirectionBreakdownTest(unittest.TestCase):
    def test_long_short_split_is_exhaustive(self):
        probs = [0.8, 0.6, 0.4, 0.2]
        outcomes = [1, 0, 0, 1]
        parts = {d.direction: d for d in direction_breakdown(probs, outcomes)}
        self.assertEqual(parts["LONG"].count, 2)
        self.assertEqual(parts["SHORT"].count, 2)
        self.assertAlmostEqual(parts["LONG"].coverage + parts["SHORT"].coverage, 1.0)
        self.assertAlmostEqual(parts["LONG"].accuracy, 0.5)
        self.assertAlmostEqual(parts["SHORT"].accuracy, 0.5)

    def test_empty_direction_reported(self):
        parts = {d.direction: d for d in direction_breakdown([0.9, 0.7], [1, 1])}
        self.assertEqual(parts["SHORT"].count, 0)
        self.assertEqual(parts["SHORT"].coverage, 0.0)


class ReportCarriesNewSurfaceTest(unittest.TestCase):
    def test_calibration_report_populates_thresholds_and_directions(self):
        probs = [0.9, 0.62, 0.58, 0.4, 0.3]
        outcomes = [1, 1, 0, 1, 0]
        r = calibration_report(probs, outcomes)
        self.assertEqual(len(r.threshold_coverage), 3)
        self.assertEqual([d.direction for d in r.directions], ["LONG", "SHORT"])

    def test_boundary_at_exactly_one_half_is_long(self):
        parts = {d.direction: d for d in direction_breakdown([0.5], [1])}
        self.assertEqual(parts["LONG"].count, 1)
        self.assertEqual(parts["SHORT"].count, 0)
