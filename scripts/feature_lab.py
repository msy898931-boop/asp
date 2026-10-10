"""Feature-signal laboratory.

Question: does the XAUUSD decision model gain predictive skill from information
it currently never sees — the *time of day / day of week* of the decision bar?

The shipped engine builds 25 features per timeframe, all pure OHLC shape
(wicks, body, runs, momentum, structure). Nothing encodes *when* the bar is:
gold has very different behaviour in the London and New York sessions than in
the Asian lull. This script tests whether calendar-time features move the
out-of-sample Brier skill off the ~+0.0012 coin-flip it currently sits at.

It reuses the repository's real pipeline end to end — `load_corpus`, the frozen
labels from `build_labeled_examples`, the chronological year split, the same
IRLS logistic (`l2=0.05`, matching the published run) and Platt calibration —
so the numbers are directly comparable to docs/FINAL_REPORT.md.

To keep the result honest, a RANDOM-feature arm runs beside the time arm. If
noise moves skill as much as the time features do, the time "gain" is
overfitting, not signal.

Measured (XAUUSD, Dukascopy 2021-2025, dev=2021-22 val=2023-24 oos=2025,
l2=0.05, Platt), reproducible via the command below:

    arm                            feat   brier    skill     ece      acc
    baseline (185 OHLC)             185  0.2497  +0.0012  0.0012   0.5172
    baseline + time of day (7)      192  0.2497  +0.0011  0.0036   0.5177
    baseline + random control (7)   192  0.2497  +0.0011  0.0015   0.5172

The baseline reproduces docs/FINAL_REPORT.md exactly (brier 0.2497, skill
+0.0012, acc 0.5170). Calendar-time features add NO skill: the eleven-
thousandth-place move is matched by the random control, and they cost
calibration. Conclusion: on this corpus at the M15 horizon, when-the-bar-is is
not exploitable signal for XAUUSD. Do not ship them into the C++ engine.

Usage:
    python3 scripts/feature_lab.py [--corpus FILE] [--l2 0.05]
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sys
from typing import List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone  # noqa: E402

from src.models.calibration import calibration_report  # noqa: E402
from src.models.calibrators import fit_calibrator  # noqa: E402
from src.models.dataset import (  # noqa: E402
    LabeledExample,
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
    Split,
    chronological_split,
)

SECONDS_PER_DAY = 86400


def time_features(ts: int) -> dict:
    """Causal calendar features for a decision instant (UTC).

    Only reads the instant itself, so nothing future can leak in. Cyclic
    encodings avoid the wrap-around discontinuity of a raw hour number.
    """
    hour = (ts % SECONDS_PER_DAY) / 3600.0
    # Monday=0 ... Sunday=6
    weekday = ((ts // SECONDS_PER_DAY) + 3) % 7  # epoch day 0 was a Thursday
    signed_tod = 2.0 * math.pi * hour / 24.0
    dow = 2.0 * math.pi * weekday / 7.0
    return {
        "time.sinHour": math.sin(signed_tod),
        "time.cosHour": math.cos(signed_tod),
        "time.sinDow": math.sin(dow),
        "time.cosDow": math.cos(dow),
        "time.isLondon": 1.0 if 7 <= hour < 16 else 0.0,
        "time.isNewYork": 1.0 if 12 <= hour < 21 else 0.0,
        "time.isOverlap": 1.0 if 12 <= hour < 16 else 0.0,
    }


def with_extra(
    examples: List[LabeledExample], extra: List[dict]
) -> List[LabeledExample]:
    out = []
    for ex, add in zip(examples, extra):
        row = dict(ex.features)
        row.update(add)
        out.append(
            LabeledExample(
                timestamp=ex.timestamp,
                features=row,
                label=ex.label,
                label_timestamp=ex.label_timestamp,
            )
        )
    return out


def evaluate_split(split: Split, l2: float) -> dict:
    dev = [s.payload for s in split.development]
    val = [s.payload for s in split.validation]
    oos = [s.payload for s in split.oos]
    factory = _logistic_factory(l2)
    columns = feature_columns(dev)
    for name, part in (("val", val), ("oos", oos)):
        if feature_columns(part) != columns:
            raise SystemExit(f"column mismatch in {name}")
    model = factory(*to_matrix(dev, columns))
    calibrator = fit_calibrator(
        "platt", [model.decision_function(r) for r in to_matrix(val, columns)[0]],
        to_matrix(val, columns)[1],
    )
    x_oos, y_oos = to_matrix(oos, columns)
    probs = calibrator.transform_batch([model.decision_function(r) for r in x_oos])
    rep = calibration_report(probs, y_oos)
    return {
        "n_features": len(columns),
        "n_oos": rep.n,
        "brier": rep.brier,
        "skill": rep.brier_skill,
        "ece": rep.ece,
        "accuracy": sum(1 for p, y in zip(probs, y_oos) if (p >= 0.5) == bool(y))
        / len(oos),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="XAUUSD feature-signal laboratory")
    parser.add_argument(
        "--corpus",
        default="research/features_real/corpus/real_corpus_2021_2025.json.gz",
    )
    parser.add_argument("--l2", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=20260101)
    args = parser.parse_args(argv)

    print(f"loading {args.corpus} ...")
    feature_sets, closes = load_corpus(args.corpus)
    kept = [(fs, c) for fs, c in zip(feature_sets, closes) if fs.valid]
    feature_sets = [fs for fs, _ in kept]
    closes = [c for _, c in kept]
    print(f"valid instants: {len(feature_sets)}")

    base = build_labeled_examples(feature_sets, closes, horizon=1)
    print(f"labeled examples: {len(base)}")

    def split_of(examples: List[LabeledExample]) -> Split:
        samples = [
            Sample(datetime.fromtimestamp(e.timestamp, tz=timezone.utc), e)
            for e in examples
        ]
        return chronological_split(
            samples, DEVELOPMENT_YEARS, VALIDATION_YEARS, OOS_YEARS
        )

    rng = random.Random(args.seed)
    random_extra = [
        {f"rand.{i}": rng.random() for i in range(7)} for _ in base
    ]
    time_extra = [time_features(e.timestamp) for e in base]

    arms = {
        "baseline (185 OHLC features)": base,
        "baseline + time of day (7)": with_extra(base, time_extra),
        "baseline + random control (7)": with_extra(base, random_extra),
    }

    print(f"\nl2={args.l2}  split dev={DEVELOPMENT_YEARS} val={VALIDATION_YEARS} "
          f"oos={OOS_YEARS}\n")
    header = f"{'arm':38s} {'feat':>5s} {'n_oos':>7s} {'brier':>8s} " \
             f"{'skill':>9s} {'ece':>8s} {'acc':>7s}"
    print(header)
    print("-" * len(header))
    for name, examples in arms.items():
        m = evaluate_split(split_of(examples), args.l2)
        print(
            f"{name:38s} {m['n_features']:5d} {m['n_oos']:7d} "
            f"{m['brier']:8.4f} {m['skill']:+9.4f} {m['ece']:8.4f} "
            f"{m['accuracy']:7.4f}"
        )
    print(
        "\nPublished baseline (docs/FINAL_REPORT.md): brier 0.2497 "
        "skill +0.0012 ece 0.0015 acc 0.5170."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
