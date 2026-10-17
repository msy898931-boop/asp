"""Calibrators (T05) — deterministic, dependency-free.

`calibration.py` measures calibration (Brier, ECE, reliability, coverage).
This module *fits* a mapping from raw scores to calibrated probabilities, which
is the missing half: it turns a model's raw output into a probability the system
can stand behind.

Three deterministic calibrators:

  * Platt scaling   — logistic fit p = sigmoid(A*s + B) on the raw score.
  * Isotonic (PAVA) — monotone step function, distribution-free.
  * Histogram       — equal-width bins, per-bin empirical rate.

Leakage discipline (enforced by the caller, documented here): a calibrator MUST
be fit on a partition disjoint from the one it is evaluated on. The intended use
is fit-on-validation, evaluate-on-OOS. Fitting on the same rows you score will
look perfect and mean nothing. Nothing here is published (RULE C).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence, Tuple

from src.models.logistic import sigmoid
from src.models.splits import SplitError


def _validate(scores: Sequence[float], outcomes: Sequence[int]) -> None:
    if len(scores) != len(outcomes):
        raise SplitError(
            f"length mismatch: {len(scores)} scores vs {len(outcomes)} outcomes"
        )
    if not scores:
        raise SplitError("empty sample")
    for s in scores:
        if not math.isfinite(s):
            raise SplitError(f"score must be finite, got {s!r}")
    for y in outcomes:
        if y not in (0, 1):
            raise SplitError(f"outcome must be 0 or 1, got {y!r}")


@dataclass(frozen=True)
class PlattCalibrator:
    """p = sigmoid(A * score + B), fit by Newton with a small ridge.

    Deterministic: zero initialisation, fixed iteration cap, no randomness. The
    ridge keeps the fit finite on separable data, where the unregularised
    likelihood is unbounded.
    """

    a: float
    b: float
    iterations: int
    converged: bool

    @staticmethod
    def fit(
        scores: Sequence[float],
        outcomes: Sequence[int],
        l2: float = 1e-3,
        max_iter: int = 100,
        tol: float = 1e-12,
    ) -> "PlattCalibrator":
        _validate(scores, outcomes)
        if l2 < 0.0:
            raise SplitError("l2 must be >= 0")
        a, b = 0.0, 0.0
        converged = False
        iterations = 0
        for it in range(max_iter):
            iterations = it + 1
            g_a = g_b = 0.0
            h_aa = h_ab = h_bb = 0.0
            for s, y in zip(scores, outcomes):
                p = sigmoid(a * s + b)
                w = max(p * (1.0 - p), 1e-12)
                r = y - p
                g_a += r * s
                g_b += r
                h_aa += w * s * s
                h_ab += w * s
                h_bb += w
            g_a -= l2 * a
            g_b -= l2 * b
            h_aa += l2
            h_bb += l2
            det = h_aa * h_bb - h_ab * h_ab
            if abs(det) < 1e-15:
                break
            da = (h_bb * g_a - h_ab * g_b) / det
            db = (h_aa * g_b - h_ab * g_a) / det
            a += da
            b += db
            if max(abs(da), abs(db)) < tol:
                converged = True
                break
        return PlattCalibrator(a=a, b=b, iterations=iterations, converged=converged)

    def transform(self, score: float) -> float:
        return sigmoid(self.a * score + self.b)

    def transform_batch(self, scores: Sequence[float]) -> List[float]:
        return [self.transform(s) for s in scores]


@dataclass(frozen=True)
class IsotonicCalibrator:
    """Monotone step function fit by Pool-Adjacent-Violators (PAVA).

    Distribution-free and deterministic. Ties in the score are pooled before the
    monotone fit so the result does not depend on input order. Prediction clamps
    to the fitted range.
    """

    thresholds: Tuple[float, ...]   # increasing score breakpoints
    values: Tuple[float, ...]       # calibrated probability at/above each breakpoint
    lower: float                    # probability for scores below the first breakpoint
    upper: float                    # probability for scores above the last breakpoint

    @staticmethod
    def fit(scores: Sequence[float], outcomes: Sequence[int]) -> "IsotonicCalibrator":
        _validate(scores, outcomes)
        pairs = sorted(zip(scores, outcomes), key=lambda t: t[0])
        # Pool exact score ties so order cannot change the fit.
        pooled: List[Tuple[float, int, int]] = []  # (score, positives, count)
        for s, y in pairs:
            if pooled and pooled[-1][0] == s:
                ps, py, pc = pooled[-1]
                pooled[-1] = (ps, py + y, pc + 1)
            else:
                pooled.append((s, y, 1))

        # PAVA on block means.
        blocks: List[List[float]] = []  # [score, positives, count, mean]
        for s, pos, count in pooled:
            blocks.append([s, float(pos), float(count), pos / count])
            while len(blocks) >= 2 and blocks[-2][3] > blocks[-1][3]:
                right = blocks.pop()
                left = blocks.pop()
                total_count = left[2] + right[2]
                merged_mean = (left[1] + right[1]) / total_count
                blocks.append([left[0], left[1] + right[1], total_count, merged_mean])

        thresholds = tuple(b[0] for b in blocks)
        values = tuple(b[3] for b in blocks)
        return IsotonicCalibrator(
            thresholds=thresholds,
            values=values,
            lower=values[0],
            upper=values[-1],
        )

    def transform(self, score: float) -> float:
        if score <= self.thresholds[0]:
            return self.lower
        if score >= self.thresholds[-1]:
            return self.upper
        lo, hi = 0, len(self.thresholds) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.thresholds[mid] <= score:
                lo = mid
            else:
                hi = mid - 1
        return self.values[lo]

    def transform_batch(self, scores: Sequence[float]) -> List[float]:
        return [self.transform(s) for s in scores]


@dataclass(frozen=True)
class HistogramCalibrator:
    """Equal-width binning with the per-bin empirical positive rate.

    Simple, interpretable, and deterministic. Empty bins are filled by carrying
    the nearest populated bin's rate (never by inventing a value).
    """

    lower: float
    upper: float
    bins: int
    rates: Tuple[float, ...]
    populated: Tuple[bool, ...]

    @staticmethod
    def fit(
        scores: Sequence[float], outcomes: Sequence[int], bins: int = 10
    ) -> "HistogramCalibrator":
        _validate(scores, outcomes)
        if bins <= 0:
            raise SplitError("bins must be positive")
        lo = min(scores)
        hi = max(scores)
        if hi == lo:
            hi = lo + 1.0  # degenerate range: one effective bin
        width = (hi - lo) / bins
        pos = [0] * bins
        count = [0] * bins
        for s, y in zip(scores, outcomes):
            idx = min(int((s - lo) / width), bins - 1)
            pos[idx] += y
            count[idx] += 1
        rates: List[float] = []
        populated: List[bool] = []
        for i in range(bins):
            if count[i] == 0:
                rates.append(0.0)
                populated.append(False)
            else:
                rates.append(pos[i] / count[i])
                populated.append(True)
        # Fill empty bins from the nearest populated neighbour.
        for i in range(bins):
            if populated[i]:
                continue
            left = next((j for j in range(i - 1, -1, -1) if populated[j]), None)
            right = next((j for j in range(i + 1, bins) if populated[j]), None)
            if left is None and right is None:
                rates[i] = 0.5
            elif left is None:
                assert right is not None
                rates[i] = rates[right]
            elif right is None:
                assert left is not None
                rates[i] = rates[left]
            else:
                rates[i] = rates[left] if (i - left) <= (right - i) else rates[right]
        return HistogramCalibrator(
            lower=lo,
            upper=hi,
            bins=bins,
            rates=tuple(rates),
            populated=tuple(populated),
        )

    def transform(self, score: float) -> float:
        if score <= self.lower:
            return self.rates[0]
        if score >= self.upper:
            return self.rates[-1]
        width = (self.upper - self.lower) / self.bins
        idx = min(int((score - self.lower) / width), self.bins - 1)
        return self.rates[idx]

    def transform_batch(self, scores: Sequence[float]) -> List[float]:
        return [self.transform(s) for s in scores]


def fit_calibrator(method: str, scores, outcomes):
    """Factory. `method` in {platt, isotonic, histogram}. Deterministic."""
    if method == "platt":
        return PlattCalibrator.fit(scores, outcomes)
    if method == "isotonic":
        return IsotonicCalibrator.fit(scores, outcomes)
    if method == "histogram":
        return HistogramCalibrator.fit(scores, outcomes)
    raise SplitError(f"unknown calibration method: {method!r}")
