#!/usr/bin/env python3
"""T26 — real XAUUSD M1 -> frozen FeatureSet harness (Agent-A zone).

Feeds real Dukascopy M1 bars through the REAL C++ AnalyticalFeatureEngine and
emits the frozen ``FeatureSet`` JSON the Python model layer consumes.

This driver does **no feature computation**. It is a data-prep boundary:

    M1 CSV -> calendar aggregation to M1/M5/.../MN1 -> aura_feature_dump
           -> frozen FeatureSet JSON -> validated with parse_feature_set

All feature values come from ``build/aura_feature_dump`` (which links
``src/analysis/features/AnalyticalFeatureEngine.cpp``). Every emitted decision
instant is therefore a genuine C++ engine snapshot; the sample is real-data
evidence, not a synthetic pipeline check (that was T01/T24).

Aggregation is calendar-aligned (UTC): a higher-timeframe bar starts at the
bucket boundary and carries the first open, the max high, the min low, the last
close and the summed tick volume of its M1 members. The dumper excludes any bar
that has not fully closed at the decision instant, so partially formed
higher-timeframe bars are never read (no lookahead).

Usage:
    # real corpus (once T25 has pushed the raw yearly CSVs)
    python3 research/features_real/run_features.py \
        --m1 research/data/xauusd_m1/2025.csv \
        --out research/features_real/sample_2025.json --decisions 500

    # determinism / hermetic self-test (no data files needed)
    python3 research/features_real/run_features.py --synthetic --out /tmp/s.json \
        --decisions 20

Exit codes: 0 ok, 2 usage/config, 3 missing input or dump binary, 4 validation.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1")

_SECONDS = {
    "M1": 60,
    "M5": 5 * 60,
    "M15": 15 * 60,
    "M30": 30 * 60,
    "H1": 60 * 60,
    "H4": 4 * 60 * 60,
    "D1": 24 * 60 * 60,
}
# 1970-01-05 was a Monday: the reference for weekly bucket alignment.
_MONDAY_EPOCH = 345600
_WEEK = 7 * 24 * 60 * 60

DEFAULT_DUMP = os.path.join(REPO_ROOT, "build", "aura_feature_dump")


def bucket_start(sec: int, tf: str) -> int:
    """Calendar-aligned bucket start (UTC) for a bar-open second."""
    if tf in _SECONDS:
        width = _SECONDS[tf]
        return (sec // width) * width
    if tf == "W1":
        return ((sec - _MONDAY_EPOCH) // _WEEK) * _WEEK + _MONDAY_EPOCH
    if tf == "MN1":
        dt = datetime.fromtimestamp(sec, tz=timezone.utc)
        return int(dt.replace(day=1, hour=0, minute=0, second=0,
                              microsecond=0).timestamp())
    raise ValueError(f"unknown timeframe {tf!r}")


def _to_secs(ts: int) -> int:
    """Normalise an epoch timestamp to seconds (accepts s or ms)."""
    return ts // 1000 if ts >= 100_000_000_000 else ts


def _read_text(path: str) -> str:
    """Decode a bar file: gzip, UTF-16 (MT5 export, BOM) or UTF-8.

    The committed multi-year Dukascopy corpus ships as deterministic per-year
    `.csv.gz`; the raw MT5 export is UTF-16 with a BOM; Dukascopy fetch output is
    UTF-8 (a header may be present).
    """
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as handle:
        raw = handle.read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


# ISO (canonical Phase 5.2 corpus) and dot-date (raw MT5 export) forms.
_TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                 "%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M")


def _parse_open_time(text: str) -> int:
    """Bar-open time -> epoch seconds (UTC). Accepts epoch (s/ms) or a datetime.

    The canonical Phase 5.2 corpus (research/data/xauusd_m1/xauusd_m1_real.csv)
    carries ISO timestamps and a raw MT5 export carries 'YYYY.MM.DD HH:MM', so
    both are accepted here rather than forcing callers to pre-convert.
    """
    text = text.strip()
    if not text:
        raise ValueError("empty timestamp")
    try:
        return _to_secs(int(text))
    except ValueError:
        pass
    for fmt in _TIME_FORMATS:
        try:
            stamp = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return int(stamp.timestamp())
        except ValueError:
            continue
    raise ValueError(f"unrecognised timestamp {text!r}")


def load_m1(paths):
    """Read M1 CSVs: Dukascopy, canonical MT5 corpus, or raw MT5 export."""
    rows = []
    for path in paths:
        for raw in _read_text(path).splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            cols = [c.strip() for c in line.split(",")]
            # A leading non-numeric token is a header; anything else that fails
            # to parse is real corruption and must be loud, not silently dropped
            # (dropping a bar would fabricate a gap).
            if not re.match(r"^[0-9]", cols[0]):
                continue  # header line
            if len(cols) < 5:
                raise SystemExit(f"malformed M1 row in {path}: {line!r}")
            try:
                sec = _parse_open_time(cols[0])
                ohlc = [float(cols[i]) for i in (1, 2, 3, 4)]
            except ValueError as exc:
                raise SystemExit(
                    f"malformed M1 row in {path}: {line!r} ({exc})"
                ) from exc
            # Volume is optional (some MT5 tick exports omit it). Where present it
            # may be fractional (Dukascopy, millions of units) - an activity proxy,
            # so round to a whole tick count. Extra columns (MT5 spread) ignored.
            try:
                vol = round(float(cols[5])) if len(cols) >= 6 else 0
            except ValueError:
                vol = 0
            rows.append((sec, ohlc[0], ohlc[1], ohlc[2], ohlc[3], vol))
    rows.sort(key=lambda r: r[0])
    # Multiple files may be supplied out of order; sorting harmonises them.
    # Duplicate open times, however, are ambiguous bars — reject, never silently
    # pick one (that would fabricate a bar).
    for i in range(1, len(rows)):
        if rows[i][0] == rows[i - 1][0]:
            raise SystemExit(f"duplicate M1 open time at row {i}: {rows[i][0]}")
    return rows


def aggregate(m1, tf):
    """Aggregate M1 rows into bars for one timeframe (open-time keyed)."""
    out = {}
    for sec, o, h, l, c, v in m1:
        b = bucket_start(sec, tf)
        cur = out.get(b)
        if cur is None:
            out[b] = [o, h, l, c, v]
        else:
            cur[1] = max(cur[1], h)
            cur[2] = min(cur[2], l)
            cur[3] = c
            cur[4] += v
    return sorted(out.items())


def write_bars(path, bars):
    with open(path, "w", newline="") as handle:
        handle.write("timestamp_ms_utc,open,high,low,close,volume\n")
        for b, (o, h, l, c, v) in bars:
            handle.write(f"{b * 1000},{o},{h},{l},{c},{v}\n")


def synth_m1(n_minutes: int, seed: int = 20210101):
    """Deterministic pseudo-random M1 series (self-test only; not evidence)."""
    rows = []
    price = 1800.0
    start = 1735689600  # 2025-01-01 00:00:00 UTC
    for i in range(n_minutes):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        step = ((seed / 0x7FFFFFFF) - 0.5) * 0.4
        o = price
        c = round(price + step, 3)
        h = round(max(o, c) + abs(step) * 0.3, 3)
        l = round(min(o, c) - abs(step) * 0.3, 3)
        v = 10 + (seed % 90)
        rows.append((start + i * 60, o, h, l, c, v))
        price = c
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="T26 real-data FeatureSet harness")
    ap.add_argument("--m1", nargs="*", default=[],
                    help="M1 CSV path(s); files are concatenated in time order")
    ap.add_argument("--synthetic", action="store_true",
                    help="generate a deterministic synthetic M1 series (self-test)")
    ap.add_argument("--synthetic-minutes", type=int, default=40000)
    ap.add_argument("--out", required=True, help="output FeatureSet JSON path")
    ap.add_argument("--decisions", type=int, default=500,
                    help="max decision instants to emit (0 = all M15 opens)")
    ap.add_argument("--dump", default=DEFAULT_DUMP, help="aura_feature_dump binary")
    ap.add_argument("--workdir", default=None, help="scratch dir (default: temp)")
    ap.add_argument("--write-bars", default=None,
                    help="optional dir to keep the aggregated <TF>.csv files")
    ap.add_argument("--dump-sample", default=None,
                    help="optional pretty-printed sample of the first sets")
    args = ap.parse_args()

    if args.synthetic:
        m1 = synth_m1(args.synthetic_minutes)
    elif args.m1:
        missing = [p for p in args.m1 if not os.path.isfile(p)]
        if missing:
            print(f"missing M1 input: {missing}", file=sys.stderr)
            return 3
        m1 = load_m1(args.m1)
    else:
        ap.error("provide --m1 or --synthetic")

    if not m1:
        print("no M1 rows loaded", file=sys.stderr)
        return 3

    if not os.path.isfile(args.dump) or not os.access(args.dump, os.X_OK):
        print(f"dump binary missing/not executable: {args.dump}", file=sys.stderr)
        return 3

    workdir = args.workdir or tempfile.mkdtemp(prefix="t26_")
    os.makedirs(workdir, exist_ok=True)

    bars_dir = args.write_bars or workdir
    os.makedirs(bars_dir, exist_ok=True)
    m15_close_secs = []
    for tf in TIMEFRAMES:
        bars = aggregate(m1, tf)
        write_bars(os.path.join(bars_dir, f"{tf}.csv"), bars)
        if tf == "M15":
            # The decision instant is the bar's CLOSE: the moment the last M15
            # bar becomes knowable. The engine snapshots the closed history and
            # the dumper emits that bar's close as the T27 label.
            m15_close_secs = [b + 15 * 60 for b, _ in bars]

    decisions = m15_close_secs if args.decisions in (0, None) \
        else m15_close_secs[:args.decisions]
    if not decisions:
        print("no M15 bars to derive decisions from", file=sys.stderr)
        return 3
    dec_path = os.path.join(workdir, "decisions.csv")
    with open(dec_path, "w") as handle:
        handle.write("\n".join(str(d) for d in decisions) + "\n")

    out_abs = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_abs) or ".", exist_ok=True)
    proc = subprocess.run(
        [args.dump, bars_dir, dec_path, out_abs, ",".join(TIMEFRAMES)],
        capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"aura_feature_dump failed ({proc.returncode}): {proc.stderr}",
              file=sys.stderr)
        return 3

    from src.models.features import parse_feature_set

    with open(out_abs) as handle:
        sets = json.load(handle)
    validated = 0
    for i, raw in enumerate(sets):
        parsed = parse_feature_set(raw)
        if parsed.asOfBarOpenSec != decisions[i]:
            print(f"set[{i}] asOfBarOpenSec {parsed.asOfBarOpenSec} != "
                  f"decision {decisions[i]}", file=sys.stderr)
            return 4
        if "close" not in raw or not isinstance(raw["close"], (int, float)):
            print(f"set[{i}] missing numeric 'close' (T27 contract)",
                  file=sys.stderr)
            return 4
        validated += 1

    if args.dump_sample:
        with open(args.dump_sample, "w") as handle:
            json.dump(sets[:min(3, len(sets))], handle, indent=2)

    n_valid = sum(1 for r in sets if r["valid"])
    print(f"T26: {len(m1)} M1 bars -> {validated} decision sets "
          f"({n_valid} valid) -> {out_abs}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
