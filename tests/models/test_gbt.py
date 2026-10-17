"""Deterministic tests for the T04 stdlib gradient-boosted trees."""

from __future__ import annotations

import unittest

from src.models.gbt import Tree, fit_gbt
from src.models.splits import SplitError


def separable(n=200):
    """x0 in [-3, 3] drives the label; x1 is noise."""
    x = []
    y = []
    for i in range(n):
        x0 = -3.0 + 6.0 * (i + 0.5) / n
        x1 = ((i * 7) % 11) / 11.0
        x.append([x0, x1])
        y.append(1 if x0 > 0.0 else 0)
    return x, y


class TreeTest(unittest.TestCase):
    def test_leaf_predicts_constant(self):
        tree = Tree.leaf(2.5)
        self.assertEqual(tree.predict([0.0, 0.0]), 2.5)

    def test_split_routes_on_threshold(self):
        # Build a one-split tree by hand: feature 0 <= 0.5 -> left leaf.
        from src.models.gbt import TreeNode

        nodes = (
            TreeNode(0, 0.5, 1, 2, 0.0),
            TreeNode(None, 0.0, -1, -1, -1.0),
            TreeNode(None, 0.0, -1, -1, 1.0),
        )
        tree = Tree(nodes)
        self.assertEqual(tree.predict([0.0]), -1.0)
        self.assertEqual(tree.predict([1.0]), 1.0)


class FitTest(unittest.TestCase):
    def test_learns_separable_signal(self):
        x, y = separable()
        model = fit_gbt(x, y, n_estimators=50, learning_rate=0.3, max_depth=2)
        preds = [model.predict(row) for row in x]
        acc = sum(1 for a, b in zip(y, preds) if a == b) / len(y)
        self.assertGreater(acc, 0.95)

    def test_outputs_are_probabilities(self):
        x, y = separable()
        model = fit_gbt(x, y, n_estimators=20)
        for p in model.predict_proba_batch(x):
            self.assertGreaterEqual(p, 0.0)
            self.assertLessEqual(p, 1.0)

    def test_deterministic(self):
        x, y = separable()
        a = fit_gbt(x, y, n_estimators=25)
        b = fit_gbt(x, y, n_estimators=25)
        self.assertEqual(a.base_score, b.base_score)
        self.assertEqual(a.learning_rate, b.learning_rate)
        self.assertEqual(len(a.trees), len(b.trees))
        for ta, tb in zip(a.trees, b.trees):
            self.assertEqual(ta.nodes, tb.nodes)

    def test_boosting_reduces_training_loss(self):
        x, y = separable()
        from src.models.logistic import sigmoid

        def loss(model):
            return sum(
                -(yy * _log(sigmoid(model.decision_function(xx)))
                  + (1 - yy) * _log(1.0 - sigmoid(model.decision_function(xx))))
                for xx, yy in zip(x, y)
            ) / len(y)

        weak = fit_gbt(x, y, n_estimators=1)
        strong = fit_gbt(x, y, n_estimators=40)
        self.assertLess(loss(strong), loss(weak))

    def test_zero_estimators_is_base_rate_only(self):
        x, y = separable()
        model = fit_gbt(x, y, n_estimators=0)
        self.assertEqual(len(model.trees), 0)
        # Constant prediction equal to the base rate (sigmoid of base_score).
        from src.models.logistic import sigmoid

        p = model.predict_proba(x[0])
        self.assertAlmostEqual(p, sigmoid(model.base_score), places=12)

    def test_max_depth_zero_is_a_stump(self):
        x, y = separable()
        model = fit_gbt(x, y, n_estimators=10, max_depth=0)
        for tree in model.trees:
            self.assertEqual(len(tree.nodes), 1)  # single leaf

    def test_deep_tree_predict_terminates(self):
        # Regression: sub-tree child indices must be rebased when flattened, or a
        # node can point at itself and predict() loops forever.
        x, y = separable()
        model = fit_gbt(x, y, n_estimators=5, max_depth=3)
        for row in x:
            self.assertTrue(0.0 <= model.predict_proba(row) <= 1.0)

    def test_internal_nodes_never_self_reference(self):
        x, y = separable()
        model = fit_gbt(x, y, n_estimators=5, max_depth=3)
        for tree in model.trees:
            for i, node in enumerate(tree.nodes):
                if node.feature is not None:
                    self.assertNotEqual(node.left, i)
                    self.assertNotEqual(node.right, i)
                    self.assertLess(node.left, len(tree.nodes))
                    self.assertLess(node.right, len(tree.nodes))

    def test_length_mismatch_rejected(self):
        with self.assertRaises(SplitError):
            fit_gbt([[0.0], [1.0]], [1])

    def test_empty_rejected(self):
        with self.assertRaises(SplitError):
            fit_gbt([], [])

    def test_bad_label_rejected(self):
        with self.assertRaises(SplitError):
            fit_gbt([[0.0], [1.0]], [0, 2])

    def test_negative_estimators_rejected(self):
        with self.assertRaises(SplitError):
            fit_gbt([[0.0], [1.0]], [0, 1], n_estimators=-1)

    def test_bad_learning_rate_rejected(self):
        with self.assertRaises(SplitError):
            fit_gbt([[0.0], [1.0]], [0, 1], learning_rate=0.0)


def _log(v):
    import math
    return math.log(max(v, 1e-15))


if __name__ == "__main__":
    unittest.main()
