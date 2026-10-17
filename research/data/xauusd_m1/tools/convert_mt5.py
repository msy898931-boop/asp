#!/usr/bin/env python3
"""Convert an MT5 XAUUSD M1 export to the canonical ASTRA M1 CSV.

The human uploads a raw MetaTrader 5 export (UTF-16, no header, columns
`<DATE> <TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<TICKVOL>,<SPREAD>`). This tool
turns it into the single canonical corpus file the rest of the pipeline
consumes:

    research/data/xauusd_m1/xauusd_m1_real.csv
    timestamp,open,high,low,close,volume

Requirements (Phase 5.2):
  * one timestamp column, ISO `YYYY-MM-DD HH:MM:SS`
  * sorted ascending, no duplicates (duplicates are reported, never silently
    dropped: ambiguity is surfaced, not hidden)
  * OHLC + volume only; the MT5 spread column is dropped

Timestamps are kept verbatim as the broker wrote them. The exporter does not
label its timezone and there is no DST marker in the file, so this tool does
NOT convert to UTC and never guesses an offset; the README documents the value
as broker server time and asks the operator to confirm it.

This tool never fabricates or repairs data.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # research/data/xauusd_m1
DEFAULT_SRC = Path("/workspace/asp/XAUUSDM1.csv")
DEFAULT_OUT = HERE / "xauusd_m1_real.csv"

# MT5 emits its folder glyph as a replacement char in some exports; accept a
# few separators and both MT5 date formats.
_DT_FORMATS = ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M",
               "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")


def _detect_encoding(path: Path) -> str:
    head = path.open("rb").read(4)
    if head[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "utf-16"
    if head[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    return "utf-8"


def _parse_time(text: str) -> dt.datetime:
    text = text.strip().strip('"')
    for fmt in _DT_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"unrecognised timestamp {text!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    if not args.src.exists():
        raise SystemExit(f"source not found: {args.src}")

    enc = _detect_encoding(args.src)
    rows: list[tuple[dt.datetime, float, float, float, float, int]] = []
    skipped: list[tuple[int, str]] = []
    with args.src.open(newline="", encoding=enc) as fh:
        raw_rows = csv.reader(fh)
        for n, cols in enumerate(raw_rows, 1):
            if not cols or (len(cols) == 1 and not cols[0].strip()):
                continue
            try:
                t = _parse_time(cols[0])
                o, h, l, c = (float(cols[i]) for i in (1, 2, 3, 4))
                v = round(float(cols[5])) if len(cols) > 5 else 0
            except (ValueError, IndexError) as exc:
                skipped.append((n, str(exc)))
                continue
            rows.append((t, o, h, l, c, v))

    rows.sort(key=lambda r: r[0])

    duplicates = []
    deduped = []
    seen: set[dt.datetime] = set()
    for row in rows:
        if row[0] in seen:
            duplicates.append(row[0])
            continue
        seen.add(row[0])
        deduped.append(row)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as out:
        out.write("timestamp,open,high,low,close,volume\n")
        for t, o, h, l, c, v in deduped:
            out.write(f"{t:%Y-%m-%d %H:%M:%S},{o:g},{h:g},{l:g},{c:g},{v}\n")

    print(f"source      : {args.src} ({enc})")
    print(f"parsed      : {len(rows)} rows")
    print(f"skipped     : {len(skipped)}")
    print(f"duplicates  : {len(duplicates)} (dropped, first kept)")
    print(f"written     : {len(deduped)} rows -> {args.out}")
    if deduped:
        print(f"range       : {deduped[0][0]} .. {deduped[-1][0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
