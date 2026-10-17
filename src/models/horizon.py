"""T15 — cost-aware direction labels and horizon comparison.

Implements §1 of `docs/architecture/DECISION_MODEL.md`: a three-class label
(UP / DOWN / FLAT) where FLAT is a cost-aware dead-band, not a free parameter,
and an empirical comparison of candidate horizons by honest calibration.

Deterministic, pure stdlib. This module measures; it publishes nothing (RULE C).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

from src.models.calibration import calibration_report
from src.models.dataset import (
    LabeledExample,
    assert_partitions_separated,
    feature_columns,
    to_matrix,
)
from src.models.levels import tier_by_name
from src.models.logistic import fit_logistic
from src.models.splits import SplitError

FLAT = "FLAT"
UP = "UP"
DOWN = "DOWN"


def direction_label(
    closes: Sequence[float], index: int, horizon: int, theta: float
) -> str:
    """Three-class label at `index` for `horizon` bars ahead with dead-band theta.

    UP   if close(i+H) - close(i) >  theta
    DOWN if close(i)   - close(i+H) >  theta
    FLAT otherwise

    theta must be the cost-aware dead-band (RULE B), not a free parameter.
    """
    if horizon < 1:
        raise SplitError("horizon must be >= 1")
    if theta < 0.0:
        raise SplitError("theta must be >= 0")
    future = index + horizon
    if index < 0 or future >= len(closes):
        raise SplitError(
            f"index {index} with horizon {horizon} reaches past the series"
        )
    delta = closes[future] - closes[index]
    if delta > theta:
        return UP
    if -delta > theta:
        return DOWN
    return FLAT


def label_distribution(labels: Sequence[str]) -> dict:
    total = len(labels)
    if total == 0:
        raise SplitError("no labels")
    counts = {UP: 0, DOWN: 0, FLAT: 0}
    for label in labels:
        if label not in counts:
            raise SplitError(f"unknown label {label!r}")
        counts[label] += 1
    return {
        name: {"count": counts[name], "share": counts[name] / total}
        for name in (UP, DOWN, FLAT)
    }


@dataclass(frozen=True)
class HorizonResult:
    horizon: int
    theta: float
    distribution: dict
    n: int
    brier: float
    ece: float
    brier_skill: float
    meets_target: bool
    # RULE C gate as stated in MISSION.md §8 / DECISION_MODEL.md §1.2.
    label: str  # "probability" | "score"


def _binary_examples(
    closes: Sequence[float],
    features: Sequence,
    horizon: int,
    theta: float,
    positive: str,
) -> List[LabeledExample]:
    """Build binary examples where `positive` direction is 1, the opposite is 0,
    and FLAT rows are dropped."""
    examples: List[LabeledExample] = []
    for i in range(len(closes) - horizon):
        label = direction_label(closes, i, horizon, theta)
        if label == FLAT:
            continue
        examples.append(
            LabeledExample(
                timestamp=features[i].asOfBarOpenSec,
                features=features[i].as_flat_row(),
                label=1 if label == positive else 0,
                label_timestamp=features[i + horizon].asOfBarOpenSec,
            )
        )
    return examples


def evaluate_horizon(
    features: Sequence,
    closes: Sequence[float],
    horizon: int,
    theta: float,
    model_factory=None,
) -> HorizonResult:
    """Fit + calibrate a direction model for one horizon; measure honest OOS.

    Direction here is a binary question: is the move UP (vs DOWN) once FLAT is
    excluded. Calibration is measured on the OOS partition with a Platt
    calibrator fitted on validation, using the same structural guard as T05.
    """
    if len(features) != len(closes):
        raise SplitError("features and closes must be aligned")
    examples = _binary_examples(closes, features, horizon, theta, positive=UP)
    if len(examples) < 50:
        raise SplitError(f"too few non-FLAT examples for horizon {horizon}")

    dist = label_distribution(
        [direction_label(closes, i, horizon, theta) for i in range(len(closes) - horizon)]
    )

    # Reuse the year-based purged split by timestamp.

    from src.models.dataset import purge_split

    split = purge_split(examples)
    dev, val, oos = split.development, split.validation, split.oos
    if not (dev and val and oos):
        raise SplitError("horizon split did not populate all three partitions")
    assert_partitions_separated(
        (("development", dev), ("validation", val), ("oos", oos))
    )

    columns = feature_columns(dev)
    for name, part in (("validation", val), ("oos", oos)):
        if feature_columns(part) != columns:
            raise SplitError(f"partition '{name}' has different feature columns")

    x_dev, y_dev = to_matrix(dev, columns)
    if model_factory is None:
        model = fit_logistic(x_dev, y_dev)
    else:
        model = model_factory(x_dev, y_dev)

    # Platt calibrator fitted on validation raw scores.
    from src.models.calibrators import fit_calibrator

    x_val, y_val = to_matrix(val, columns)
    raw_val = [model.decision_function(row) for row in x_val]
    calibrator = fit_calibrator("platt", raw_val, y_val)

    x_oos, y_oos = to_matrix(oos, columns)
    raw_oos = [model.decision_function(row) for row in x_oos]
    probs = calibrator.transform_batch(raw_oos)
    report = calibration_report(probs, y_oos)

    label = "probability" if report.meets_target() else "score"
    return HorizonResult(
        horizon=horizon,
        theta=theta,
        distribution=dist,
        n=len(oos),
        brier=report.brier,
        ece=report.ece,
        brier_skill=report.brier_skill,
        meets_target=report.meets_target(),
        label=label,
    )


def compare_horizons(
    features: Sequence,
    closes: Sequence[float],
    horizons: Sequence[int] = (1, 4, 16),
    cost_tier: str = "conservative",
    model_factory=None,
) -> List[HorizonResult]:
    """Calibrate each candidate horizon under one cost tier's dead-band.

    `horizons` are in bars of the decision timeframe (1 = next bar, 4 ~ 1h on
    M15, 16 ~ 4h). The dead-band is the tier's round-trip cost for every horizon
    (RULE B), so the comparison is cost-honest.
    """
    theta = tier_by_name(cost_tier).dead_band()
    return [
        evaluate_horizon(features, closes, h, theta, model_factory=model_factory)
        for h in horizons
    ]
