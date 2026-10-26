#!/usr/bin/env python3
"""Does the SMC fact sheet's zones have a measured edge? Answer it with numbers.

The SMC engine ships an opinionated reading — order blocks, fair-value gaps,
structure — and until now nothing ever checked whether those zones predict
anything. This study closes that gap. It builds plans *deterministically from the
fact sheet* (never from a model), resolves them with ``advisor/smc_resolve``, and
reports the honest result: win rate, Wilson lower bound, net R, break-even.

Three questions, three sections:

  1. **Is the ruler straight?** A control of symmetric 1R/1R plans entered at the
     market must resolve near 50%. If it does not, the resolver is biased and
     nothing else here can be trusted.
  2. **Do the zones have an edge?** Deterministic plans from order blocks and
     fair-value gaps, resolved forward, split in-sample (2021-2024) and
     out-of-sample (2025) exactly as the tracker's gate does.
  3. **Does confluence help?** The same plans, filtered to those whose direction
     agrees with the multi-timeframe confluence tally. If the filter does not
     improve the out-of-sample half, it is not an improvement.

Method is causal throughout: each plan sees only closed bars up to its decision
bar, and is resolved against bars strictly after it. Stdlib only, no network.

Run:
    python3 research/smc_zone_study.py
    python3 research/smc_zone_study.py --years 2021,2022,2023,2024,2025
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import sys
from typing import Dict, List, Optional, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "advisor"))

import smc_read  # noqa: E402
import smc_resolve  # noqa: E402

DATA_DIR = os.path.join(REPO_ROOT, "research", "data", "xauusd_m1")
REPORT_DIR = os.path.join(HERE, "reports")

OOS_YEAR = 2025          # the year the tracker's gate holds out
WINDOW = 220             # M15 bars of history each fact sheet sees
STEP = 8                 # evaluate every 8th bar (a two-hourly cadence)
COST_USD = 0.30          # round-trip spread, matching the tracker
TF = "M15"
# The frames the confluence tally reads, and how many bars of each the sheet
# sees. Higher frames need fewer bars for the same span, so the cost stays sane.
FRAMES = {"M15": 15, "M30": 30, "H1": 60, "H4": 240}
FRAME_WINDOW = {"M15": WINDOW, "M30": 150, "H1": 150, "H4": 120}


# ── data ─────────────────────────────────────────────────────────────────

def load_m1(path: str, limit: int = 400000) -> List[dict]:
    """M1 bars from a Dukascopy-style CSV (epoch-ms timestamp, OHLCV)."""
    opener = gzip.open if path.endswith(".gz") else open
    out: List[dict] = []
    with opener(path, "rt") as fh:
        for i, row in enumerate(csv.DictReader(fh)):
            if i >= limit:
                break
            try:
                ts = int(row["timestamp"])
                if ts > 10 ** 11:
                    ts //= 1000
                out.append({"time": ts, "open": float(row["open"]),
                            "high": float(row["high"]), "low": float(row["low"]),
                            "close": float(row["close"])})
            except (ValueError, KeyError):
                continue
    return out


def resample(m1: Sequence[dict], minutes: int) -> List[dict]:
    """Aggregate M1 bars into ``minutes`` bars, left-labelled by open time."""
    step = minutes * 60
    buckets: Dict[int, dict] = {}
    order: List[int] = []
    for x in m1:
        key = x["time"] - (x["time"] % step)
        bar = buckets.get(key)
        if bar is None:
            buckets[key] = {"time": key, "open": x["open"], "high": x["high"],
                            "low": x["low"], "close": x["close"]}
            order.append(key)
        else:
            bar["high"] = max(bar["high"], x["high"])
            bar["low"] = min(bar["low"], x["low"])
            bar["close"] = x["close"]
    return [buckets[k] for k in order]


# ── the deterministic plan builder (no model, ever) ──────────────────────

def build_plan(sheet: dict, tf: str = TF) -> Optional[dict]:
    """A plan built from the fact sheet alone: enter the nearest zone.

    The direction comes from the frame's structure bias; the entry is the
    midpoint of the nearest order block or fair-value gap on the correct side of
    price; the stop sits one ATR behind the zone; the targets are 2R and 3R.
    Returns None when the sheet offers no qualifying zone. This is the honest
    test of the *zones*, isolated from any model's judgement.
    """
    facts = (sheet.get("timeframes") or {}).get(tf)
    if not facts:
        return None
    close = facts.get("close")
    atr = facts.get("atr")
    bias = (facts.get("structure") or {}).get("bias")
    if close is None or atr is None or atr <= 0:
        return None
    if bias not in ("bullish", "bearish"):
        return None

    zones: List[tuple] = []
    for ob in facts.get("order_blocks") or []:
        zones.append((ob["low"], ob["high"], "OB"))
    for f in facts.get("fair_value_gaps") or []:
        zones.append((f["low"], f["high"], "FVG"))
    if not zones:
        return None

    if bias == "bullish":
        below = [z for z in zones if z[1] < close]
        if not below:
            return None
        lo, hi, kind = max(below, key=lambda z: z[1])
        entry = (lo + hi) / 2.0
        stop = lo - atr
        if stop >= entry:
            return None
        risk = entry - stop
        return {"direction": "LONG", "entry": round(entry, 2),
                "stop": round(stop, 2),
                "targets": [round(entry + 2.0 * risk, 2),
                            round(entry + 3.0 * risk, 2)],
                "confidence": 0.5, "zone": kind}

    above = [z for z in zones if z[0] > close]
    if not above:
        return None
    lo, hi, kind = min(above, key=lambda z: z[0])
    entry = (lo + hi) / 2.0
    stop = hi + atr
    if stop <= entry:
        return None
    risk = stop - entry
    return {"direction": "SHORT", "entry": round(entry, 2),
            "stop": round(stop, 2),
            "targets": [round(entry - 2.0 * risk, 2),
                        round(entry - 3.0 * risk, 2)],
            "confidence": 0.5, "zone": kind}


def control_plan(close: float, atr: float, direction: str) -> dict:
    """A symmetric 1R/1R plan entered at the market. The bias control."""
    if direction == "LONG":
        return {"direction": "LONG", "entry": close, "stop": close - atr,
                "targets": [close + atr]}
    return {"direction": "SHORT", "entry": close, "stop": close + atr,
            "targets": [close - atr]}


# ── one year of evaluation ───────────────────────────────────────────────

def _slice_at(bars: Sequence[dict], cutoff_time: float,
              count: int) -> List[dict]:
    """The last ``count`` bars that closed at or before ``cutoff_time``.

    Causal by construction: a higher frame contributes only bars fully closed
    by the moment the M15 decision is made.
    """
    eligible = [b for b in bars if b["time"] <= cutoff_time]
    return eligible[-count:]


def evaluate_year(m1: Sequence[dict], year: int) -> Dict[str, List[dict]]:
    """Resolve every plan this year's tape offers. Returns rows by strategy.

    Keys: ``smc`` (zone plans), ``confluence`` (zone plans whose direction
    agrees with the *multi-frame* tally), ``control`` (symmetric 1R/1R). The
    sheet is built from four frames so the confluence tally carries real
    information rather than echoing a single frame's bias.
    """
    out: Dict[str, List[dict]] = {"smc": [], "confluence": [], "control": []}
    bars_by_tf = {tf: resample(m1, minutes)
                  for tf, minutes in FRAMES.items()}
    m15 = bars_by_tf["M15"]
    if len(m15) < WINDOW + 40:
        return out

    for i in range(WINDOW, len(m15) - 40, STEP):
        cutoff = m15[i - 1]["time"]      # the last closed M15 bar
        window = m15[max(0, i - WINDOW):i]
        generated_at = cutoff
        forward = m15[i:]

        sheet_bars = {tf: _slice_at(bars, cutoff, FRAME_WINDOW[tf])
                      for tf, bars in bars_by_tf.items()}
        sheet = smc_read.build(sheet_bars, tuple(FRAMES))
        plan = build_plan(sheet, TF)
        if plan is not None:
            res = smc_resolve.resolve_plan(plan, forward,
                                           generated_at=generated_at,
                                           cost_usd=COST_USD)
            res["year"] = year
            res["zone"] = plan.get("zone")
            out["smc"].append(res)

            confluence = sheet.get("confluence") or {}
            dominant = confluence.get("dominant")
            agree = ((dominant == "bullish" and plan["direction"] == "LONG")
                     or (dominant == "bearish" and plan["direction"] == "SHORT"))
            if agree:
                out["confluence"].append(dict(res))

        close = window[-1]["close"]
        atr = smc_read.atr(window)
        if atr and atr > 0:
            for direction in ("LONG", "SHORT"):
                cp = control_plan(close, atr, direction)
                cres = smc_resolve.resolve_plan(cp, forward,
                                                generated_at=generated_at,
                                                cost_usd=COST_USD)
                cres["year"] = year
                out["control"].append(cres)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Measure the SMC zones' edge")
    parser.add_argument("--years", default="2021,2022,2023,2024,2025")
    parser.add_argument("--limit", type=int, default=400000,
                        help="M1 bars read per year file")
    args = parser.parse_args(argv)

    all_rows: Dict[str, List[dict]] = {"smc": [], "confluence": [], "control": []}
    for year in [int(y) for y in args.years.split(",") if y.strip()]:
        path = os.path.join(DATA_DIR, "%d.csv.gz" % year)
        if not os.path.exists(path):
            print("skip %d: %s not found" % (year, path))
            continue
        m1 = load_m1(path, limit=args.limit)
        rows = evaluate_year(m1, year)
        for key in all_rows:
            all_rows[key].extend(rows[key])
        print("%d: %d M1 bars -> %d zone plans"
              % (year, len(m1), len(rows["smc"])))

    report = {"tf": TF, "window": WINDOW, "step": STEP, "cost_usd": COST_USD,
              "oos_year": OOS_YEAR, "years": args.years, "strategies": {}}

    print("\n" + "=" * 74)
    for name in ("control", "smc", "confluence"):
        rows = all_rows[name]
        if not rows:
            continue
        in_sample = [r for r in rows if r["year"] < OOS_YEAR]
        oos = [r for r in rows if r["year"] >= OOS_YEAR]
        s_all = smc_resolve.summarize(rows)
        s_is = smc_resolve.summarize(in_sample)
        s_oos = smc_resolve.summarize(oos)
        report["strategies"][name] = {"all": s_all, "in_sample": s_is,
                                      "out_of_sample": s_oos}
        print("\n%s" % name.upper())
        for label, s in (("all", s_all), ("in-sample (2021-2024)", s_is),
                         ("out-of-sample (2025)", s_oos)):
            rate = ("%.3f" % s["win_rate"]) if s["win_rate"] is not None else "  -  "
            lo = ("%.3f" % s["wilson_low"]) if s["wilson_low"] is not None else "  -  "
            be = ("%.3f" % s["break_even"]) if s["break_even"] is not None else "  -  "
            print("  %-22s scored=%-5d win=%-6s wilson_lo=%-6s break_even=%-6s "
                  "R/trade=%+.4f total_R=%+.1f"
                  % (label, s["scored"], rate, lo, be, s["r_per_trade"],
                     s["total_r"]))

    os.makedirs(REPORT_DIR, exist_ok=True)
    out_path = os.path.join(REPORT_DIR, "smc_zone_study.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    print("\nreport written to %s" % out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
