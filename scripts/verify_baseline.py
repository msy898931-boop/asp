"""Reproduce the published baseline and fail loudly if it drifts.

One command answers "is this checkout still the one that produced the report?":

    python3 scripts/verify_baseline.py

It runs the exact published pipeline — real corpus, single-bar labels,
chronological 2021-22 / 2023-24 / 2025 split, IRLS logistic l2=0.05, Platt
calibration — and checks the four headline numbers against docs/FINAL_REPORT.md.
Any drift beyond the tolerance exits non-zero, so CI (or a human) can trust the
"green" instead of eyeballing it.

Tolerances are tight enough to catch a regression (a changed feature, split,
solver iteration count or seed moves these visibly) and loose enough to absorb
last-bit float noise.

Exit codes: 0 all matched, 1 mismatch, 2 could not run.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

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
L2 = 0.05
TOL = 2e-4

# docs/FINAL_REPORT.md headline numbers.
EXPECTED = {
    "brier": 0.2497,
    "skill": 0.0012,
    "accuracy": 0.5172,
    "ece": 0.0012,
}


def main() -> int:
    if not os.path.exists(CORPUS):
        print(f"ERROR: corpus missing: {CORPUS}", file=sys.stderr)
        return 2

    print(f"loading {CORPUS} ...")
    feature_sets, closes = load_corpus(CORPUS)
    kept = [(fs, c) for fs, c in zip(feature_sets, closes) if fs.valid]
    feature_sets = [fs for fs, _ in kept]
    closes = [c for _, c in kept]
    print(f"valid instants: {len(feature_sets)}")

    examples = build_labeled_examples(feature_sets, closes, horizon=1)
    samples = [
        Sample(datetime.fromtimestamp(e.timestamp, tz=timezone.utc), e)
        for e in examples
    ]
    split = chronological_split(
        samples, DEVELOPMENT_YEARS, VALIDATION_YEARS, OOS_YEARS
    )
    measured = evaluate_split(split, L2)

    print(f"\nl2={L2}  dev={DEVELOPMENT_YEARS} val={VALIDATION_YEARS} oos={OOS_YEARS}  "
          f"n_features={measured['n_features']}  n_oos={measured['n_oos']}\n")
    header = f"{'metric':10s} {'expected':>10s} {'measured':>10s} {'delta':>10s}  ok"
    print(header)
    print("-" * len(header))

    failures = 0
    for key, expected in EXPECTED.items():
        got = measured[key]
        delta = got - expected
        ok = abs(delta) <= TOL
        if not ok:
            failures += 1
        print(
            f"{key:10s} {expected:10.4f} {got:10.4f} {delta:+10.5f}  "
            f"{'yes' if ok else 'NO'}"
        )

    print()
    if failures:
        print(f"FAIL: {failures} metric(s) drifted beyond {TOL:.0e}. "
              f"The checkout no longer reproduces docs/FINAL_REPORT.md.")
        return 1
    print(f"PASS: baseline reproduced within {TOL:.0e}. "
          f"docs/FINAL_REPORT.md is verified against this tree.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
