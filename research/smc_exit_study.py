"""Does a nearer target turn the SMC zones' reading into a measured edge?

The committed study (research/smc_zone_study.py) builds each plan with targets
at 2R and 3R and finds the zones do not clear break-even: the win rate is 21-24%
against a break-even of 25-28%. But "win" there means the *final* target, and a
zone is a place price reacts, not a place it travels from. So the question this
probe asks is narrower and more honest: at which target does the reading clear
break-even, and does it hold out of sample?

It resolves the *same* deterministic plans through the *same* resolver
(advisor/smc_resolve.py, cost 0.30 USD, stop wins ties), changing only the last
target. Every variant is charged the spread, and the report is split
in-sample (2021-2024) / out-of-sample (2025) exactly as tracker/validation_gate
does, so the honest end of the Wilson interval decides — not the point estimate.

Overlapping trades: the plan is rebuilt every 8 M15 bars and held up to 32, so
consecutive trades share tape and are not independent. The Wilson interval
assumes independence and is therefore too narrow; treat it as a floor on the
uncertainty, not the whole of it. The out-of-sample half is the check that
matters, because it was not used to pick the target.

Run:  python3 research/smc_exit_study.py
      python3 research/smc_exit_study.py --years 2021,2022,2023,2024,2025
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Dict, List, Optional, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "advisor"))
sys.path.insert(0, HERE)

import smc_read  # noqa: E402
import smc_resolve  # noqa: E402
import smc_zone_study as study  # noqa: E402

DATA_DIR = study.DATA_DIR
REPORT_DIR = study.REPORT_DIR
OOS_YEAR = study.OOS_YEAR
COST_USD = study.COST_USD
FRAMES = study.FRAMES
FRAME_WINDOW = study.FRAME_WINDOW
TF = study.TF

# The exit multiple each variant asks for, in R. 2R/3R is what ships today; the
# nearer ones are the hypothesis. 0.5R is included because the MFE probe put the
# largest gap there, not because it is a good trade on its own.
TARGET_R = (0.5, 0.75, 1.0, 1.5, 2.0, 3.0)


def replan(plan: dict, target_r: float) -> dict:
    """The same plan, stopped at ``target_r`` R instead of at 2R/3R.

    Direction, entry and stop are untouched — only the final target moves, so
    the comparison isolates the exit and nothing else. Targets are ascending for
    a LONG and descending for a SHORT, as validate_plan requires.
    """
    risk = abs(plan["entry"] - plan["stop"])
    sign = 1.0 if plan["direction"] == "LONG" else -1.0
    out = dict(plan)
    out["targets"] = [round(plan["entry"] + sign * target_r * risk, 2)]
    return out


def evaluate_year(m1: Sequence[dict], year: int) -> Dict[str, List[dict]]:
    """Resolve every plan of the year once per exit variant."""
    out: Dict[str, List[dict]] = {("%.2fR" % t): [] for t in TARGET_R}
    bars_by_tf = {f: study.resample(m1, mm) for f, mm in FRAMES.items()}
    if TF not in bars_by_tf:
        return out
    decision = bars_by_tf[TF]
    window_len = FRAME_WINDOW.get(TF, study.WINDOW)
    step = study.frame_step(TF)
    if len(decision) < window_len + 40:
        return out

    for i in range(window_len, len(decision) - 40, step):
        cutoff = decision[i - 1]["time"]
        sheet_bars = {f: study._slice_at(bars, cutoff, FRAME_WINDOW[f])
                      for f, bars in bars_by_tf.items()}
        sheet = smc_read.build(sheet_bars, tuple(FRAMES))
        plan = study.build_plan(sheet, TF)
        if plan is None:
            continue
        forward = decision[i:]
        for target_r in TARGET_R:
            res = smc_resolve.resolve_plan(replan(plan, target_r), forward,
                                           generated_at=cutoff, cost_usd=COST_USD)
            res["year"] = year
            res["target_r"] = target_r
            out["%.2fR" % target_r].append(res)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Which SMC exit clears break-even?")
    parser.add_argument("--years", default="2021,2022,2023,2024,2025")
    parser.add_argument("--limit", type=int, default=400000)
    args = parser.parse_args(argv)

    all_rows: Dict[str, List[dict]] = {("%.2fR" % t): [] for t in TARGET_R}
    for year in [int(y) for y in args.years.split(",") if y.strip()]:
        path = os.path.join(DATA_DIR, "%d.csv.gz" % year)
        if not os.path.exists(path):
            print("skip %d: not found" % year)
            continue
        m1 = study.load_m1(path, limit=args.limit)
        rows = evaluate_year(m1, year)
        for key in all_rows:
            all_rows[key].extend(rows[key])
        print("%d: %d M1 bars -> %d zone plans" % (year, len(m1), len(rows["1.00R"])))

    report = {"tf": TF, "cost_usd": COST_USD, "oos_year": OOS_YEAR,
              "years": args.years, "variants": {}}
    print("\n" + "=" * 84)
    print("%-8s %-16s %7s %8s %9s %9s %9s"
          % ("target", "half", "scored", "win", "wilson_lo", "break_even", "R/trade"))
    for target_r in TARGET_R:
        key = "%.2fR" % target_r
        rows = all_rows[key]
        if not rows:
            continue
        is_rows = [r for r in rows if r["year"] < OOS_YEAR]
        oos_rows = [r for r in rows if r["year"] >= OOS_YEAR]
        report["variants"][key] = {
            "all": smc_resolve.summarize(rows),
            "in_sample": smc_resolve.summarize(is_rows),
            "out_of_sample": smc_resolve.summarize(oos_rows),
        }
        for label, s in (("in-sample", smc_resolve.summarize(is_rows)),
                         ("out-of-sample", smc_resolve.summarize(oos_rows))):
            if not s.get("scored"):
                continue
            clears = s["wilson_low"] > s["break_even"]
            print("%-8s %-16s %7d %8.3f %9.3f %9.3f %+9.4f  %s"
                  % (key, label, s["scored"], s["win_rate"], s["wilson_low"],
                     s["break_even"], s["r_per_trade"],
                     "CLEARS" if clears else ""))

    os.makedirs(REPORT_DIR, exist_ok=True)
    out_path = os.path.join(REPORT_DIR, "smc_exit_study.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
    print("\nwrote %s" % out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
