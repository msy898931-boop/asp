"""Horizon laboratory: is one M15 bar simply the wrong forecast horizon?

The frozen target is a single-bar move: label = (close[i+1] > close[i]), i.e.
"will the next 15 minutes close higher?". Gold is known for momentum and
mean-reversion at *different* scales, so the coin-flip score could be a
horizon-selection artefact rather than an absence of signal.

This runs the identical pipeline (same features, same logistic, same Platt
calibration, same chronological year split) at several pre-declared horizons and
reports ALL of them. Reporting every horizon is the point: picking the best of
many and publishing only that is p-hacking, so the sweep is fixed up front and
the whole table is the result.

CAVEAT (honest): at horizon h the label reads the close h bars ahead. A row
sitting near a year boundary can therefore have a label drawn from the next
partition — a seam leak of at most h rows per boundary against ~40k training
rows. It is disclosed, not corrected, because correcting it would change the
target the published baseline was scored on and break comparability. At h=1 it
is the exact published setup.

Usage:
    python3 scripts/horizon_lab.py [--horizons 1,4,8,16] [--l2 0.05]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from typing import List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.feature_lab import evaluate_split  # noqa: E402
from src.models.dataset import build_labeled_examples  # noqa: E402
from src.models.realdata import load_corpus  # noqa: E402
from src.models.splits import (  # noqa: E402
    DEVELOPMENT_YEARS,
    OOS_YEARS,
    VALIDATION_YEARS,
    Sample,
    chronological_split,
)

CORPUS = "research/features_real/corpus/real_corpus_2021_2025.json.gz"
BAR_MINUTES = 15


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="forecast-horizon laboratory")
    parser.add_argument("--horizons", default="1,4,8,16")
    parser.add_argument("--l2", type=float, default=0.05)
    args = parser.parse_args(argv)
    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]

    print(f"loading {CORPUS} ...")
    feature_sets, closes = load_corpus(CORPUS)
    kept = [(fs, c) for fs, c in zip(feature_sets, closes) if fs.valid]
    feature_sets = [fs for fs, _ in kept]
    closes = [c for _, c in kept]
    print(f"valid instants: {len(feature_sets)}")

    header = (
        f"{'horizon':>9s} {'wall':>7s} {'n_dev':>7s} {'n_oos':>7s} "
        f"{'brier':>8s} {'skill':>9s} {'ece':>8s} {'acc':>7s}"
    )
    print(f"\nl2={args.l2}  dev={DEVELOPMENT_YEARS} val={VALIDATION_YEARS} "
          f"oos={OOS_YEARS}\n")
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
        m = evaluate_split(split, args.l2)
        wall = f"{h * BAR_MINUTES}m"
        print(
            f"{h:9d} {wall:>7s} {len(split.development):7d} {m['n_oos']:7d} "
            f"{m['brier']:8.4f} {m['skill']:+9.4f} {m['ece']:8.4f} "
            f"{m['accuracy']:7.4f}"
        )
    print(
        "\nh=1 reproduces the published baseline (brier 0.2497 skill +0.0012 "
        "acc 0.5170). A skill near zero across ALL horizons means the signal is "
        "absent, not merely mis-scaled."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
