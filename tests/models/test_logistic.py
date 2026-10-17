"""Deterministic tests for the logistic baseline (T03)."""

from __future__ import annotations

import math
import unittest

from src.models.logistic import (
    Standardizer,
    accuracy,
    fit_logistic,
    sigmoid,
)
from src.models.splits import SplitError


class SigmoidTest(unittest.TestCase):
    def test_sigmoid_at_zero(self):
        self.assertAlmostEqual(sigmoid(0.0), 0.5)

    def test_sigmoid_saturates(self):
        self.assertAlmostEqual(sigmoid(100.0), 1.0, places=12)
        self.assertAlmostEqual(sigmoid(-100.0), 0.0, places=12)

    def test_sigmoid_stable_for_large_negative(self):
        self.assertTrue(math.isfinite(sigmoid(-1000.0)))


class StandardizerTest(unittest.TestCase):
    def test_standardize_centres_and_scales(self):
        rows = [[1.0, 10.0], [3.0, 20.0], [5.0, 30.0]]
        s = Standardizer.fit(rows)
        out = s.transform(rows)
        for j in range(2):
            col = [r[j] for r in out]
            self.assertAlmostEqual(sum(col) / len(col), 0.0, places=10)

    def test_zero_variance_column_maps_to_zero(self):
        rows = [[1.0, 5.0], [2.0, 5.0], [3.0, 5.0]]
        s = Standardizer.fit(rows)
        out = s.transform(rows)
        self.assertTrue(all(abs(r[1]) < 1e-12 for r in out))

    def test_empty_matrix_rejected(self):
        with self.assertRaises(SplitError):
            Standardizer.fit([])

    def test_ragged_matrix_rejected(self):
        with self.assertRaises(SplitError):
            Standardizer.fit([[1.0], [1.0, 2.0]])


class FitTest(unittest.TestCase):
    def test_separable_data_recovers_ordering(self):
        # x < 0 -> 0, x > 0 -> 1
        x = [[-3.0], [-2.0], [-1.0], [1.0], [2.0], [3.0]]
        y = [0, 0, 0, 1, 1, 1]
        model = fit_logistic(x, y)
        self.assertLess(model.predict_proba([-2.0]), 0.5)
        self.assertGreater(model.predict_proba([2.0]), 0.5)

    def test_accuracy_on_separable(self):
        x = [[-3.0], [-2.0], [-1.0], [1.0], [2.0], [3.0]]
        y = [0, 0, 0, 1, 1, 1]
        model = fit_logistic(x, y)
        preds = [model.predict(row) for row in x]
        self.assertEqual(accuracy(y, preds), 1.0)

    def test_fit_is_deterministic(self):
        x = [[i * 0.1, (i % 3) * 0.5] for i in range(40)]
        y = [1 if (i % 2 == 0) else 0 for i in range(40)]
        a = fit_logistic(x, y)
        b = fit_logistic(x, y)
        self.assertEqual(a.weights, b.weights)
        self.assertEqual(a.intercept, b.intercept)
        self.assertEqual(a.iterations, b.iterations)

    def test_length_mismatch_rejected(self):
        with self.assertRaises(SplitError):
            fit_logistic([[1.0], [2.0]], [1])

    def test_non_binary_label_rejected(self):
        with self.assertRaises(SplitError):
            fit_logistic([[1.0], [2.0]], [0, 2])

    def test_empty_matrix_rejected(self):
        with self.assertRaises(SplitError):
            fit_logistic([], [])

    def test_negative_l2_rejected(self):
        with self.assertRaises(SplitError):
            fit_logistic([[1.0], [2.0]], [0, 1], l2=-1.0)

    def test_collinear_columns_still_fit(self):
        # Two identical columns: ridge keeps the normal equations solvable.
        x = [[1.0, 1.0], [2.0, 2.0], [3.0, 3.0], [4.0, 4.0]]
        y = [0, 0, 1, 1]
        model = fit_logistic(x, y)
        self.assertTrue(all(math.isfinite(w) for w in model.weights))
        self.assertTrue(math.isfinite(model.intercept))

    def test_probabilities_in_range(self):
        x = [[float(i), float(i % 5)] for i in range(30)]
        y = [i % 2 for i in range(30)]
        model = fit_logistic(x, y)
        for row in x:
            p = model.predict_proba(row)
            self.assertGreaterEqual(p, 0.0)
            self.assertLessEqual(p, 1.0)


class AccuracyTest(unittest.TestCase):
    def test_perfect(self):
        self.assertEqual(accuracy([1, 0, 1], [1, 0, 1]), 1.0)

    def test_length_mismatch(self):
        with self.assertRaises(SplitError):
            accuracy([1], [1, 0])

    def test_empty(self):
        with self.assertRaises(SplitError):
            accuracy([], [])


if __name__ == "__main__":
    unittest.main()
