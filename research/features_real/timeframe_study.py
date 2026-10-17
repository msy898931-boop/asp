#!/usr/bin/env python3
"""Timeframe study: is a longer bar the one lever that clears the gate?

The M15 rules lose because a fixed spread is large next to a 15-minute move.
A longer bar makes the same spread small, so this asks the single most
promising question the prior work raised — and it asks it through the gate,
not through a raw win rate.

For each timeframe it builds the same feature store the M15 study uses, then
runs every candidate condition through the in/out-of-sample gate. A timeframe
is only reported as an edge if a condition clears both halves.

Run: python3 research/features_real/timeframe_study.py [--tfs 900 3600 14400]
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
for extra in (os.path.join(REPO, "tracker"), HERE):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import build_store  # noqa: E402
import study  # noqa: E402
import validation_gate as gate  # noqa: E402

IS_YEARS = ["2021", "2022", "2023"]
OOS_YEARS = ["2024", "2025", "2026"]


def build_rows(tf: int) -> list[dict]:
    """Build a feature store for one timeframe, in a temp file, and load it."""
    files = build_store.year_files(IS_YEARS + OOS_YEARS)
    rows: list[dict] = []
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                     encoding="utf-8") as tmp:
        path = tmp.name
    try:
        import json

        import features
        import replay
        with open(path, "w", encoding="utf-8") as out:
            for year, src in files:
                bars = build_store.resample(src, tf)
                if len(bars) < replay.MIN_BARS:
                    continue
                signals = replay.build_signals(bars)
                for r in features.rows_for_signals(bars, signals):
                    r["year"] = year
                    out.write(json.dumps(r, ensure_ascii=False) + "\n")
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    finally:
        os.unlink(path)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Timeframe gate study")
    parser.add_argument("--tfs", nargs="*", type=int, default=[3600, 14400])
    parser.add_argument("--split-year", type=int, default=2024)
    parser.add_argument("--min-n", type=int, default=100)
    args = parser.parse_args(argv)

    names = {900: "M15", 3600: "H1", 14400: "H4", 86400: "D1"}
    for tf in args.tfs:
        rows = build_rows(tf)
        print("\n" + "=" * 92)
        print("TIMEFRAME %s (tf=%ds) — %d resolved trades"
              % (names.get(tf, str(tf)), tf, len(rows)))
        print("=" * 92)
        for name, pred in study.candidates():
            if pred is None:
                continue          # the atr_pct median filter is M15-specific
            subset = [r for r in rows if pred(r)]
            is_rows, oos_rows = gate.split_rows(subset, args.split_year)
            verdict = gate.gate(gate.evaluate(is_rows), gate.evaluate(oos_rows),
                                min_n=args.min_n)
            mark = {"PROMOTE": "PASS", "REJECT": "fail",
                    "INSUFFICIENT": "thin"}[verdict["verdict"]]
            print("[%s] %-26s IS %s" % (mark, name, study.fmt_half(verdict["in_sample"])))
            print("     %-26s OOS %s" % ("", study.fmt_half(verdict["out_of_sample"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
