"""Deterministic end-to-end demo of the T03 logistic-baseline pipeline.

IMPORTANT — this demo uses a SYNTHETIC, deterministic dataset. It is a pipeline
validation, NOT a market result. There is no real XAUUSD data in this container,
so no claim about the market is made or implied. RULE C still applies: nothing
here is a published probability.

Run:
    python3 -m src.models.demo_baseline

The generator is a pure function of the row index (a fixed sine mixture, no RNG,
no hashing), so the whole report is reproducible byte-for-byte.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import List, Tuple

from src.models.baseline import run_baseline
from src.models.dataset import LabeledExample, build_labeled_examples, purge_split
from src.models.features import (
    PER_TIMEFRAME_BOUNDS,
    PER_TIMEFRAME_DISCRETE,
    FeatureSet,
    FeatureVector,
)

SYNTHETIC_BANNER = "SYNTHETIC DEMO — not a market result (no real data available)"

_PARTITION_YEARS = (2021, 2022, 2023, 2024, 2025)
_HOUR = timedelta(hours=1)


def _bounded_signed(x: float) -> float:
    return max(-1.0, min(1.0, x))


def _bounded_unit(x: float) -> float:
    return max(0.0, min(1.0, x))


def _latent(i: int) -> float:
    """Deterministic latent state in [-1,1]: a slow cycle plus a fast ripple."""
    return 0.6 * math.sin(i * 0.05) + 0.4 * math.sin(i * 0.31)


def _feature_offset(name: str, index: int) -> float:
    """Deterministic per-feature offset in [0,1). No hashing, no randomness."""
    seed = sum(ord(c) for c in name) % 97
    return ((seed * 13 + index * 7) % 100) / 100.0


def _synthetic_features(i: int) -> dict:
    state = _latent(i)
    values = {}
    for name, (low, _high) in PER_TIMEFRAME_BOUNDS.items():
        raw = state * (0.3 + 0.7 * _feature_offset(name, i % 17))
        values[name] = _bounded_signed(raw) if low < 0 else _bounded_unit(raw)
    for name in PER_TIMEFRAME_DISCRETE:
        values[name] = 1.0 if state > 0.0 else -1.0
    return values


def _year_for_row(i: int, per_year: int) -> int:
    return _PARTITION_YEARS[min(i // per_year, len(_PARTITION_YEARS) - 1)]


def _synthetic_series(n: int) -> Tuple[List[FeatureSet], List[float]]:
    """Deterministic feature snapshots and aligned closes, spread over 2021-2025."""
    feature_sets: List[FeatureSet] = []
    closes: List[float] = []
    price = 1800.0
    per_year = n // len(_PARTITION_YEARS)
    for i in range(n):
        year = _year_for_row(i, per_year)
        ts = int(
            (
                datetime(year, 1, 1, tzinfo=timezone.utc)
                + _HOUR * (i % (365 * 24))
            ).timestamp()
        )
        values = _synthetic_features(i)
        vector = FeatureVector("M15", ts, values, "VALID", True)
        feature_sets.append(
            FeatureSet(
                asOfBarOpenSec=ts,
                perTimeframe=(vector,),
                cross=None,
                quality="VALID",
                valid=True,
            )
        )
        # Forward price: the latent state weakly predicts the next move.
        price += _latent(i) * 0.8 + 0.05 * math.sin(i * 1.7)
        closes.append(round(price, 6))
    return feature_sets, closes


def _dedupe_sorted(examples: List[LabeledExample]) -> List[LabeledExample]:
    ordered = sorted(examples, key=lambda e: e.timestamp)
    out: List[LabeledExample] = []
    seen = set()
    for e in ordered:
        if e.timestamp in seen:
            continue
        seen.add(e.timestamp)
        out.append(e)
    return out


def main() -> int:
    n = 2000
    feature_sets, closes = _synthetic_series(n)
    examples = build_labeled_examples(feature_sets, closes, horizon=1)
    examples = _dedupe_sorted(examples)

    split = purge_split(examples)
    if not (split.development and split.validation):
        print("demo could not populate development+validation partitions")
        return 1

    report = run_baseline(split.development, split.validation)
    print(SYNTHETIC_BANNER)
    print(
        f"rows: total={len(examples)} dev={len(split.development)} "
        f"val={len(split.validation)} oos={len(split.oos)}"
    )
    print(
        f"model: converged={report.model.converged} iters={report.model.iterations} "
        f"features={len(report.columns)}"
    )
    print(report.summary())
    print(
        "NOTE: synthetic data only — a pipeline check, not evidence about XAUUSD. "
        "Uncalibrated and unpublished (RULE C)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
