"""Nonlinearity laboratory: is the missing signal a linear-model limitation?

The shipped model is a linear IRLS logistic regression. Its probability is
well-calibrated but discriminates no better than a coin flip (OOS Brier skill
~+0.0012). A linear model can only find a hyperplane; if the informative
structure in the 185 OHLC features is nonlinear (thresholds, interactions
between timeframes), a tree ensemble would recover skill the logistic cannot.

This tests that hypothesis with a gradient-boosted tree ensemble on the SAME
split (dev 2021-22 / val 2023-24 / oos 2025) and the SAME Platt calibration, so
the OOS Brier skill is directly comparable to the published baseline. A second
arm adds the calendar-time features, since trees consume interactions well.

Research only: sklearn is used here, never in the shipped harness (which stays
stdlib/hermetic). A positive result justifies porting to the stdlib booster.

Measured (same split, Platt-calibrated on val, scored on 2025):

    model                       feat   brier    skill     ece      acc
    linear logistic (baseline)  185   0.2497  +0.0012  0.0012   0.5172
    GBT (HistGradientBoosting)  185   0.2498  +0.0009  0.0128   0.5151
    GBT + time of day           192   0.2499  +0.0006  0.0133   0.5108

Result: a tree ensemble does NOT beat the linear logistic. Model class is not
the limiting factor, and neither is calendar time. Both remaining explanations
for the coin-flip score — (a) linear-only capacity, (b) missing time signal —
are now tested and refuted. The 185 OHLC shape features carry no exploitable
M15-forward XAUUSD direction signal under either hypothesis. The next lever is
new information (order flow, volume, cross-asset such as DXY/real yields), not
a new model on the same features.

Usage:
    python3 scripts/nonlinearity_lab.py [--corpus FILE]
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from typing import List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
from sklearn.ensemble import HistGradientBoostingClassifier  # noqa: E402

from scripts.feature_lab import time_features  # noqa: E402
from src.models.calibrators import fit_calibrator  # noqa: E402
from src.models.calibration import calibration_report  # noqa: E402
from src.models.dataset import feature_columns, to_matrix  # noqa: E402
from src.models.realdata import load_corpus  # noqa: E402


def _logit(p: np.ndarray) -> List[float]:
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return list(np.log(p / (1 - p)))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="nonlinearity laboratory")
    parser.add_argument(
        "--corpus",
        default="research/features_real/corpus/real_corpus_2021_2025.json.gz",
    )
    parser.add_argument("--seed", type=int, default=20260101)
    args = parser.parse_args(argv)

    print(f"loading {args.corpus} ...")
    feature_sets, closes = load_corpus(args.corpus)
    kept = [(fs, c) for fs, c in zip(feature_sets, closes) if fs.valid]
    feature_sets = [fs for fs, _ in kept]
    closes = [c for _, c in kept]

    # Reuse the lab's labeled-example builder for identical rows/labels.
    from scripts.feature_lab import build_labeled_examples

    base = build_labeled_examples(feature_sets, closes, horizon=1)

    from datetime import datetime, timezone

    stamps = [e.timestamp for e in base]
    years = [datetime.fromtimestamp(t, tz=timezone.utc).year for t in stamps]
    idx = {
        "dev": [i for i, y in enumerate(years) if y in (2021, 2022)],
        "val": [i for i, y in enumerate(years) if y in (2023, 2024)],
        "oos": [i for i, y in enumerate(years) if y == 2025],
    }
    for k, v in idx.items():
        print(f"  {k}: {len(v)}")

    def evaluate(extra: List[dict], label: str) -> None:
        rows = []
        for e, add in zip(base, extra):
            r = dict(e.features)
            r.update(add)
            rows.append(r)
        columns = sorted(rows[0])
        X = np.array([[r[c] for c in columns] for r in rows], dtype=np.float64)
        y = np.array([e.label for e in base], dtype=np.int64)

        model = HistGradientBoostingClassifier(
            max_iter=300,
            learning_rate=0.05,
            max_leaf_nodes=31,
            l2_regularization=1.0,
            random_state=args.seed,
        )
        model.fit(X[idx["dev"]], y[idx["dev"]])

        val_scores = _logit(model.predict_proba(X[idx["val"]])[:, 1])
        cal = fit_calibrator("platt", val_scores, list(y[idx["val"]]))
        oos_scores = _logit(model.predict_proba(X[idx["oos"]])[:, 1])
        probs = cal.transform_batch(oos_scores)
        rep = calibration_report(probs, list(y[idx["oos"]]))
        acc = sum(1 for p, t in zip(probs, y[idx["oos"]]) if (p >= 0.5) == bool(t)) / len(
            idx["oos"]
        )
        print(
            f"{label:34s} feat={len(columns):3d} brier={rep.brier:.4f} "
            f"skill={rep.brier_skill:+.4f} ece={rep.ece:.4f} acc={acc:.4f}"
        )

    print("\nGBT (HistGradientBoosting), Platt-calibrated on val, scored on 2025:\n")
    evaluate([{} for _ in base], "GBT (185 OHLC)")
    evaluate([time_features(e.timestamp) for e in base], "GBT + time of day")
    print(
        "\nReference — linear logistic baseline: brier 0.2497 skill +0.0012 "
        "ece 0.0012 acc 0.5172."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
