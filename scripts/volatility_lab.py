#!/usr/bin/env python3
"""Phase 1 — a calibrated "will the next window be volatile?" model.

Phase 0 showed the *size* of the next move is predictable (trailing vol ranks
forward vol, AUC 0.69-0.79) while its *sign* is not. This turns that into a
shippable classifier: the model outputs a probability that the next window is
high-volatility, which the bot reads as "stand aside" when it is high.

The label is causal and deployable, not an OOS-median artifact:

    label_i = 1  if  forward_vol(i, H)  >  rolling_median(trailing_vol)(i)

`rolling_median` looks only at trailing vols up to and including i, so nothing
about the future (or about 2025) leaks into the definition of "high".

It reuses the published pipeline unchanged — frozen features, 2021-22 / 2023-24
/ 2025 year split, IRLS logistic (l2=0.05), Platt calibration — so the numbers
are directly comparable to docs/FINAL_REPORT.md. It also prints the *naive*
baseline (just compare trailing vol to the rolling median) so the model has to
earn its place: if it does not beat the one-liner, it is not worth shipping.

Usage:
    python3 scripts/volatility_lab.py            # sweep the default horizons
    python3 scripts/volatility_lab.py --horizon 16
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sys
from datetime import datetime, timezone
from typing import List, Sequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.discrimination_lab import auc, fit_predict, permutation_p
from src.models.calibration import calibration_report
from src.models.dataset import (
    LabeledExample,
    assert_partitions_separated,
)
from src.models.realdata import load_corpus
from src.models.splits import (
    DEVELOPMENT_YEARS,
    OOS_YEARS,
    VALIDATION_YEARS,
    Sample,
    chronological_split,
)

CORPUS = "research/features_real/corpus/real_corpus_2021_2025.json.gz"
BAR_MINUTES = 15


def stdev(values: Sequence[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))


def rolling_median(values: Sequence[float], lookback: int) -> List[float]:
    """Median of the `lookback` values ending at each index (causal).

    A sliding sorted window keeps this O(n * lookback) in C-level list ops
    instead of re-sorting a full window per step, which matters at 100k rows.
    Early entries use the growing window, which is still causal.
    """
    import bisect
    out: List[float] = []
    window: List[float] = []
    for i, value in enumerate(values):
        bisect.insort(window, value)
        if len(window) > lookback:
            old = values[i - lookback]
            window.pop(bisect.bisect_left(window, old))
        out.append(window[len(window) // 2])
    return out


def vol_series(closes: Sequence[float]) -> List[float]:
    logret = [0.0] * len(closes)
    for i in range(1, len(closes)):
        logret[i] = math.log(closes[i] / closes[i - 1])
    return logret


def build_vol_examples(
    feature_sets: Sequence, closes: Sequence[float], horizon: int, window: int,
    median_lookback: int, feature_pattern: str = "",
) -> List[LabeledExample]:
    """Label each instant by whether the forward vol beats the causal rolling
    median of trailing vol. Pure and deterministic.

    `feature_pattern` (a regex) keeps only matching feature columns. A model of
    volatility should read volatility features, not all 185; this also cuts the
    stdlib solver's O(n*d^2) cost by ~50x.
    """
    import re
    keep = re.compile(feature_pattern) if feature_pattern else None

    def row_of(fs):
        flat = fs.as_flat_row()
        if keep is None:
            return flat
        return {k: v for k, v in flat.items() if keep.search(k)}

    logret = vol_series(closes)
    times = [fs.asOfBarOpenSec for fs in feature_sets]
    n = len(closes)

    trailing = [0.0] * n
    forward = [0.0] * n
    for i in range(window, n):
        trailing[i] = stdev(logret[i - window + 1:i + 1])
    for i in range(n - horizon):
        forward[i] = stdev(logret[i + 1:i + 1 + horizon])
    med = rolling_median(trailing, median_lookback)

    examples: List[LabeledExample] = []
    for i in range(window, n - horizon):
        label = 1 if forward[i] > med[i] else 0
        examples.append(LabeledExample(
            timestamp=times[i],
            features=row_of(feature_sets[i]),
            label=label,
            label_timestamp=times[i + horizon],
        ))
    return examples


def naive_auc(closes: Sequence[float], horizon: int, window: int,
              median_lookback: int) -> float:
    """The one-liner: score = trailing vol. If the model cannot beat this, the
    extra machinery is not earning its keep."""
    logret = vol_series(closes)
    n = len(closes)
    trailing = [0.0] * n
    forward = [0.0] * n
    for i in range(window, n):
        trailing[i] = stdev(logret[i - window + 1:i + 1])
    for i in range(n - horizon):
        forward[i] = stdev(logret[i + 1:i + 1 + horizon])
    med = rolling_median(trailing, median_lookback)
    scores, labels = [], []
    for i in range(window, n - horizon):
        scores.append(trailing[i])
        labels.append(1 if forward[i] > med[i] else 0)
    return auc(scores, labels)


def evaluate(feature_sets, closes, horizon: int, window: int,
             median_lookback: int, l2: float, seed: int,
             feature_pattern: str = "") -> dict:
    examples = build_vol_examples(feature_sets, closes, horizon, window,
                                  median_lookback, feature_pattern)
    samples = [Sample(datetime.fromtimestamp(e.timestamp, tz=timezone.utc), e)
               for e in examples]
    split = chronological_split(samples, DEVELOPMENT_YEARS, VALIDATION_YEARS,
                                OOS_YEARS)
    dev = [s.payload for s in split.development]
    val = [s.payload for s in split.validation]
    oos = [s.payload for s in split.oos]
    assert_partitions_separated((("dev", dev), ("val", val), ("oos", oos)))

    probs, y = fit_predict(dev, val, oos, l2)
    rep = calibration_report(probs, y)
    a = auc(probs, y)
    p = permutation_p(probs, y, a, random.Random(seed), 200)
    base_rate = sum(y) / len(y)
    return {
        "horizon": horizon,
        "minutes": horizon * BAR_MINUTES,
        "n_oos": rep.n,
        "base_rate": base_rate,
        "brier": rep.brier,
        "skill": rep.brier_skill,
        "ece": rep.ece,
        "auc": a,
        "auc_p": p,
        "naive_auc": naive_auc(closes, horizon, window, median_lookback),
    }


def export_model(feature_sets, closes, horizon: int, window: int,
                 median_lookback: int, l2: float, feature_pattern: str,
                 out_path: str) -> dict:
    """Train the deployable model and write a self-contained JSON artifact.

    Ships exactly the model whose OOS numbers we report: base fit on
    development (2021-22), calibrator on validation (2023-24). The bot loads
    only this file — no corpus, no numpy, no sklearn.
    """
    import json
    from datetime import timezone as _tz

    from src.models.calibrators import fit_calibrator
    from src.models.dataset import feature_columns, to_matrix
    from src.models.realdata import _logistic_factory

    examples = build_vol_examples(feature_sets, closes, horizon, window,
                                  median_lookback, feature_pattern)
    samples = [Sample(datetime.fromtimestamp(e.timestamp, tz=timezone.utc), e)
               for e in examples]
    split = chronological_split(samples, DEVELOPMENT_YEARS, VALIDATION_YEARS,
                                OOS_YEARS)
    dev = [s.payload for s in split.development]
    val = [s.payload for s in split.validation]
    oos = [s.payload for s in split.oos]

    columns = feature_columns(dev)
    model = _logistic_factory(l2)(*to_matrix(dev, columns))
    xv, yv = to_matrix(val, columns)
    calibrator = fit_calibrator(
        "platt", [model.decision_function(r) for r in xv], yv)
    xt, yt = to_matrix(oos, columns)
    probs = calibrator.transform_batch(
        [model.decision_function(r) for r in xt])
    rep = calibration_report(probs, yt)

    artifact = {
        "kind": "astra-volatility-model",
        "version": 1,
        "trained_at": datetime.now(_tz.utc).isoformat(timespec="seconds"),
        "target": "forward realized vol above its causal rolling median",
        "horizon_bars": horizon,
        "bar_minutes": BAR_MINUTES,
        "window_bars": window,
        "context_bars": 200,
        "median_lookback": median_lookback,
        "columns": list(columns),
        "standardizer": {"means": list(model.standardizer.means),
                         "stds": list(model.standardizer.stds)},
        "weights": list(model.weights),
        "intercept": model.intercept,
        "calibrator": {"method": "platt", "a": calibrator.a, "b": calibrator.b},
        # The tier the bot treats as "stand aside". OOS-justified: at 0.65 the
        # high tier is the top ~third of predicted risk (see the lab output).
        "alert_threshold": 0.65,
        "oos": {"n": rep.n, "brier": rep.brier, "skill": rep.brier_skill,
                "ece": rep.ece,
                "auc": auc(probs, yt)},
    }
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return artifact


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="volatility-target laboratory")
    parser.add_argument("--corpus", default=CORPUS)
    parser.add_argument("--horizons", default="4,16,96")
    parser.add_argument("--window", type=int, default=16,
                        help="trailing-vol window in bars")
    parser.add_argument("--median-lookback", type=int, default=2880,
                        help="bars in the causal rolling-median window (~30d)")
    parser.add_argument("--l2", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=20260101)
    parser.add_argument("--features", default=r"volatilityRatio|atrRatio",
                        help="regex of feature columns to keep (default: the "
                             "volatility ratios; empty string = all 185)")
    parser.add_argument("--export", metavar="PATH", default=None,
                        help="train on dev/val and write a deployable model JSON")
    args = parser.parse_args(argv)

    print(f"loading {args.corpus} ...")
    feature_sets, closes = load_corpus(args.corpus)
    kept = [(fs, c) for fs, c in zip(feature_sets, closes) if fs.valid]
    feature_sets = [fs for fs, _ in kept]
    closes = [c for _, c in kept]
    print(f"valid instants: {len(feature_sets)}")

    if args.export:
        art = export_model(feature_sets, closes, 16, args.window,
                           args.median_lookback, args.l2, args.features,
                           args.export)
        print(f"\nwrote {args.export}")
        print("  columns=%d  horizon=%d  oos skill=%+.4f  ece=%.4f  auc=%.3f" % (
            len(art["columns"]), art["horizon_bars"], art["oos"]["skill"],
            art["oos"]["ece"], art["oos"]["auc"]))
        return 0

    print("\n=== Phase 1: calibrated volatility classifier (OOS = 2025) ===")
    print("%-8s %-8s %-7s %-8s %-8s %-9s %-8s %-8s %-9s" %
          ("horizon", "minutes", "n_oos", "base", "brier", "skill",
           "ece", "auc", "naive_auc"))
    for h in (int(x) for x in args.horizons.split(",")):
        r = evaluate(feature_sets, closes, h, args.window,
                     args.median_lookback, args.l2, args.seed, args.features)
        print("%-8d %-8d %-7d %-8.3f %-8.4f %-+9.4f %-8.4f %-8.3f %-9.3f" % (
            r["horizon"], r["minutes"], r["n_oos"], r["base_rate"],
            r["brier"], r["skill"], r["ece"], r["auc"], r["naive_auc"]))
    print("\nThe model must beat naive_auc to be worth shipping. skill is against")
    print("the base-rate forecast; ece is calibration (<0.05 is the publish bar).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
