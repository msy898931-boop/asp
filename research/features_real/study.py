#!/usr/bin/env python3
"""Study the feature store: which conditions, if any, actually clear the gate.

This is the "learn from mistakes" step made honest. It does not tune anything
and it does not pick a threshold that looks good — it takes the trades the
engine already took, groups them by the context they opened under, and asks of
each group the one question that decides whether it may touch live:

    does the win rate clear break-even, in-sample AND out-of-sample, by the
    pessimistic end of the Wilson interval?

Every group is reported whether it passes or fails, with the specific reason,
so a promising-looking bucket that is really noise is visible as noise.

Run: python3 research/features_real/study.py [--split-year 2024] [--min-n 100]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
for extra in (os.path.join(REPO, "tracker"),):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import validation_gate as gate  # noqa: E402

DEFAULT_STORE = os.path.join(HERE, "feature_store.jsonl")


def load_store(path: str) -> list[dict]:
    rows: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def candidates() -> list[tuple[str, object]]:
    """The filters worth testing, as (name, predicate) pairs.

    Each is a hypothesis the prior work raised, not a search: the point is to
    confirm or kill them on out-of-sample data, not to mine combinations until
    one passes.
    """
    return [
        ("all (baseline)", lambda r: True),
        ("H4 aligned", lambda r: r.get("h4_align") == "ALIGNED"),
        ("H4 against", lambda r: r.get("h4_align") == "AGAINST"),
        ("regime: TRENDING_UP", lambda r: r.get("regime") == "TRENDING_UP"),
        ("regime: TRENDING_DOWN", lambda r: r.get("regime") == "TRENDING_DOWN"),
        ("regime: RANGING", lambda r: r.get("regime") == "RANGING"),
        ("regime: QUIET", lambda r: r.get("regime") == "QUIET"),
        ("session: LONDON", lambda r: r.get("session") == "LONDON"),
        ("session: LONDON_NY", lambda r: r.get("session") == "LONDON_NY"),
        ("session: NY", lambda r: r.get("session") == "NY"),
        ("session: ASIA_LATE", lambda r: r.get("session") == "ASIA_LATE"),
        ("LONG only", lambda r: r.get("direction") == "LONG"),
        ("SHORT only", lambda r: r.get("direction") == "SHORT"),
        ("weekday: TUE", lambda r: r.get("weekday") == "TUE"),
        ("weekday: WED", lambda r: r.get("weekday") == "WED"),
        ("weekday: THU", lambda r: r.get("weekday") == "THU"),
        ("high vol (atr_pct>=med)", None),      # filled in by main
        ("aligned + TRENDING", lambda r: (r.get("h4_align") == "ALIGNED"
                                          and r.get("regime", "").startswith("TRENDING"))),
    ]


def median(values: list[float]) -> float:
    s = sorted(values)
    if not s:
        return 0.0
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def fmt_half(h: dict) -> str:
    if h["n"] == 0:
        return "n=0"
    wr = "%5.1f%%" % (100.0 * (h["win_rate"] or 0.0))
    lo = "%5.1f%%" % (100.0 * (h["wilson_low"] or 0.0))
    be = ("%5.1f%%" % (100.0 * h["break_even"])) if h["break_even"] is not None else "  n/a"
    return "n=%-4d win=%s wlo=%s be=%s R=%+8.1f" % (
        h["n"], wr, lo, be, h["total_r"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Feature-store study")
    parser.add_argument("--store", default=DEFAULT_STORE)
    parser.add_argument("--split-year", type=int, default=2024,
                        help="years < this are in-sample; >= are out-of-sample")
    parser.add_argument("--min-n", type=int, default=gate.DEFAULT_MIN_N)
    args = parser.parse_args(argv)

    rows = load_store(args.store)
    if not rows:
        sys.stderr.write("[study] empty store: %s\n" % args.store)
        return 1

    med_atr = median([r["atr_pct"] for r in rows if r.get("atr_pct")])

    defs = candidates()
    resolved_defs: list[tuple[str, object]] = []
    for name, pred in defs:
        if pred is None:
            resolved_defs.append(
                (name, lambda r, m=med_atr: (r.get("atr_pct") or 0.0) >= m))
        else:
            resolved_defs.append((name, pred))

    print("=" * 92)
    print("ASTRA feature-store study — split at %d (IS < %d <= OOS), min_n=%d"
          % (args.split_year, args.split_year, args.min_n))
    print("store: %d resolved trades | median atr_pct=%.5f" % (len(rows), med_atr))
    print("=" * 92)

    accepted: list[str] = []
    for name, pred in resolved_defs:
        subset = [r for r in rows if pred(r)]
        is_rows, oos_rows = gate.split_rows(subset, args.split_year)
        verdict = gate.gate(gate.evaluate(is_rows), gate.evaluate(oos_rows),
                            min_n=args.min_n)
        mark = {"PROMOTE": "PASS", "REJECT": "fail", "INSUFFICIENT": "thin"}[
            verdict["verdict"]]
        print("\n[%s] %s" % (mark, name))
        print("    IS : %s" % fmt_half(verdict["in_sample"]))
        print("    OOS: %s" % fmt_half(verdict["out_of_sample"]))
        if verdict["verdict"] == "PROMOTE":
            accepted.append(name)
        for reason in verdict["reasons"][:2]:
            print("    - %s" % reason)

    print("\n" + "=" * 92)
    if accepted:
        print("CLEARED THE GATE: %s" % ", ".join(accepted))
    else:
        print("CLEARED THE GATE: none. No tested condition beats break-even "
              "out-of-sample at the Wilson lower bound.")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
