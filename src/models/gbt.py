"""Deterministic gradient-boosted trees (T04) — pure stdlib, no xgboost/numpy.

The Lead delegated the T04 model choice (real XGBoost vs a stdlib booster). This
implements the stdlib option: a logistic-loss gradient-boosted tree ensemble
(XGBoost-style second-order boosting) with no third-party dependency, so the
harness stays hermetic and byte-for-byte reproducible.

Model: F(x) = intercept + sum_k lr * tree_k(x); probability = sigmoid(F(x)).

Determinism by construction:
  * every split threshold is a midpoint of two adjacent DISTINCT feature values
    drawn from a sorted list — no quantile sketch, no sampling;
  * candidate thresholds are scanned in ascending order and ties are broken by
    the lowest feature index (first, then best gain);
  * feature order is the column order, fixed by the caller;
  * no RNG, no hashing, no wall-clock, no unordered iteration.

The fitted model emits a *probability* (sigmoid of the boosted score). Per RULE C
that probability is NOT published and is NOT calibrated — calibration (T05) is a
separate, audited step (T11). This module is a research estimator.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from src.models.logistic import Standardizer, sigmoid
from src.models.splits import SplitError

_EPS = 1e-12


@dataclass(frozen=True)
class TreeNode:
    """A split node, or a leaf when `feature` is None."""

    feature: Optional[int]
    threshold: float
    left: int    # child node index; -1 for a leaf
    right: int   # child node index; -1 for a leaf
    value: float  # leaf output (raw score) when feature is None


@dataclass(frozen=True)
class Tree:
    nodes: Tuple[TreeNode, ...]

    @staticmethod
    def leaf(value: float) -> "Tree":
        return Tree((TreeNode(None, 0.0, -1, -1, value),))

    def predict(self, row: Sequence[float]) -> float:
        i = 0
        while True:
            node = self.nodes[i]
            if node.feature is None:
                return node.value
            i = node.left if row[node.feature] <= node.threshold else node.right


def _candidate_thresholds(column: Sequence[float]) -> List[float]:
    """Midpoints of adjacent distinct values, ascending. Fully deterministic."""
    values = sorted(set(column))
    return [(values[i] + values[i + 1]) / 2.0 for i in range(len(values) - 1)]


def _rebased(nodes: Sequence[TreeNode], offset: int) -> List[TreeNode]:
    """Shift a sub-tree's child indices into its position in a combined array."""
    out: List[TreeNode] = []
    for node in nodes:
        if node.feature is None:
            out.append(node)
        else:
            out.append(
                TreeNode(
                    node.feature,
                    node.threshold,
                    node.left + offset,
                    node.right + offset,
                    node.value,
                )
            )
    return out


def _leaf_value(g: float, h: float, l2: float, min_child_weight: float) -> float:
    denom = h + l2
    if denom < _EPS or h < min_child_weight:
        return 0.0
    return -g / denom


def _build_tree(
    x: Sequence[Sequence[float]],
    g: Sequence[float],
    h: Sequence[float],
    indices: Sequence[int],
    depth: int,
    max_depth: int,
    min_child_weight: float,
    reg_lambda: float,
    gamma: float,
) -> Tree:
    g_sum = sum(g[i] for i in indices)
    h_sum = sum(h[i] for i in indices)
    leaf = _leaf_value(g_sum, h_sum, reg_lambda, min_child_weight)

    if depth >= max_depth or len(indices) <= 1:
        return Tree.leaf(leaf)

    n_features = len(x[0])
    best_gain = gamma
    # Track the winning (feature, split position, threshold) only. Child index
    # lists are materialised ONCE after the scan, so the search stays O(n log n)
    # per feature instead of O(n^2).
    best: Optional[Tuple[int, int, float]] = None
    best_order: Optional[List[int]] = None
    total_g = g_sum
    total_h = h_sum

    for feature in range(n_features):
        column = [x[i][feature] for i in indices]
        order = sorted(range(len(indices)), key=lambda p: column[p])
        sorted_col = [column[p] for p in order]
        thresholds = _candidate_thresholds(sorted_col)
        if not thresholds:
            continue
        left_g = left_h = 0.0
        pos = 0
        for threshold in thresholds:
            while pos < len(order) and sorted_col[pos] <= threshold:
                idx = indices[order[pos]]
                left_g += g[idx]
                left_h += h[idx]
                pos += 1
            right_g = total_g - left_g
            right_h = total_h - left_h
            if left_h < min_child_weight or right_h < min_child_weight:
                continue
            gain = (
                0.5
                * (
                    left_g * left_g / (left_h + reg_lambda)
                    + right_g * right_g / (right_h + reg_lambda)
                    - total_g * total_g / (total_h + reg_lambda)
                )
                - gamma
            )
            if gain > best_gain + 1e-15:
                best_gain = gain
                best = (feature, pos, threshold)
                best_order = order

    if best is None:
        return Tree.leaf(leaf)

    feature, split_pos, threshold = best
    assert best_order is not None
    left_idx = [best_order[p] for p in range(split_pos)]
    right_idx = [best_order[p] for p in range(split_pos, len(best_order))]
    left_tree = _build_tree(
        x, g, h, left_idx, depth + 1, max_depth,
        min_child_weight, reg_lambda, gamma,
    )
    right_tree = _build_tree(
        x, g, h, right_idx, depth + 1, max_depth,
        min_child_weight, reg_lambda, gamma,
    )

    # Flatten children into a single node array (deterministic indexing). Child
    # indices inside each sub-tree are relative to that sub-tree, so they must be
    # rebased by the sub-tree's offset in the combined array — otherwise a node
    # can point at itself and prediction loops forever.
    left_base = 1
    right_base = 1 + len(left_tree.nodes)
    nodes: List[TreeNode] = [TreeNode(feature, threshold, left_base, right_base, 0.0)]
    nodes.extend(_rebased(left_tree.nodes, left_base))
    nodes.extend(_rebased(right_tree.nodes, right_base))
    return Tree(tuple(nodes))


@dataclass(frozen=True)
class GBTModel:
    """A fitted gradient-boosted tree ensemble."""

    standardizer: Standardizer
    trees: Tuple[Tree, ...]
    base_score: float
    learning_rate: float

    def decision_function(self, row: Sequence[float]) -> float:
        xs = self.standardizer.transform([row])[0]
        score = self.base_score
        for tree in self.trees:
            score += self.learning_rate * tree.predict(xs)
        return score

    def predict_proba(self, row: Sequence[float]) -> float:
        return sigmoid(self.decision_function(row))

    def predict_proba_batch(self, rows: Sequence[Sequence[float]]) -> List[float]:
        return [self.predict_proba(row) for row in rows]

    def predict(self, row: Sequence[float], threshold: float = 0.5) -> int:
        return 1 if self.predict_proba(row) >= threshold else 0


def fit_gbt(
    x: Sequence[Sequence[float]],
    y: Sequence[int],
    n_estimators: int = 100,
    learning_rate: float = 0.3,
    max_depth: int = 3,
    min_child_weight: float = 1.0,
    reg_lambda: float = 1.0,
    gamma: float = 0.0,
) -> GBTModel:
    """Fit a logistic-loss gradient-boosted tree ensemble.

    `n_estimators` boosting rounds; each round fits one regression tree to the
    second-order (gradient, hessian) of the logistic loss and adds it scaled by
    `learning_rate`. Deterministic: no sampling, no randomness.
    """
    if not x:
        raise SplitError("cannot fit on an empty matrix")
    if len(x) != len(y):
        raise SplitError(f"length mismatch: {len(x)} rows vs {len(y)} labels")
    for label in y:
        if label not in (0, 1):
            raise SplitError(f"labels must be 0 or 1, got {label!r}")
    if n_estimators < 0:
        raise SplitError("n_estimators must be >= 0")
    if max_depth < 0:
        raise SplitError("max_depth must be >= 0")
    if learning_rate <= 0.0:
        raise SplitError("learning_rate must be > 0")

    standardizer = Standardizer.fit(x)
    xs = standardizer.transform(x)

    n = len(xs)
    positives = sum(y)
    base_score = _logit((positives + 0.5) / (n + 1.0))

    trees: List[Tree] = []
    # Maintain the running raw score incrementally so each round is O(n), not
    # O(rounds * n) of full-ensemble re-scoring.
    scores = [base_score] * n
    for _ in range(n_estimators):
        probs = [sigmoid(s) for s in scores]
        g = [p - label for p, label in zip(probs, y)]
        h = [max(p * (1.0 - p), _EPS) for p in probs]
        tree = _build_tree(
            xs, g, h, list(range(n)), 0, max_depth,
            min_child_weight, reg_lambda, gamma,
        )
        trees.append(tree)
        for i, row in enumerate(xs):
            scores[i] += learning_rate * tree.predict(row)

    return GBTModel(
        standardizer=standardizer,
        trees=tuple(trees),
        base_score=base_score,
        learning_rate=learning_rate,
    )


def _logit(p: float) -> float:
    p = min(max(p, _EPS), 1.0 - _EPS)
    return math.log(p / (1.0 - p))
