#!/usr/bin/env python3
"""Build the feature store: every replayed trade from the real corpus, with the
context it opened under.

The replay engine says *what* happened; this says *under what conditions*, so
losses can be grouped and compared instead of guessed at. It reads the yearly
Dukascopy M1 files, resamples to M15 exactly as the live tracker does, replays
the live rules, and writes one JSONL row per resolved trade.

The output is the raw material for `study.py`: no thresholds are chosen and
nothing is filtered here, so the same store answers every later question and a
finding cannot be an artefact of how the file was written.

Run: python3 research/features_real/build_store.py [--years 2021 ...] [--out PATH]
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
for extra in (os.path.join(REPO, "tracker"),):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import features  # noqa: E402
import replay  # noqa: E402

DATA_DIR = os.path.join(REPO, "research", "data", "xauusd_m1")
DEFAULT_OUT = os.path.join(HERE, "feature_store.jsonl")
TF = 900  # M15


def _open(path: str):
    return (gzip.open(path, "rt", encoding="utf-8") if path.endswith(".gz")
            else open(path, encoding="utf-8"))


def resample(path: str, tf: int = TF) -> list[dict]:
    """Resample a Dukascopy M1 CSV (plain or .gz) into ``tf``-second bars.

    The live tracker reads M15; passing a larger ``tf`` is how the "is a longer
    bar better?" question is asked without touching the engine's rules.
    """
    buckets: dict = {}
    order: list[int] = []
    with _open(path) as fh:
        for row in csv.DictReader(fh):
            raw = row["timestamp"]
            try:
                ts = int(raw)
                if ts > 1e12:
                    ts //= 1000
            except ValueError:
                ts = int(dt.datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
                         .replace(tzinfo=dt.timezone.utc).timestamp())
            key = ts - (ts % tf)
            o, h, l, c = (float(row["open"]), float(row["high"]),
                          float(row["low"]), float(row["close"]))
            v = float(row.get("volume") or 0)
            if key not in buckets:
                buckets[key] = {"time": key, "open": o, "high": h, "low": l,
                                "close": c, "volume": v}
                order.append(key)
            else:
                b = buckets[key]
                b["high"] = max(b["high"], h)
                b["low"] = min(b["low"], l)
                b["close"] = c
                b["volume"] += v
    return [buckets[k] for k in order]


def load_m15(path: str) -> list[dict]:
    """Back-compat alias: M15 bars, the tracker's timeframe."""
    return resample(path, TF)


def year_files(years: list[str]) -> list[tuple[str, str]]:
    found = []
    for y in years:
        for name in ("%s.csv.gz" % y, "%s.csv" % y, "xauusd_m1_%s.csv" % y):
            path = os.path.join(DATA_DIR, name)
            if os.path.exists(path):
                found.append((y, path))
                break
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the ASTRA feature store")
    parser.add_argument("--years", nargs="*",
                        default=["2021", "2022", "2023", "2024", "2025", "2026"])
    parser.add_argument("--tf", type=int, default=TF,
                        help="bar size in seconds (900=M15, 3600=H1, 14400=H4)")
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    files = year_files(args.years)
    if not files:
        sys.stderr.write("[store] no data files under %s\n" % DATA_DIR)
        return 1

    total = 0
    with open(args.out, "w", encoding="utf-8") as out:
        for year, path in files:
            bars = resample(path, args.tf)
            if len(bars) < replay.MIN_BARS:
                sys.stderr.write("[store] %s: only %d bars, skipped\n"
                                 % (year, len(bars)))
                continue
            signals = replay.build_signals(bars)
            rows = features.rows_for_signals(bars, signals)
            for r in rows:
                r["year"] = year
                out.write(json.dumps(r, ensure_ascii=False) + "\n")
            total += len(rows)
            sys.stderr.write("[store] %s (tf=%ds): %d bars -> %d resolved trades\n"
                             % (year, args.tf, len(bars), len(rows)))
    sys.stderr.write("[store] wrote %d rows to %s\n" % (total, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
