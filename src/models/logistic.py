"""Deterministic logistic regression (T03) — pure stdlib, no sklearn/numpy.

Fitted by Newton-Raphson / IRLS with ridge regularisation. Deterministic by
construction: fixed zero initialisation, a fixed iteration count, no shuffling,
no randomness, and a stable (Gauss-Jordan) linear solve with partial pivoting.

The fitted model emits a *probability* (sigmoid of the linear score). Per RULE C
that probability is NOT published and is NOT calibrated — calibration is a
separate, audited step (T05/T11). This module is a research estimator.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence, Tuple

from src.models.splits import SplitError


def sigmoid(z: float) -> float:
    """Numerically stable logistic function."""
    if z >= 0.0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


@dataclass(frozen=True)
class Standardizer:
    """Column-wise standardisation. Zero-variance columns map to 0.0."""

    means: Tuple[float, ...]
    stds: Tuple[float, ...]

    @staticmethod
    def fit(rows: Sequence[Sequence[float]]) -> "Standardizer":
        if not rows:
            raise SplitError("cannot standardise an empty matrix")
        n = len(rows[0])
        for row in rows:
            if len(row) != n:
                raise SplitError("ragged matrix")
        means = []
        stds = []
        for j in range(n):
            column = [row[j] for row in rows]
            mean = sum(column) / len(column)
            var = sum((v - mean) ** 2 for v in column) / len(column)
            std = math.sqrt(var)
            means.append(mean)
            stds.append(std if std > 1e-12 else 1.0)
        return Standardizer(tuple(means), tuple(stds))

    def transform(self, rows: Sequence[Sequence[float]]) -> List[List[float]]:
        out = []
        for row in rows:
            if len(row) != len(self.means):
                raise SplitError("row width does not match the fitted standardizer")
            out.append(
                [(row[j] - self.means[j]) / self.stds[j] for j in range(len(self.means))]
            )
        return out


def _solve(matrix: List[List[float]], rhs: List[float]) -> List[float]:
    """Solve a small dense linear system by Gauss-Jordan with partial pivoting.

    Raises SplitError on a singular system rather than returning NaNs.
    """
    n = len(matrix)
    aug = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-12:
            raise SplitError("singular normal-equations matrix")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        pivot_value = aug[col][col]
        aug[col] = [v / pivot_value for v in aug[col]]
        for r in range(n):
            if r != col and aug[r][col] != 0.0:
                factor = aug[r][col]
                aug[r] = [a - factor * b for a, b in zip(aug[r], aug[col])]
    return [aug[i][n] for i in range(n)]


@dataclass(frozen=True)
class LogisticModel:
    """A fitted logistic model: standardizer + weights + intercept."""

    standardizer: Standardizer
    weights: Tuple[float, ...]
    intercept: float
    iterations: int
    converged: bool

    def decision_function(self, row: Sequence[float]) -> float:
        z = self.intercept
        for w, x in zip(self.weights, self.standardizer.transform([row])[0]):
            z += w * x
        return z

    def predict_proba(self, row: Sequence[float]) -> float:
        return sigmoid(self.decision_function(row))

    def predict_proba_batch(self, rows: Sequence[Sequence[float]]) -> List[float]:
        return [self.predict_proba(row) for row in rows]

    def predict(self, row: Sequence[float], threshold: float = 0.5) -> int:
        return 1 if self.predict_proba(row) >= threshold else 0


def fit_logistic(
    x: Sequence[Sequence[float]],
    y: Sequence[int],
    l2: float = 1e-6,
    max_iter: int = 100,
    tol: float = 1e-10,
) -> LogisticModel:
    """Fit a ridge-penalised logistic regression by IRLS.

    `l2` is a small ridge term that keeps the system solvable when a column is
    (near-)collinear. `max_iter`/`tol` give a deterministic stopping rule.
    """
    if not x:
        raise SplitError("cannot fit on an empty matrix")
    if len(x) != len(y):
        raise SplitError(f"length mismatch: {len(x)} rows vs {len(y)} labels")
    for label in y:
        if label not in (0, 1):
            raise SplitError(f"labels must be 0 or 1, got {label!r}")
    if l2 < 0.0:
        raise SplitError("l2 must be >= 0")

    standardizer = Standardizer.fit(x)
    xs = standardizer.transform(x)
    d = len(xs[0])

    # Design matrix with an intercept column.
    design = [[1.0] + row for row in xs]
    beta = [0.0] * (d + 1)

    converged = False
    iterations = 0
    for it in range(max_iter):
        iterations = it + 1
        # Gradient and Hessian of the penalised log-likelihood.
        grad = [0.0] * (d + 1)
        hess = [[0.0] * (d + 1) for _ in range(d + 1)]
        for row, label in zip(design, y):
            p = sigmoid(sum(b * v for b, v in zip(beta, row)))
            weight = max(p * (1.0 - p), 1e-12)
            residual = label - p
            for i in range(d + 1):
                grad[i] += residual * row[i]
                for j in range(d + 1):
                    hess[i][j] += weight * row[i] * row[j]
        # Ridge on all but the intercept.
        for i in range(1, d + 1):
            grad[i] -= l2 * beta[i]
            hess[i][i] += l2

        delta = _solve(hess, grad)
        beta = [b + step for b, step in zip(beta, delta)]
        if max(abs(step) for step in delta) < tol:
            converged = True
            break

    return LogisticModel(
        standardizer=standardizer,
        weights=tuple(beta[1:]),
        intercept=beta[0],
        iterations=iterations,
        converged=converged,
    )


def accuracy(y_true: Sequence[int], y_pred: Sequence[int]) -> float:
    if len(y_true) != len(y_pred):
        raise SplitError("length mismatch")
    if not y_true:
        raise SplitError("empty sample")
    hits = sum(1 for a, b in zip(y_true, y_pred) if a == b)
    return hits / len(y_true)
