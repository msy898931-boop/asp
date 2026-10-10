"""Order-flow laboratory: can volume and spread carry the missing edge?

The 185 shipped features are pure OHLC *shape*. Two leverage experiments
(calendar time, tree capacity) already refuted the cheap hypotheses. This tests
the remaining real lever the corpus itself can support: **new information from
the raw Dukascopy feed** that the feature engine never sees —

  * traded volume (the raw M1 file carries `volume`);
  * the BID/ASK spread (a separate `ask/` file exists);
  * **volume imbalance** — volume on up-closes vs down-closes, an order-flow
    proxy that shape-only features cannot express.

The features are causal: a decision at instant T may use only M1 bars fully
closed before T (`start + 60 <= T`). Nothing future is read. They are joined to
the existing corpus rows by decision instant, so the target and split are the
exact ones behind docs/FINAL_REPORT.md and the numbers stay comparable.

A random-feature arm runs beside the volume arm (same discipline as
feature_lab.py): if noise moves skill as much as the volume features do, the
end is overfitting, not signal.

Research only; the raw data is read, the corpus is not modified.

Measured (same split, l2=0.05, Platt), 8 causal flow features:

    arm                            feat    brier    skill     ece      acc
    baseline (185 OHLC)             185   0.2497  +0.0012  0.0012   0.5172
    baseline + order-flow (8)       193   0.2510  -0.0040  0.0300   0.4894
    baseline + random control (8)   193   0.2497  +0.0012  0.0012   0.5173

Result: order-flow features do not merely fail to help — they hurt. Skill goes
NEGATIVE (-0.0040) and accuracy falls below a coin flip (0.4894), while the
random control leaves the score untouched. The fit exploits the flow dimensions
spuriously on the development window and that hurts out-of-sample. Volume,
spread and volume-imbalance at M1→M15 on Dukascopy gold carry no exploitable
forward-direction signal for this model; they add noise the fit overfits.

This is the third "new information" hypothesis tested and refuted (after
calendar time and model capacity). The remaining untested lever is genuinely
different data — real order-book/tape imbalance or cross-asset context (DXY,
real yields) — not a transformation of this feed.

Usage:
    python3 scripts/volume_flow_lab.py [--l2 0.05]
"""

from __future__ import annotations

import argparse
import csv
import gzip
import math
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from scripts.feature_lab import (  # noqa: E402
    evaluate_split,
    with_extra,
)
from src.models.dataset import build_labeled_examples  # noqa: E402
from src.models.realdata import load_corpus  # noqa: E402
from src.models.splits import (  # noqa: E402
    DEVELOPMENT_YEARS,
    OOS_YEARS,
    VALIDATION_YEARS,
    Sample,
    Split,
    chronological_split,
)

DATA = "research/data/xauusd_m1"
CORPUS = "research/features_real/corpus/real_corpus_2021_2025.json.gz"
SECOND = 60


def _load(path: str, has_volume: bool):
    ts, o, h, l, c = [], [], [], [], []
    v = []
    with gzip.open(path, "rt") as handle:
        reader = csv.reader(handle)
        next(reader)
        for row in reader:
            ts.append(int(row[0]) // 1000)
            o.append(float(row[1]))
            h.append(float(row[2]))
            l.append(float(row[3]))
            c.append(float(row[4]))
            v.append(float(row[5]) if has_volume else 0.0)
    return (np.array(ts, dtype=np.int64), np.array(o), np.array(h), np.array(l),
            np.array(c), np.array(v))


def _flow_features(year: int) -> Dict[int, Dict[str, float]]:
    """Causal volume/spread features per M1-bar-closed decision instant."""
    ts, o, h, l, c, vol = _load(f"{DATA}/{year}.csv.gz", True)
    ats, ao, ah, al, ac, _ = _load(f"{DATA}/ask/{year}.csv.gz", False)
    ask = dict(zip(ats.tolist(), ac.tolist()))

    order = np.argsort(ts, kind="stable")
    ts, o, h, l, c, vol = ts[order], o[order], h[order], l[order], c[order], vol[order]
    spread = np.array([ask.get(int(t), math.nan) - cl for t, cl in zip(ts, c)])
    spread = np.nan_to_num(spread, nan=0.0)

    up = (c > o).astype(np.float64)
    down = (c < o).astype(np.float64)
    upv = vol * up
    dnv = vol * down
    cvol = np.concatenate([[0.0], np.cumsum(vol)])
    cupv = np.concatenate([[0.0], np.cumsum(upv)])
    cdnv = np.concatenate([[0.0], np.cumsum(dnv)])
    cspread = np.concatenate([[0.0], np.cumsum(spread)])

    def win(cumsum, j, n):
        lo = max(0, j - n)
        return cumsum[j] - cumsum[lo]

    out: Dict[int, Dict[str, float]] = {}
    # Decision instant T: last M1 bar closed before T is the one starting at T-60.
    for i in range(len(ts)):
        T = int(ts[i]) + SECOND
        j = i + 1  # bars [0, j) have start <= ts[i] <= T-60
        v15 = win(cvol, j, 15)
        v60 = win(cvol, j, 60)
        s15 = win(cspread, j, 15)
        s60 = win(cspread, j, 60)
        up15 = win(cupv, j, 15)
        dn15 = win(cdnv, j, 15)
        up60 = win(cupv, j, 60)
        dn60 = win(cdnv, j, 60)
        denom15 = up15 + dn15
        denom60 = up60 + dn60
        close = float(c[i])
        out[T] = {
            "flow.volRatio15_60": (v15 / (v15 + v60)) if (v15 + v60) > 0 else 0.5,
            "flow.volMeanRatio15_60": (
                (v15 / min(15, j)) / ((v15 / min(15, j)) + (v60 / min(60, j)))
                if (v15 + v60) > 0 else 0.5
            ),
            "flow.volImbalance15": ((up15 - dn15) / denom15) if denom15 > 0 else 0.0,
            "flow.volImbalance60": ((up60 - dn60) / denom60) if denom60 > 0 else 0.0,
            "flow.volLastRel": (
                float(vol[i]) / (v15 / min(15, j)) if v15 > 0 else 1.0
            ),
            "flow.spreadMean15": (s15 / min(15, j)) if j > 0 else 0.0,
            "flow.spreadRel15": (
                (s15 / min(15, j)) / close if close > 0 else 0.0
            ),
            "flow.spreadRatio15_60": (s15 / (s15 + s60)) if (s15 + s60) > 0 else 0.5,
        }
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="order-flow laboratory")
    parser.add_argument("--l2", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=20260101)
    args = parser.parse_args(argv)

    print(f"loading corpus {CORPUS} ...")
    feature_sets, closes = load_corpus(CORPUS)
    kept = [(fs, c) for fs, c in zip(feature_sets, closes) if fs.valid]
    feature_sets = [fs for fs, _ in kept]
    closes = [c for _, c in kept]
    base = build_labeled_examples(feature_sets, closes, horizon=1)
    print(f"valid examples: {len(base)}")

    print("building order-flow features from raw Dukascopy (volume + ask spread) ...")
    flow: Dict[int, Dict[str, float]] = {}
    for year in (2021, 2022, 2023, 2024, 2025):
        flow.update(_flow_features(year))
    print(f"  feature instants: {len(flow)}")

    missing = sum(1 for e in base if e.timestamp not in flow)
    if missing:
        print(f"  WARNING: {missing} corpus rows have no flow features (gap)")
    flow_extra = [flow.get(e.timestamp, {k: 0.0 for k in next(iter(flow.values()))})
                  for e in base]

    import random

    rng = random.Random(args.seed)
    n_flow = len(next(iter(flow.values())))
    random_extra = [{f"rand.{i}": rng.random() for i in range(n_flow)} for _ in base]

    def split_of(examples):
        samples = [
            Sample(datetime.fromtimestamp(e.timestamp, tz=timezone.utc), e)
            for e in examples
        ]
        return chronological_split(
            samples, DEVELOPMENT_YEARS, VALIDATION_YEARS, OOS_YEARS
        )

    arms = {
        "baseline (185 OHLC)": base,
        f"baseline + order-flow ({n_flow})": with_extra(base, flow_extra),
        f"baseline + random control ({n_flow})": with_extra(base, random_extra),
    }
    header = f"{'arm':38s} {'feat':>5s} {'brier':>8s} {'skill':>9s} {'ece':>8s} {'acc':>7s}"
    print(f"\nl2={args.l2}  dev={DEVELOPMENT_YEARS} val={VALIDATION_YEARS} oos={OOS_YEARS}\n")
    print(header)
    print("-" * len(header))
    for name, examples in arms.items():
        m = evaluate_split(split_of(examples), args.l2)
        print(
            f"{name:38s} {m['n_features']:5d} {m['brier']:8.4f} "
            f"{m['skill']:+9.4f} {m['ece']:8.4f} {m['accuracy']:7.4f}"
        )
    print("\nReference — published baseline: brier 0.2497 skill +0.0012 acc 0.5170.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
