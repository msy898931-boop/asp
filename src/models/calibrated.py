"""End-to-end calibrated runner (T05) — the mission's deliverable shape.

Flow, with the leakage discipline enforced structurally:

    development  -> fit the base model (logistic)
    validation   -> fit the CALIBRATOR on the base model's raw scores
    oos          -> evaluate the calibrated probabilities ONCE

The calibrator is never fit on the rows it is scored against. `run_calibrated`
enforces this structurally: it rejects partitions that are not pairwise disjoint
and chronologically ordered (see `assert_partitions_separated`), so overlapping
or inverted partitions raise `SplitError` instead of returning a tautological
near-zero error.

Everything here is uncalibrated-and-unpublished in the RULE C sense: it produces
the *measurement* of calibration, and the published-probability decision waits on
the T11 audit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

from src.models.baseline import PartitionMetrics
from src.models.calibration import calibration_report
from src.models.calibrators import fit_calibrator
from src.models.dataset import (
    LabeledExample,
    assert_partitions_separated,
    feature_columns,
    to_matrix,
)
from src.models.logistic import fit_logistic
from src.models.splits import SplitError


def _raw_scores(model, x) -> List[float]:
    return [model.decision_function(row) for row in x]


def _metrics(
    name: str, probabilities: Sequence[float], outcomes: Sequence[int]
) -> PartitionMetrics:
    preds = [1 if p >= 0.5 else 0 for p in probabilities]
    hits = sum(1 for a, b in zip(outcomes, preds) if a == b)
    return PartitionMetrics(
        name=name,
        n=len(probabilities),
        accuracy=hits / len(probabilities),
        calibration=calibration_report(probabilities, outcomes),
        probabilities=tuple(probabilities),
        outcomes=tuple(int(v) for v in outcomes),
    )


@dataclass(frozen=True)
class CalibratedReport:
    method: str
    columns: Tuple[str, ...]
    raw: dict          # name -> PartitionMetrics (uncalibrated probabilities)
    calibrated: dict   # name -> PartitionMetrics (calibrated probabilities)

    def summary(self) -> str:
        lines = [f"calibrator: {self.method}"]
        for name in ("development", "validation", "oos"):
            if name in self.raw:
                lines.append("  raw        " + self.raw[name].summary())
            if name in self.calibrated:
                lines.append("  calibrated " + self.calibrated[name].summary())
        return "\n".join(lines)


def run_calibrated(
    development: Sequence[LabeledExample],
    validation: Sequence[LabeledExample],
    oos: Sequence[LabeledExample] | None = None,
    method: str = "platt",
    model_factory=None,
) -> CalibratedReport:
    """Fit base model on development, calibrator on validation, evaluate all.

    `oos` is optional and, when present, is scored with the validation-fitted
    calibrator — never used to fit anything.

    `model_factory(x, y) -> model` selects the base estimator (default: the
    logistic model). T04 passes the stdlib GBT factory; the model must expose
    `decision_function(row)` and `predict_proba_batch(rows)`.
    """
    if not development:
        raise SplitError("development partition is empty")
    if not validation:
        raise SplitError("validation partition is required to fit the calibrator")

    # Structural leakage guard: reject overlap or chronological inversion before
    # any fitting happens, so a tautological near-zero error cannot be produced.
    assert_partitions_separated(
        (("development", development), ("validation", validation), ("oos", oos))
    )

    columns = feature_columns(development)
    for name, part in (("validation", validation), ("oos", oos)):
        if part and feature_columns(part) != columns:
            raise SplitError(f"partition '{name}' has different feature columns")

    x_dev, y_dev = to_matrix(development, columns)
    if model_factory is None:
        model = fit_logistic(x_dev, y_dev)
    else:
        model = model_factory(x_dev, y_dev)

    x_val, y_val = to_matrix(validation, columns)
    raw_val = _raw_scores(model, x_val)
    calibrator = fit_calibrator(method, raw_val, y_val)
    calibrated_val = calibrator.transform_batch(raw_val)

    # Development is reported RAW only: fitting a calibrator on development and
    # scoring development would be tautological, so no calibrated dev row exists.
    raw = {
        "development": _metrics("development", model.predict_proba_batch(x_dev), y_dev),
        "validation": _metrics("validation", model.predict_proba_batch(x_val), y_val),
    }
    calibrated = {
        "validation": _metrics("validation", calibrated_val, y_val),
    }

    if oos:
        x_oos, y_oos = to_matrix(oos, columns)
        raw_oos = _raw_scores(model, x_oos)
        raw["oos"] = _metrics("oos", model.predict_proba_batch(x_oos), y_oos)
        calibrated["oos"] = _metrics("oos", calibrator.transform_batch(raw_oos), y_oos)

    return CalibratedReport(
        method=method, columns=columns, raw=raw, calibrated=calibrated
    )
