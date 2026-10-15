#!/usr/bin/env python3
"""The pre-specified rule test: one rule, fixed before looking, gated honestly.

Plan A and Plan B both pointed the same way — the rules are least bad on
high-volatility, post-news, trend-aligned bars, and almost all of the loss sits
in the quiet window. This script fixes *one* rule that combines those leads and
asks the gate for a verdict, with no search over thresholds.

Because the rule's subset is small (~200 trades), the gate will most likely say
INSUFFICIENT rather than PROMOTE or REJECT. "Not enough evidence" is a real
answer, but it is unsatisfying, so this script adds the test the gate cannot:
a **permutation test** on the difference in R/trade between the rule's trades
and the rest. That answers "is the difference real?" independently of whether
the sample is big enough to clear a break-even bar — and it is the right tool
when the gate's honest verdict is "cannot say".

Run: python3 research/features_real/rule_test.py
"""

from __future__ import annotations

import argparse
import bisect
import importlib.util
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
for extra in (os.path.join(REPO, "tracker"), os.path.join(REPO, "akoan"),
              os.path.join(REPO, "advisor"), HERE):
    if extra in sys.path:
        sys.path.remove(extra)
    sys.path.insert(0, extra)

import validation_gate as gate  # noqa: E402


def _load(name, rel):
    path = os.path.join(REPO, rel)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


news_state = _load("akoan_news_state", os.path.join("akoan", "state.py"))
news_study = _load("news_study_mod",
                   os.path.join("research", "features_real", "news_study.py"))

DEFAULT_STORE = os.path.join(HERE, "feature_store.jsonl")
DEFAULT_LEDGER = os.path.join(HERE, "news_ledger.jsonl.gz")

# The rule, fixed here before any number is read. 2h = the "after a release"
# window the Plan B study measured as positive and robust; ALIGNED = the Plan A
# H4 thread. Neither threshold is tuned.
POST_NEWS_MIN = 120
SEED = 20241015


def the_rule(r: dict) -> bool:
    """THE pre-specified rule: trade only H4-aligned bars within 2h of a release."""
    return r.get("h4_align") == "ALIGNED" and bool(r.get("recent_release"))


def comparator_rules() -> list[tuple[str, object]]:
    """The two simpler leads, for context — each still pre-specified, not searched."""
    return [
        ("H4-aligned only", lambda r: r.get("h4_align") == "ALIGNED"),
        ("post-news only (<=2h)", lambda r: bool(r.get("recent_release"))),
    ]


def r_per_trade(rows: list[dict]) -> float:
    rs = [r["r_net"] for r in rows if r.get("r_net") is not None]
    return (sum(rs) / len(rs)) if rs else 0.0


def permutation_p(rows: list[dict], pred, n_perm: int = 20000,
                  seed: int = SEED) -> tuple[float, float]:
    """Two-sided permutation p-value for (mean R/trade | rule) - (| rest).

    Pools the trades, shuffles the rule label, and counts how often a random
    split of the same size produces a difference at least as large. This makes
    no distributional assumption and does not care that the sample is small —
    it only asks whether the observed gap is more than the split could produce
    by chance.
    """
    inside = [r for r in rows if pred(r)]
    outside = [r for r in rows if not pred(r)]
    if not inside or not outside:
        return (1.0, 0.0)
    observed = r_per_trade(inside) - r_per_trade(outside)

    pool = [r["r_net"] for r in rows if r.get("r_net") is not None]
    k = len(inside)
    rng = random.Random(seed)
    hits = 0
    for _ in range(n_perm):
        rng.shuffle(pool)
        diff = (sum(pool[:k]) / k) - (sum(pool[k:]) / (len(pool) - k))
        if abs(diff) >= abs(observed):
            hits += 1
    return ((hits + 1) / (n_perm + 1), observed)


def report(store: list[dict], name: str, pred, split_year: int, min_n: int) -> None:
    subset = [r for r in store if pred(r)]
    is_rows, oos_rows = gate.split_rows(subset, split_year)
    verdict = gate.gate(gate.evaluate(is_rows), gate.evaluate(oos_rows), min_n=min_n)
    p, diff = permutation_p(store, pred)
    allhalf = gate.evaluate(subset)

    print("\n" + "=" * 88)
    print("RULE: %s" % name)
    print("=" * 88)
    print("  trades taken : %d of %d  (%.1f%% of the book)"
          % (len(subset), len(store), 100.0 * len(subset) / len(store)))
    print("  all years    : n=%d win=%s R=%+.1f R/trade=%+.3f"
          % (allhalf["n"],
             ("%.1f%%" % (100 * allhalf["win_rate"])) if allhalf["win_rate"] else "-",
             allhalf["total_r"], allhalf["r_per_trade"]))
    print("  in-sample    : n=%d R=%+.1f R/trade=%+.3f"
          % (is_rows and len(is_rows), verdict["in_sample"]["total_r"],
             verdict["in_sample"]["r_per_trade"]))
    print("  out-of-sample: n=%d R=%+.1f R/trade=%+.3f"
          % (len(oos_rows), verdict["out_of_sample"]["total_r"],
             verdict["out_of_sample"]["r_per_trade"]))
    print("  permutation  : diff R/trade = %+.3f, two-sided p = %.4f" % (diff, p))
    print("  gate verdict : %s" % verdict["verdict"])
    for reason in verdict["reasons"][:2]:
        print("    - %s" % reason)


def robustness(store: list[dict], events: list[dict]) -> None:
    """Two checks the gate cannot give: does the rule hold every year, and does
    it hold across the window that defines "after a release"?
    """
    print("\n" + "-" * 88)
    print("ROBUSTNESS of THE RULE")
    print("-" * 88)
    print("  by year:")
    years = sorted({int(r["year"]) for r in store if r.get("year")})
    for y in years:
        rows = [r for r in store if r.get("year") and int(r["year"]) == y and the_rule(r)]
        if not rows:
            continue
        h = gate.evaluate(rows)
        print("    %s  n=%-4d R=%+8.1f  R/trade=%+.3f"
              % (y, len(rows), h["total_r"], h["r_per_trade"]))

    # Window sweep: "after a release" is the only threshold in the rule.
    events = sorted(events, key=lambda e: e["at_utc"])
    times = [e["at_utc"] for e in events]
    near = {id(r): events[bisect.bisect_left(times, r["time"] - 6 * 3600):
                         bisect.bisect_right(times, r["time"] + 2 * 3600)]
            for r in store}
    print("  by post-news window:")
    for minutes in (30, 60, 90, 120, 180, 240):
        rows = []
        for r in store:
            if r.get("h4_align") != "ALIGNED":
                continue
            st = news_state.state_at(near[id(r)], r["time"], recent_min=minutes)
            if st["recent_release"]:
                rows.append(r)
        h = gate.evaluate(rows)
        print("    <=%3d min  n=%-4d R=%+8.1f  R/trade=%+.3f"
              % (minutes, len(rows), h["total_r"], h["r_per_trade"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-specified rule test")
    parser.add_argument("--store", default=DEFAULT_STORE)
    parser.add_argument("--ledger", default=DEFAULT_LEDGER)
    parser.add_argument("--split-year", type=int, default=2024)
    parser.add_argument("--min-n", type=int, default=gate.DEFAULT_MIN_N)
    args = parser.parse_args(argv)

    store = news_study.load_jsonl(args.store)
    events = news_study.load_jsonl(args.ledger)
    news_study.stamp(store, events)

    base = gate.evaluate(store)
    print("baseline book: %d trades, win=%s, R=%+.1f, R/trade=%+.3f"
          % (len(store),
             ("%.1f%%" % (100 * base["win_rate"])) if base["win_rate"] else "-",
             base["total_r"], base["r_per_trade"]))

    report(store, "THE RULE: H4-aligned AND within %d min after a release"
           % POST_NEWS_MIN, the_rule, args.split_year, args.min_n)
    for name, pred in comparator_rules():
        report(store, name, pred, args.split_year, args.min_n)

    robustness(store, events)

    print("\n" + "=" * 88)
    print("Read the p-value with the count of rules tested in mind: three rules "
          "here,\nand a p below ~0.017 would survive a Bonferroni correction at 5%.")
    print("=" * 88)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
