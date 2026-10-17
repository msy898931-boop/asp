"""T03 — deterministic logistic baseline runner.

Fits the logistic baseline on the development partition, measures it on
development and validation, and (only when explicitly asked) on OOS. OOS is
touched once, at the end, and never tuned on.

This is a RESEARCH result, not a published probability:
  * RULE C — the probabilities below are uncalibrated and unpublished. The
    calibration surface is measured (Brier/ECE/coverage) but the calibration
    audit (T11) has not run.
  * RULE B — cost tiers are not applied here (no trade simulation in T03); this
    is a probability-quality baseline only. Cost-aware evaluation is downstream.
  * RULE E — every report is a plain immutable record; nothing is overwritten.

No third-party dependencies. Fully deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from src.models.calibration import CalibrationReport, calibration_report
from src.models.dataset import (
    LabeledExample,
    assert_partitions_separated,
    feature_columns,
    to_matrix,
)
from src.models.logistic import LogisticModel, accuracy, fit_logistic
from src.models.splits import SplitError


@dataclass(frozen=True)
class PartitionMetrics:
    name: str
    n: int
    accuracy: float
    calibration: CalibrationReport
    # Optional raw material for the T27 publication surface (reliability table,
    # tier coverage, LONG/SHORT). Defaulted so existing constructors stay valid;
    # `_metrics` always populates them. Research-only, never a wire field.
    probabilities: Tuple[float, ...] = ()
    outcomes: Tuple[int, ...] = ()

    def summary(self) -> str:
        return (
            f"{self.name}: n={self.n} acc={self.accuracy:.4f} "
            f"brier={self.calibration.brier:.4f} "
            f"skill={self.calibration.brier_skill:+.4f} "
            f"ece={self.calibration.ece:.4f} "
            f"meets_target={self.calibration.meets_target()}"
        )


@dataclass(frozen=True)
class BaselineReport:
    model: LogisticModel
    columns: Tuple[str, ...]
    development: PartitionMetrics
    validation: PartitionMetrics
    oos: Optional[PartitionMetrics] = None

    def summary(self) -> str:
        lines = [self.development.summary(), self.validation.summary()]
        if self.oos is not None:
            lines.append(self.oos.summary())
        return "\n".join(lines)


def _evaluate(
    name: str, model: LogisticModel, examples: Sequence[LabeledExample], columns
) -> PartitionMetrics:
    if not examples:
        raise SplitError(f"partition '{name}' is empty")
    x, y = to_matrix(examples, columns)
    probs = model.predict_proba_batch(x)
    preds = [1 if p >= 0.5 else 0 for p in probs]
    return PartitionMetrics(
        name=name,
        n=len(examples),
        accuracy=accuracy(y, preds),
        calibration=calibration_report(probs, y),
        probabilities=tuple(probs),
        outcomes=tuple(int(v) for v in y),
    )


def run_baseline(
    development: Sequence[LabeledExample],
    validation: Sequence[LabeledExample],
    oos: Sequence[LabeledExample] | None = None,
    l2: float = 1e-6,
    max_iter: int = 100,
) -> BaselineReport:
    """Fit on development, evaluate on development + validation (+ OOS if given).

    Columns are fixed from the development partition, so validation/OOS cannot
    influence the feature set. Any mismatch is a hard error.
    """
    if not development:
        raise SplitError("development partition is empty")
    # Same structural guard as the calibrated runner: reject overlap or inversion
    # so a partition cannot be trained and scored on itself.
    assert_partitions_separated(
        (("development", development), ("validation", validation), ("oos", oos))
    )
    columns = feature_columns(development)
    # Enforce identical columns across partitions.
    for name, part in (("validation", validation), ("oos", oos)):
        if part and feature_columns(part) != columns:
            raise SplitError(f"partition '{name}' has different feature columns")

    x_dev, y_dev = to_matrix(development, columns)
    model = fit_logistic(x_dev, y_dev, l2=l2, max_iter=max_iter)

    dev_metrics = _evaluate("development", model, development, columns)
    val_metrics = _evaluate("validation", model, validation, columns)
    oos_metrics = _evaluate("oos", model, oos, columns) if oos else None
    return BaselineReport(
        model=model,
        columns=columns,
        development=dev_metrics,
        validation=val_metrics,
        oos=oos_metrics,
    )
