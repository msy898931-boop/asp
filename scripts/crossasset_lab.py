"""Cross-asset laboratory: does the macro context gold is priced against help?

Every refuted hypothesis so far kept the view inside the gold tape (OHLC shape,
calendar time, tree capacity, its own volume/spread). Gold, however, is priced
*against* the US dollar and real yields: a rising DXY and rising real rates are
the classic headwinds. Those are different information entirely.

This joins **DXY** (Yahoo `DX-Y.NYB`) and the **10-year real yield** (FRED
`DFII10`) to each decision instant by *last-observation-carried-forward*, which
is causal — a macro series is only ever observed after it is published, and a
daily close stamped at day D is available from D onward. It is a strict subset
of what a live system would know, so no look-ahead is introduced.

Free, keyless sources: Yahoo chart JSON and FRED's csv endpoint. Data is cached
under research/macro/ (gitignored) and the experiment degrades honestly if the
network is unavailable — it reports the failure, it does not fabricate a value.

A random control runs beside the macro arm, as in the other labs.

Usage:
    python3 scripts/crossasset_lab.py [--l2 0.05]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import random
import sys
import urllib.request
from bisect import bisect_right
from datetime import datetime, timezone
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.feature_lab import evaluate_split, with_extra
from src.models.dataset import build_labeled_examples
from src.models.realdata import load_corpus
from src.models.splits import (
    DEVELOPMENT_YEARS,
    OOS_YEARS,
    VALIDATION_YEARS,
    Sample,
    chronological_split,
)

CORPUS = "research/features_real/corpus/real_corpus_2021_2025.json.gz"
CACHE = "research/macro"
UA = {"User-Agent": "Mozilla/5.0 (compatible; ASTRA-research/1.0)"}


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read()


def dxy_series() -> List[Tuple[int, float]]:
    """Daily DXY close as (epoch_sec, close), ascending. Cached."""
    path = os.path.join(CACHE, "dxy_yahoo.json")
    raw = None
    if os.path.exists(path):
        with open(path, "rb") as fh:
            raw = fh.read()
    else:
        raw = _fetch(
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            "DX-Y.NYB?range=10y&interval=1d"
        )
        os.makedirs(CACHE, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(raw)
    doc = json.loads(raw)
    result = doc["chart"]["result"][0]
    stamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    out = [(int(t), float(c)) for t, c in zip(stamps, closes) if c is not None]
    out.sort()
    return out


def fred_series(series_id: str) -> List[Tuple[int, float]]:
    """A FRED daily series as (epoch_sec, value), ascending. Cached."""
    path = os.path.join(CACHE, f"fred_{series_id}.csv")
    raw = None
    if os.path.exists(path):
        with open(path, "rb") as fh:
            raw = fh.read()
    else:
        raw = _fetch(
            "https://fred.stlouisfed.org/graph/fredgraph.csv?id=" + series_id
        )
        os.makedirs(CACHE, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(raw)
    out = []
    for row in csv.DictReader(io.StringIO(raw.decode())):
        key = list(row.keys())
        date_s, val_s = row[key[0]], row[key[1]]
        if not val_s or val_s == ".":
            continue  # FRED marks holidays/non-observations with '.'
        dt = datetime.strptime(date_s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        out.append((int(dt.timestamp()), float(val_s)))
    out.sort()
    return out


def _asof(series: List[Tuple[int, float]], t: int) -> float:
    """Last value with publish time <= t. NaN before the series starts."""
    ts = [s[0] for s in series]
    i = bisect_right(ts, t) - 1
    return float("nan") if i < 0 else float(series[i][1])


def macro_features(instants: List[int]) -> List[Dict[str, float]]:
    dxy = dxy_series()
    real10 = fred_series("DFII10")
    day = 86400
    out: List[Dict[str, float]] = []
    for t in instants:
        d0 = _asof(dxy, t)
        d5 = _asof(dxy, t - 5 * day)
        d20 = _asof(dxy, t - 20 * day)
        r0 = _asof(real10, t)
        r5 = _asof(real10, t - 5 * day)
        r20 = _asof(real10, t - 20 * day)
        out.append(
            {
                "macro.dxyChg5": (d0 - d5) if (d0 == d0 and d5 == d5) else 0.0,
                "macro.dxyChg20": (d0 - d20) if (d0 == d0 and d20 == d20) else 0.0,
                "macro.real10Chg5": (r0 - r5) if (r0 == r0 and r5 == r5) else 0.0,
                "macro.real10Chg20": (r0 - r20) if (r0 == r0 and r20 == r20) else 0.0,
                "macro.dxyLevel": d0 if d0 == d0 else 0.0,
                "macro.real10Level": r0 if r0 == r0 else 0.0,
            }
        )
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="cross-asset laboratory")
    parser.add_argument("--l2", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=20260101)
    args = parser.parse_args(argv)

    print(f"loading {CORPUS} ...")
    feature_sets, closes = load_corpus(CORPUS)
    kept = [(fs, c) for fs, c in zip(feature_sets, closes) if fs.valid]
    feature_sets = [fs for fs, _ in kept]
    closes = [c for _, c in kept]
    base = build_labeled_examples(feature_sets, closes, horizon=1)
    print(f"valid examples: {len(base)}")

    print("fetching macro context (DXY via Yahoo, DFII10 via FRED) ...")
    try:
        macro = macro_features([e.timestamp for e in base])
    except Exception as exc:  # noqa: BLE001 - network/source unavailable: degrade honestly
        print(f"  ERROR: cannot build macro context: {exc}")
        print("  (no fabrication: the experiment stops rather than guessing.)")
        return 2
    print(f"  macro rows: {len(macro)}  (6 features)")

    rng = random.Random(args.seed)
    random_extra = [{f"rand.{i}": rng.random() for i in range(6)} for _ in base]

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
        "baseline + macro/DXY (6)": with_extra(base, macro),
        "baseline + random control (6)": with_extra(base, random_extra),
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
