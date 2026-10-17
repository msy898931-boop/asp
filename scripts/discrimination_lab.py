"""Discrimination laboratory: is there ANY ranking signal, drift aside?

Brier skill and accuracy both depend on a threshold and are inflated when the
market drifts (see horizon_diagnostic.py). AUC — the probability that a randomly
chosen up-move is ranked above a randomly chosen down-move — does not: it is
threshold-free and base-rate-free. If AUC is 0.50, the model's probabilities
carry no information about direction at all, however the score is sliced.

This fits the usual pipeline (IRLS logistic, Platt calibration, year split) and
reports, per horizon:

  * AUC on the OOS rows, and on a stride-h subset (disjoint labels);
  * the "excess skill" — model Brier minus base-rate Brier. A negative number
    means the model is worse than just predicting 2025 gold's drift;
  * a permutation p-value: OOS labels are shuffled (the fit is unchanged, only
    scoring) to build the null AUC distribution. p is the share of shuffles
    whose AUC is at least as far from 0.5 as the real one.

A non-significant AUC agrees with every other lab here: no exploitable edge.

Usage:
    python3 scripts/discrimination_lab.py [--horizons 1,4,8,16] [--l2 0.05]
                                          [--permutations 200]
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models.calibration import brier_score
from src.models.calibrators import fit_calibrator
from src.models.dataset import (
    build_labeled_examples,
    feature_columns,
    to_matrix,
)
from src.models.realdata import _logistic_factory, load_corpus
from src.models.splits import (
    DEVELOPMENT_YEARS,
    OOS_YEARS,
    VALIDATION_YEARS,
    Sample,
    chronological_split,
)

CORPUS = "research/features_real/corpus/real_corpus_2021_2025.json.gz"
BAR_MINUTES = 15


def auc(probabilities, outcomes) -> float:
    """Rank-based AUC via the Mann–Whitney statistic, with ties at 0.5."""
    pairs = sorted(zip(probabilities, outcomes))
    n = len(pairs)
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    pos = sum(1 for _, y in pairs if y == 1)
    neg = n - pos
    if pos == 0 or neg == 0:
        return 0.5
    rank_sum = sum(r for r, (_, y) in zip(ranks, pairs) if y == 1)
    return (rank_sum - pos * (pos + 1) / 2.0) / (pos * neg)


def permutation_p(probabilities, outcomes, observed, rng, rounds) -> float:
    extreme = 0
    dev = abs(observed - 0.5)
    for _ in range(rounds):
        shuffled = outcomes[:]
        rng.shuffle(shuffled)
        if abs(auc(probabilities, shuffled) - 0.5) >= dev:
            extreme += 1
    return (extreme + 1) / (rounds + 1)


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
    parser = argparse.ArgumentParser(description="discrimination laboratory")
    parser.add_argument("--horizons", default="1,4,8,16")
    parser.add_argument("--l2", type=float, default=0.05)
    parser.add_argument("--permutations", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20260101)
    args = parser.parse_args(argv)
    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]
    rng = random.Random(args.seed)

    print(f"loading {CORPUS} ...")
    feature_sets, closes = load_corpus(CORPUS)
    kept = [(fs, c) for fs, c in zip(feature_sets, closes) if fs.valid]
    feature_sets = [fs for fs, _ in kept]
    closes = [c for _, c in kept]

    header = (
        f"{'h':>3s} {'wall':>6s} {'n_oos':>7s} {'auc':>7s} {'aucStride':>10s} "
        f"{'excessSkill':>12s} {'p':>7s}"
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
        a = auc(probs, y)
        a_stride = auc(probs[::h], y[::h])
        base = sum(y) / n
        excess = brier_score(probs, y) - brier_score([base] * n, y)
        p = permutation_p(probs, y, a, rng, args.permutations)

        print(
            f"{h:3d} {h * BAR_MINUTES:>4d}m {n:7d} {a:7.4f} {a_stride:10.4f} "
            f"{excess:+12.4f} {p:7.3f}"
        )

    print(
        "\nread: AUC 0.50 = the probabilities rank up- and down-moves no better "
        "than a coin. 'aucStride' repeats that on disjoint (stride-h) labels. "
        "'excessSkill' = model Brier minus base-rate Brier; POSITIVE means the "
        "model is WORSE than just predicting the drift. 'p' is a two-sided "
        "permutation p-value for AUC != 0.5. Note the multiple-comparison caveat: "
        "four horizons are tested here and six labs exist, so a lone p~0.01 is "
        "exploratory, not confirmatory."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
