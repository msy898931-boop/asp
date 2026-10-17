"""Deterministic tests for the T05 calibrators."""

from __future__ import annotations

import itertools
import math
import unittest

from src.models.calibration import expected_calibration_error
from src.models.calibrators import (
    HistogramCalibrator,
    IsotonicCalibrator,
    PlattCalibrator,
    fit_calibrator,
)
from src.models.splits import SplitError


def overconfident_sample():
    """Raw scores that are systematically too extreme.

    The true positive rate for a score s is 0.5 + 0.5*(s-0.5), i.e. the model is
    overconfident by a factor of two. A good calibrator shrinks toward 0.5 and
    lowers ECE.
    """
    scores = []
    outcomes = []
    for i in range(200):
        s = (i + 0.5) / 200.0            # 0.0025 .. 0.9975
        p_true = 0.5 + 0.5 * (s - 0.5)   # shrunken truth
        scores.append(s)
        outcomes.append(1 if p_true >= 0.5 else 0)
    return scores, outcomes


class PlattTest(unittest.TestCase):
    def test_reduces_ece_on_overconfident_scores(self):
        scores, outcomes = overconfident_sample()
        before = expected_calibration_error(scores, outcomes, bins=10)
        cal = PlattCalibrator.fit(scores, outcomes)
        after = expected_calibration_error(
            cal.transform_batch(scores), outcomes, bins=10
        )
        self.assertLess(after, before)

    def test_outputs_are_probabilities(self):
        scores, outcomes = overconfident_sample()
        cal = PlattCalibrator.fit(scores, outcomes)
        for p in cal.transform_batch(scores):
            self.assertGreaterEqual(p, 0.0)
            self.assertLessEqual(p, 1.0)

    def test_deterministic(self):
        scores, outcomes = overconfident_sample()
        a = PlattCalibrator.fit(scores, outcomes)
        b = PlattCalibrator.fit(scores, outcomes)
        self.assertEqual((a.a, a.b), (b.a, b.b))
        self.assertEqual(a.iterations, b.iterations)

    def test_monotone_in_score(self):
        scores, outcomes = overconfident_sample()
        cal = PlattCalibrator.fit(scores, outcomes)
        self.assertGreater(cal.transform(0.9), cal.transform(0.1))

    def test_length_mismatch_rejected(self):
        with self.assertRaises(SplitError):
            PlattCalibrator.fit([0.1, 0.2], [1])

    def test_empty_rejected(self):
        with self.assertRaises(SplitError):
            PlattCalibrator.fit([], [])

    def test_non_finite_score_rejected(self):
        with self.assertRaises(SplitError):
            PlattCalibrator.fit([0.1, float("nan")], [0, 1])

    def test_negative_l2_rejected(self):
        with self.assertRaises(SplitError):
            PlattCalibrator.fit([0.1, 0.2], [0, 1], l2=-1.0)


class IsotonicTest(unittest.TestCase):
    def test_output_is_monotone_non_decreasing(self):
        scores, outcomes = overconfident_sample()
        cal = IsotonicCalibrator.fit(scores, outcomes)
        transformed = cal.transform_batch(scores)
        for a, b in itertools.pairwise(transformed):
            self.assertLessEqual(a, b + 1e-12)

    def test_recovers_a_clean_step(self):
        # score < 0.5 -> 0, score >= 0.5 -> 1.
        scores = [0.1, 0.2, 0.3, 0.7, 0.8, 0.9]
        outcomes = [0, 0, 0, 1, 1, 1]
        cal = IsotonicCalibrator.fit(scores, outcomes)
        self.assertLess(cal.transform(0.2), 0.5)
        self.assertGreater(cal.transform(0.8), 0.5)

    def test_ties_are_order_independent(self):
        scores = [0.5, 0.5, 0.5, 0.5]
        outcomes = [0, 1, 1, 1]
        a = IsotonicCalibrator.fit(scores, outcomes)
        b = IsotonicCalibrator.fit(list(reversed(scores)), list(reversed(outcomes)))
        self.assertEqual(a.values, b.values)

    def test_deterministic(self):
        scores, outcomes = overconfident_sample()
        a = IsotonicCalibrator.fit(scores, outcomes)
        b = IsotonicCalibrator.fit(scores, outcomes)
        self.assertEqual(a.thresholds, b.thresholds)
        self.assertEqual(a.values, b.values)

    def test_pava_enforces_block_means_non_decreasing(self):
        # A non-monotone empirical sequence must be pooled.
        scores = [0.1, 0.2, 0.3, 0.4]
        outcomes = [1, 0, 0, 1]  # rates 1,0,0,1 -> must be pooled
        cal = IsotonicCalibrator.fit(scores, outcomes)
        for a, b in zip(cal.values, cal.values[1:]):
            self.assertLessEqual(a, b + 1e-12)


class HistogramTest(unittest.TestCase):
    def test_bin_rate_matches_empirical(self):
        scores = [0.05, 0.05, 0.15, 0.15]
        outcomes = [1, 0, 1, 1]
        cal = HistogramCalibrator.fit(scores, outcomes, bins=10)
        self.assertAlmostEqual(cal.transform(0.05), 0.5)   # bin 0: 1/2
        self.assertAlmostEqual(cal.transform(0.15), 1.0)   # bin 1: 2/2

    def test_outputs_are_probabilities(self):
        scores, outcomes = overconfident_sample()
        cal = HistogramCalibrator.fit(scores, outcomes)
        for p in cal.transform_batch(scores):
            self.assertGreaterEqual(p, 0.0)
            self.assertLessEqual(p, 1.0)

    def test_deterministic(self):
        scores, outcomes = overconfident_sample()
        a = HistogramCalibrator.fit(scores, outcomes)
        b = HistogramCalibrator.fit(scores, outcomes)
        self.assertEqual(a.rates, b.rates)

    def test_zero_bins_rejected(self):
        with self.assertRaises(SplitError):
            HistogramCalibrator.fit([0.1, 0.2], [0, 1], bins=0)

    def test_degenerate_range_does_not_crash(self):
        cal = HistogramCalibrator.fit([0.5, 0.5, 0.5], [0, 1, 1], bins=5)
        self.assertTrue(math.isfinite(cal.transform(0.5)))


class FactoryTest(unittest.TestCase):
    def test_all_methods(self):
        scores, outcomes = overconfident_sample()
        for method in ("platt", "isotonic", "histogram"):
            cal = fit_calibrator(method, scores, outcomes)
            self.assertTrue(hasattr(cal, "transform"))

    def test_unknown_method_rejected(self):
        with self.assertRaises(SplitError):
            fit_calibrator("magic", [0.1], [0])


class LeakageDisciplineTest(unittest.TestCase):
    def test_calibrating_on_the_evaluation_rows_is_not_allowed_evidence(self):
        # This test documents the rule, not a code path: a calibrator fit and
        # scored on the SAME rows looks perfect but proves nothing. We assert the
        # in-sample ECE is implausibly low, which is exactly why the harness
        # forbids using it as evidence (fit on validation, score on OOS).
        scores, outcomes = overconfident_sample()
        cal = IsotonicCalibrator.fit(scores, outcomes)
        in_sample = expected_calibration_error(
            cal.transform_batch(scores), outcomes, bins=10
        )
        self.assertLess(in_sample, 0.02)  # tautological, not evidence


if __name__ == "__main__":
    unittest.main()
