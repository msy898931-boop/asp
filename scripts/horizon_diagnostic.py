"""Horizon diagnostic: is the longer-horizon "skill" real, or drift + overlap?

horizon_lab.py shows OOS Brier skill rising monotonically with the forecast
horizon (+0.0012 at 15m, +0.0085 at 4h). Before any edge is claimed, two traps
must be ruled out:

1. CLIMATOLOGY. Brier skill uses 0.25 (a constant 0.5 forecast) as the no-skill
   reference. But 2025 gold trended, so "just predict the base rate" already
   beats 0.5. The honest reference is the *base-rate* forecast. If the model's
   skill does not exceed the base rate's, it learned the average, not the market.

2. OVERLAP. At horizon h, consecutive rows' labels share h-1 of h bars, so the
   rows are not independent and the effective sample is ~n/h. Scoring only every
   h-th row (stride = h) makes labels disjoint and the estimate honest.

This reports, per horizon: OOS base rate, model accuracy, model skill, the
base-rate skill, the accuracy edge over the majority class, and the
non-overlapping stride skill. No number here is a forecast; it is a diagnosis.

Usage:
    python3 scripts/horizon_diagnostic.py [--horizons 1,4,8,16] [--l2 0.05]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models.calibrators import fit_calibrator  # noqa: E402
from src.models.calibration import brier_skill_score  # noqa: E402
from src.models.dataset import (  # noqa: E402
    build_labeled_examples,
    feature_columns,
    to_matrix,
)
from src.models.realdata import _logistic_factory, load_corpus  # noqa: E402
from src.models.splits import (  # noqa: E402
    DEVELOPMENT_YEARS,
    OOS_YEARS,
    VALIDATION_YEARS,
    Sample,
    chronological_split,
)

CORPUS = "research/features_real/corpus/real_corpus_2021_2025.json.gz"
BAR_MINUTES = 15


def fit_predict(dev, val, oos, l2: float):
    factory = _logistic_factory(l2)
    columns = feature_columns(dev)
    model = factory(*to_matrix(dev, columns))
    xv, yv = to_matrix(val, columns)
    calibrator = fit_calibrator(
        "platt", [model.decision_function(r) for r in xv], yv
    )
    xt, yt = to_matrix(oos, columns)
    probs = calibrator.transform_batch([model.decision_function(r) for r in xt])
    return probs, yt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="horizon diagnostic")
    parser.add_argument("--horizons", default="1,4,8,16")
    parser.add_argument("--l2", type=float, default=0.05)
    args = parser.parse_args(argv)
    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]

    print(f"loading {CORPUS} ...")
    feature_sets, closes = load_corpus(CORPUS)
    kept = [(fs, c) for fs, c in zip(feature_sets, closes) if fs.valid]
    feature_sets = [fs for fs, _ in kept]
    closes = [c for _, c in kept]

    header = (
        f"{'h':>3s} {'wall':>6s} {'n_oos':>7s} {'base':>6s} {'acc':>7s} "
        f"{'skill':>9s} {'baseSkl':>9s} {'edge':>7s} {'strideSkl':>10s}"
    )
    print(header)
    print("-" * len(header))

    for h in horizons:
        examples = build_labeled_examples(feature_sets, closes, horizon=h)
        samples = [
            Sample(datetime.fromtimestamp(e.timestamp, tz=timezone.utc), e)
            for e in examples
        ]
        split = chronological_split(
            samples, DEVELOPMENT_YEARS, VALIDATION_YEARS, OOS_YEARS
        )
        dev = [s.payload for s in split.development]
        val = [s.payload for s in split.validation]
        oos = [s.payload for s in split.oos]

        probs, y = fit_predict(dev, val, oos, args.l2)
        n = len(y)
        base = sum(y) / n
        acc = sum(1 for p, t in zip(probs, y) if (p >= 0.5) == bool(t)) / n
        skill = brier_skill_score(probs, y)
        base_skill = brier_skill_score([base] * n, y)
        edge = acc - max(base, 1 - base)

        stride_skill = brier_skill_score(probs[::h], y[::h])

        print(
            f"{h:3d} {h * BAR_MINUTES:>4d}m {n:7d} {base:6.3f} {acc:7.4f} "
            f"{skill:+9.4f} {base_skill:+9.4f} {edge:+7.4f} {stride_skill:+10.4f}"
        )

    print(
        "\nread: 'base' = share of OOS labels that are 1 (the drift). "
        "'baseSkl' = skill of always predicting that base rate. "
        "'edge' = accuracy above the majority class. "
        "'strideSkl' = skill on a stride-h subset with disjoint labels.\n"
        "An edge is credible only if acc clearly beats base AND strideSkl "
        "stays positive. Otherwise it is drift plus overlap."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
