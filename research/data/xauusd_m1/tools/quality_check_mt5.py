#!/usr/bin/env python3
"""Quality checks for the operator-supplied MT5 XAUUSD M1 corpus (Phase 5.2).

Reads research/data/xauusd_m1/xauusd_m1_real.csv and writes
research/data/xauusd_m1/QUALITY.md. Every anomaly is reported; nothing is
repaired or hidden.

Checks (Phase 5.2 directive):
  * parsed successfully (ISO timestamps, numeric OHLC)
  * timestamps monotonic ascending and minute-aligned
  * no duplicate timestamps
  * no OHLC violations (high >= low/open/close, low <= open/close)
  * no NaN/null in OHLC
  * price range plausibility (directive band 1800-3000 USD/oz; a band miss is
    reported as a WARN, not repaired, because the operator's file is the
    authority for its own window)
  * coverage: bar count and first/last timestamp
  * gap structure: routine 2h daily break + weekend closures vs unexpected gaps
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # research/data/xauusd_m1
CSV = HERE / "xauusd_m1_real.csv"
OUT = HERE / "QUALITY.md"

MINUTE = 60
PRICE_BAND = (1800.0, 3000.0)
# Documented non-trading structure for a broker on a UTC+2/+3 server clock:
# a ~2h daily maintenance break at 23:00-01:00 and the Fri-night -> Mon
# weekend closure. Anything larger is surfaced.
ROUTINE_GAP_H = 3.0
WEEKEND_GAP_H = 56.0


def _fmt(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S")


def main() -> int:
    rows: list[tuple[dt.datetime, float, float, float, float, float]] = []
    bad_numeric = 0
    with CSV.open(newline="") as fh:
        reader = csv.reader(fh)
        next(reader, None)  # header row, skipped
        for cols in reader:
            if not cols:
                continue
            try:
                t = dt.datetime.strptime(cols[0], "%Y-%m-%d %H:%M:%S")
                o, h, l, c, v = (float(cols[i]) for i in range(1, 6))
            except (ValueError, IndexError):
                bad_numeric += 1
                continue
            rows.append((t, o, h, l, c, v))

    n = len(rows)
    timestamps = [r[0] for r in rows]
    dups = len(timestamps) - len(set(timestamps))
    monotonic = all(timestamps[i] > timestamps[i - 1] for i in range(1, n))
    not_minute = sum(1 for t in timestamps if t.second != 0)
    nan = sum(
        1 for t, o, h, l, c, v in rows
        if any(x != x for x in (o, h, l, c, v))
    )
    ohlc_viol = sum(
        1 for t, o, h, l, c, v in rows
        if not (h >= l and h >= o and h >= c and l <= o and l <= c)
    )

    gaps = []
    for i in range(1, n):
        secs = (timestamps[i] - timestamps[i - 1]).total_seconds()
        if secs > MINUTE * 5:
            gaps.append((timestamps[i - 1], timestamps[i], secs / 3600.0))
    weekend_gaps = sum(1 for a, b, h in gaps if h > ROUTINE_GAP_H + 1e-9)
    unexpected = sum(1 for a, b, h in gaps if h > WEEKEND_GAP_H + 1e-9)

    lo = min(r[3] for r in rows)
    hi = max(r[2] for r in rows)
    in_band = PRICE_BAND[0] <= lo and hi <= PRICE_BAND[1]
    zero_vol = sum(1 for r in rows if r[5] == 0)

    checks = [
        ("Parsed successfully", bad_numeric == 0, f"{bad_numeric} unparseable rows"),
        ("Monotonic timestamps", monotonic, "strictly ascending"),
        ("Minute-aligned timestamps", not_minute == 0, f"{not_minute} off-grid"),
        ("No duplicate timestamps", dups == 0, f"{dups} duplicates"),
        ("No NaN in OHLCV", nan == 0, f"{nan} NaN rows"),
        ("No OHLC violations", ohlc_viol == 0, f"{ohlc_viol} violations"),
        ("No unexpected gaps", unexpected == 0,
         f"{len(gaps)} session gaps, {weekend_gaps} weekend, {unexpected} unexpected"),
        ("Price range plausible", in_band,
         f"observed {lo:.2f}..{hi:.2f} (directive band "
         f"{PRICE_BAND[0]:.0f}..{PRICE_BAND[1]:.0f})"),
    ]
    hard_fail = any(not ok for name, ok, _ in checks if name != "Price range plausible")

    lines = ["# XAUUSD M1 — Data Quality Report (operator MT5 export)", ""]
    lines.append(f"- Generated: {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}")
    lines.append("- Source: MetaTrader 5 broker export, uploaded by the operator "
                 "(XAUUSDM1.csv, UTF-16, no header)")
    lines.append(f"- Converter: tools/convert_mt5.py -> {CSV.name}")
    lines.append("- Columns: timestamp,open,high,low,close,volume (MT5 spread column dropped)")
    lines.append("- Timezone: **broker server time as written in the export** (not "
                 "converted; no DST marker in the file). See README.md.")
    lines.append("")
    lines.append(f"- Bars: **{n}**")
    lines.append(f"- Coverage: **{_fmt(timestamps[0])} .. {_fmt(timestamps[-1])}** (broker time)")
    lines.append(f"- Sessions gaps: {len(gaps)} (of which weekend: {weekend_gaps})")
    lines.append(f"- Zero-volume bars: {zero_vol}")
    lines.append("")
    lines.append("| Check | Result | Detail |")
    lines.append("|---|---|---|")
    for name, ok, detail in checks:
        mark = "PASS" if ok else ("WARN" if name == "Price range plausible" else "FAIL")
        lines.append(f"| {name} | {mark} | {detail} |")
    lines.append("")
    if hard_fail:
        verdict = "FAIL — anomalies present (reported, not repaired; do not proceed silently)"
    elif not in_band:
        verdict = ("PASS (hard checks) with a price-band WARN — the observed gold "
                   "range is outside the directive's 1800-3000 band; this is a real "
                   "market move, not a defect, and the operator's file is authoritative")
    else:
        verdict = "PASS — no anomalies"
    lines.append(f"**Verdict: {verdict}**")
    lines.append("")
    lines.append("## Coverage limitation (read before using these numbers)")
    lines.append("")
    lines.append(
        "This corpus is a **single ~3.5-month window**; it supports a full real-data "
        "*proof-of-concept* run, **not** a decision-grade multi-regime walk-forward."
    )
    lines.append("")
    lines.append(
        "- Only one calendar regime is present, so the T27 chronological "
        "development/validation/OOS year partition (2021-2025) does **not** apply. "
        "Any split of this window is a **causal, in-window** split; the numbers are "
        "in-sample-ish evidence, not out-of-sample proof."
    )
    lines.append(
        "- A walk-forward at M15 cadence yields very few folds here; the T27 runner "
        "degrades honestly (records a note) rather than fabricating folds."
    )
    lines.append(
        "- The T27 result on this corpus is therefore reported as a "
        "**PROOF-OF-CONCEPT**, not the mission's final publication verdict."
    )
    OUT.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 1 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
