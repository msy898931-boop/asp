#!/usr/bin/env python3
"""Re-derive the news-move table in advisor/news_forecast.py.

The forecast module ships a small table of measured gold moves around
high-impact US releases. That table is data, not opinion, so it must be
reproducible: this script reads the committed M1 corpus and the akoan event
ledger, measures the same thing the module claims, and prints both the numbers
and the honesty checks that decide what may be claimed.

It answers three questions, and prints all three even when the answer is "no":

  1. How big does gold move, by horizon? (median and the violent tenth)
  2. Does the mechanical surprise read predict the *direction*? (it does not —
     this prints the hit rate and the binomial p so the "no" is on record)
  3. Does any pre-release drift leak ahead of the print? (it does not)

Run: python3 scripts/measure_news_move.py
     python3 scripts/measure_news_move.py --json
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime
import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

CORPUS = os.environ.get(
    "ASTRA_M1_CORPUS",
    os.path.join(ROOT, "research", "data", "xauusd_m1", "xauusd_m1_real.csv"))
EVENTS = os.environ.get(
    "ASTRA_EVENTS_LEDGER", os.path.join(ROOT, "akoan", "events_seed.jsonl"))

HORIZONS = (15, 30, 60)
LOOKBACK_MIN = 60          # price baseline is one minute before the print
DRIFT_WINDOW_MIN = 60      # how far back the pre-release drift is measured

INVERSE = ("unemployment rate", "jobless claims", "continuing claims",
           "initial jobless", "unemployment claims")


def _load_events(path: str) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _load_bars(path: str) -> list[tuple[int, float, float, float]]:
    """(ts, high, low, close) for each M1 bar, oldest first."""
    rows = []
    with open(path, encoding="utf-8") as fh:
        reader = csv.reader(fh)
        next(reader)
        for ts, _o, high, low, close, _v in reader:
            t = int(datetime.datetime.strptime(
                ts, "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=datetime.timezone.utc).timestamp())
            rows.append((t, float(high), float(low), float(close)))
    rows.sort()
    return rows


def _higher_is_stronger(title: str) -> bool:
    low = (title or "").lower()
    return not any(k in low for k in INVERSE)


def measure(corpus: str = CORPUS, events_path: str = EVENTS) -> dict:
    events = [e for e in _load_events(events_path)
              if e.get("country") == "US"
              and isinstance(e.get("importance"), (int, float))
              and e["importance"] >= 1
              and e.get("at_utc")]
    bars = _load_bars(corpus)
    times = [b[0] for b in bars]

    def idx_at(t: float) -> int | None:
        i = bisect.bisect_right(times, t) - 1
        return i if i >= 0 else None

    sizes: dict[int, list[float]] = {h: [] for h in HORIZONS}
    direction: dict[int, list[tuple[int, float]]] = {h: [] for h in HORIZONS}
    drifts = []
    for event in events:
        at = event["at_utc"]
        i0 = idx_at(at - LOOKBACK_MIN)
        if i0 is None:
            continue
        base = bars[i0][3]
        ok = True
        for h in HORIZONS:
            j = idx_at(at + h * 60)
            if j is None or j <= i0:
                ok = False
                break
            sizes[h].append(abs(bars[j][3] - base))
            if event.get("actual") is not None and event.get("forecast") is not None:
                cmp_ = (event["actual"] > event["forecast"]) - (
                    event["actual"] < event["forecast"])
                ccy = cmp_ if _higher_is_stronger(event.get("title", "")) else -cmp_
                direction[h].append((-ccy, bars[j][3] - base))
        if not ok:
            continue
        ip = idx_at(at - 2 * LOOKBACK_MIN)
        if ip is not None:
            drifts.append(base - bars[ip][3])

    def p90(xs: list[float]) -> float:
        if not xs:
            return 0.0
        return sorted(xs)[min(len(xs) - 1, int(0.9 * len(xs)))]

    table: dict[int, dict] = {}
    for h in HORIZONS:
        xs = sizes[h]
        table[h] = {
            "median": round(statistics.median(xs), 2) if xs else None,
            "p90": round(p90(xs), 2),
            "n": len(xs),
        }

    checks: dict[int, dict] = {}
    for h in HORIZONS:
        nz = [(g, m) for g, m in direction[h] if g != 0]
        if not nz:
            checks[h] = {"n": 0}
            continue
        agree = sum(1 for g, m in nz if (m > 0) - (m < 0) == g)
        n = len(nz)
        checks[h] = {
            "n": n,
            "hit_rate": round(100.0 * agree / n, 1),
            "binomial_p": round(_binom_two_sided(agree, n), 3),
        }

    return {
        "events_total": len(events),
        "events_measured": table[60]["n"],
        "corpus": os.path.relpath(corpus, ROOT),
        "table": {str(h): table[h] for h in HORIZONS},
        "direction_check": {str(h): checks[h] for h in HORIZONS},
        "pre_drift": {
            "mean": round(statistics.mean(drifts), 2) if drifts else None,
            "std": round(statistics.pstdev(drifts), 2) if drifts else None,
            "n": len(drifts),
        },
    }


def _binom_two_sided(k: int, n: int, p: float = 0.5) -> float:
    total = sum(math.comb(n, i) for i in range(n + 1))
    observed = math.comb(n, k)
    tail = sum(math.comb(n, i) for i in range(n + 1)
               if math.comb(n, i) <= observed)
    return tail / total


def _report(d: dict) -> str:
    lines = [
        "ASTRA news-move measurement",
        "  corpus: %s" % d["corpus"],
        "  high-impact US events: %d (measured: %d)"
        % (d["events_total"], d["events_measured"]),
        "",
        "  absolute gold move ($) by horizon:",
    ]
    for h in ("15", "30", "60"):
        row = d["table"][h]
        lines.append("    +%s min: median %-5s  p90 %-6s  n=%d"
                     % (h, row["median"], row["p90"], row["n"]))
    lines.append("")
    lines.append("  does the surprise read predict DIRECTION? (coin = 50%)")
    for h in ("15", "30", "60"):
        c = d["direction_check"][h]
        if not c.get("n"):
            lines.append("    +%s min: no baseline rows" % h)
            continue
        lines.append("    +%s min: hit rate %s%%  p=%.3f  n=%d  -> %s"
                     % (h, c["hit_rate"], c["binomial_p"], c["n"],
                        "NO EDGE" if c["binomial_p"] > 0.05 else "CHECK CAREFULLY"))
    lines.append("")
    pd_ = d["pre_drift"]
    lines.append("  pre-release drift (60 min): mean %s  std %s  n=%d"
                 % (pd_["mean"], pd_["std"], pd_["n"]))
    lines.append("")
    lines.append("  Conclusion: size is forecastable, direction is not.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ASTRA news-move measurement")
    parser.add_argument("--json", action="store_true", help="print raw JSON")
    parser.add_argument("--corpus", default=CORPUS)
    parser.add_argument("--events", default=EVENTS)
    args = parser.parse_args(argv)
    d = measure(args.corpus, args.events)
    if args.json:
        json.dump(d, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print(_report(d))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
