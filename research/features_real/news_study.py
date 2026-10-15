#!/usr/bin/env python3
"""News study: does the news context explain — or filter — the losses?

This joins the feature store (every trade the live rules took) with the
historical event ledger, stamps each trade with the causal news state from
`akoan/state.py`, and runs each news condition through the same promotion gate
the price study uses.

The point is not to find a news edge by searching. It is to answer, with an
out-of-sample test, the question the project could never answer before: does
trading *into* a high-impact release actually lose, and does the news read
carry information the price does not?

Run: python3 research/features_real/news_study.py [--split-year 2024]
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
# Insert HERE last so it sits at sys.path[0]: akoan/ also ships a `study.py`,
# and this module's own study.py must win the name.
for extra in (os.path.join(REPO, "tracker"), os.path.join(REPO, "akoan"),
              os.path.join(REPO, "advisor"), HERE):
    if extra in sys.path:
        sys.path.remove(extra)
    sys.path.insert(0, extra)

import importlib.util  # noqa: E402
import study  # noqa: E402
import validation_gate as gate  # noqa: E402


def _load_news_state():
    """Load akoan/state.py under a private name (the module is called `state`,
    which is too generic to import by name safely)."""
    path = os.path.join(REPO, "akoan", "state.py")
    spec = importlib.util.spec_from_file_location("akoan_news_state", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


news_state = _load_news_state()

DEFAULT_STORE = os.path.join(HERE, "feature_store.jsonl")
DEFAULT_LEDGER = os.path.join(HERE, "news_ledger.jsonl.gz")

# Only events this close to a trade can change its news state (the widest
# default window is RECENT_MIN = 120 min back, UPCOMING_MIN = 45 min forward).
_PAD_BACK = 6 * 3600
_PAD_FWD = 2 * 3600


def load_jsonl(path: str) -> list[dict]:
    """Read JSONL, transparently gunzipping a .gz path."""
    import gzip
    opener = ((lambda: gzip.open(path, "rt", encoding="utf-8"))
              if path.endswith(".gz") else (lambda: open(path, encoding="utf-8")))
    rows: list[dict] = []
    with opener() as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def stamp(store: list[dict], events: list[dict]) -> list[dict]:
    """Add the causal news state to each trade row, in place, and return them.

    `akoan/state.py` is pure and takes the event list, so we hand it only the
    events near the trade — same result as passing all of them, without the
    O(trades x events) scan.
    """
    events = sorted(events, key=lambda e: e["at_utc"])
    times = [e["at_utc"] for e in events]
    for row in store:
        ts = row["time"]
        lo = bisect.bisect_left(times, ts - _PAD_BACK)
        hi = bisect.bisect_right(times, ts + _PAD_FWD)
        st = news_state.state_at(events[lo:hi], ts, direction=row.get("direction"))
        row["blackout"] = st["blackout"]
        row["recent_release"] = st["recent_release"]
        row["upcoming"] = st["upcoming"]
        row["alignment"] = st["alignment"]
        row["news_in_play"] = st["news_in_play"]
    return store


def candidates() -> list[tuple[str, object]]:
    """News conditions to test. Each is a hypothesis, not a search."""
    return [
        ("all (baseline)", lambda r: True),
        ("no news in play", lambda r: not r.get("news_in_play")),
        ("news in play", lambda r: r.get("news_in_play")),
        ("quiet (no blackout)", lambda r: not r.get("blackout")),
        ("blackout window", lambda r: r.get("blackout")),
        ("after a release", lambda r: r.get("recent_release")),
        ("no recent release", lambda r: not r.get("recent_release")),
        ("before a release", lambda r: r.get("upcoming")),
        ("trade WITH the news", lambda r: r.get("alignment") == "WITH_NEWS"),
        ("trade AGAINST the news", lambda r: r.get("alignment") == "AGAINST_NEWS"),
        ("news-neutral", lambda r: r.get("alignment") == "NEUTRAL"),
    ]


def combinations() -> list[tuple[str, object]]:
    """Pre-specified combinations of the two leads the two studies raised.

    These are not searched: Plan A named H4-alignment, Plan B names the
    news-in-play window, and the point is to ask whether the *two* together are
    stronger than either alone — a hypothesis fixed before looking.
    """
    return [
        ("H4-aligned + after release",
         lambda r: r.get("h4_align") == "ALIGNED" and r.get("recent_release")),
        ("H4-aligned + no news",
         lambda r: r.get("h4_align") == "ALIGNED" and not r.get("news_in_play")),
        ("H4-aligned + news in play",
         lambda r: r.get("h4_align") == "ALIGNED" and r.get("news_in_play")),
    ]


def loss_breakdown(store: list[dict]) -> None:
    """Where the net R actually lives, by news state — the story behind the gate.

    The gate answers pass/fail; this answers *why*, by showing whether the
    losses are spread evenly or concentrated in one regime. A loss concentrated
    in the quiet window is a filter candidate; a loss spread everywhere is a
    no-information process.
    """
    groups = {
        "news in play": [r for r in store if r.get("news_in_play")],
        "quiet (no news)": [r for r in store if not r.get("news_in_play")],
        "after a release": [r for r in store if r.get("recent_release")],
        "before a release": [r for r in store if r.get("upcoming")],
        "blackout": [r for r in store if r.get("blackout")],
    }
    print("\n" + "-" * 92)
    print("net R by news state (all years, resolved trades)")
    print("-" * 92)
    for name, rows in groups.items():
        half = gate.evaluate(rows)
        wr = ("%.1f%%" % (100.0 * half["win_rate"])) if half["win_rate"] else "  -"
        print("  %-18s n=%-5d win=%-6s R=%+9.1f  R/trade=%+.3f"
              % (name, len(rows), wr, half["total_r"], half["r_per_trade"]))


def run_group(store: list[dict], name: str, pred, split_year: int,
              min_n: int) -> str:
    """Gate one condition and print its line. Returns the verdict."""
    subset = [r for r in store if pred(r)]
    is_rows, oos_rows = gate.split_rows(subset, split_year)
    verdict = gate.gate(gate.evaluate(is_rows), gate.evaluate(oos_rows), min_n=min_n)
    mark = {"PROMOTE": "PASS", "REJECT": "fail", "INSUFFICIENT": "thin"}[
        verdict["verdict"]]
    print("\n[%s] %s" % (mark, name))
    print("    IS : %s" % study.fmt_half(verdict["in_sample"]))
    print("    OOS: %s" % study.fmt_half(verdict["out_of_sample"]))
    for reason in verdict["reasons"][:2]:
        print("    - %s" % reason)
    return verdict["verdict"]


def atr_matched_check(store: list[dict]) -> None:
    """Is the "after a release" gain news, or just the higher volatility?

    A post-release bar is a high-ATR bar, and a high-ATR bar has a structural R
    advantage: the fixed spread is a smaller fraction of a bigger move. So
    "after a release looks good" could be "big bars look good" wearing a news
    label. This compares the news trades against the rest *inside each ATR
    quartile*: if the news edge survives in every band, it is news; if it
    vanishes once ATR is held fixed, it was volatility all along.
    """
    rows = [r for r in store if r.get("atr_pct")]
    if len(rows) < 8:
        return
    atrs = sorted(r["atr_pct"] for r in rows)
    q = [atrs[int(len(atrs) * f)] for f in (0.25, 0.50, 0.75)]

    def band(a: float) -> str:
        if a <= q[0]:
            return "Q1 calm"
        if a <= q[1]:
            return "Q2"
        if a <= q[2]:
            return "Q3"
        return "Q4 volatile"

    print("\n" + "-" * 92)
    print("ATR-matched check: is 'after a release' news, or just high volatility?")
    print("-" * 92)
    print("  %-10s %-22s %-22s %s" % ("ATR band", "after release",
                                      "not after release", "diff R/trade"))
    for name in ("Q1 calm", "Q2", "Q3", "Q4 volatile"):
        sub = [r for r in rows if band(r["atr_pct"]) == name]
        after = gate.evaluate([r for r in sub if r.get("recent_release")])
        rest = gate.evaluate([r for r in sub if not r.get("recent_release")])
        diff = after["r_per_trade"] - rest["r_per_trade"]
        print("  %-10s n=%-4d %+7.3f        n=%-4d %+7.3f        %+.3f"
              % (name, after["n"], after["r_per_trade"],
                 rest["n"], rest["r_per_trade"], diff))


def window_sensitivity(store: list[dict], events: list[dict],
                       split_year: int) -> None:
    """Is the post-release effect robust to how long "after" lasts?

    The 120-minute default is a choice, so this re-stamps the trades at 30, 60,
    120 and 240 minutes and reports the effect each time. A real effect decays
    smoothly; a fluke flickers on and off with the window. This is a robustness
    check on a lead, not a search for the window that passes.
    """
    events = sorted(events, key=lambda e: e["at_utc"])
    times = [e["at_utc"] for e in events]
    # Slice the events once per row, wide enough for the largest window tested.
    near: dict[int, list[dict]] = {}
    for r in store:
        lo = bisect.bisect_left(times, r["time"] - 6 * 3600)
        hi = bisect.bisect_right(times, r["time"] + 2 * 3600)
        near[id(r)] = events[lo:hi]

    print("\n" + "-" * 92)
    print("window sensitivity: R/trade after a release, by lookback window")
    print("-" * 92)
    print("  %-10s %-30s %s" % ("window", "in-sample", "out-of-sample"))
    for minutes in (30, 60, 120, 240):
        is_rows, oos_rows = [], []
        for r in store:
            st = news_state.state_at(near[id(r)], r["time"], recent_min=minutes)
            if not st["recent_release"]:
                continue
            (is_rows if int(r["year"]) < split_year else oos_rows).append(r)
        a, b = gate.evaluate(is_rows), gate.evaluate(oos_rows)
        print("  %-10s n=%-4d %+7.3f R/trade      n=%-4d %+7.3f R/trade"
              % ("%d min" % minutes, a["n"], a["r_per_trade"],
                 b["n"], b["r_per_trade"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="News-context gate study")
    parser.add_argument("--store", default=DEFAULT_STORE)
    parser.add_argument("--ledger", default=DEFAULT_LEDGER)
    parser.add_argument("--split-year", type=int, default=2024)
    parser.add_argument("--min-n", type=int, default=gate.DEFAULT_MIN_N)
    args = parser.parse_args(argv)

    store = load_jsonl(args.store)
    events = load_jsonl(args.ledger)
    if not store or not events:
        sys.stderr.write("[news] need both a store and a ledger\n")
        return 1
    stamp(store, events)

    in_play = sum(1 for r in store if r.get("news_in_play"))
    blackout = sum(1 for r in store if r.get("blackout"))
    print("=" * 92)
    print("ASTRA news study — split at %d, min_n=%d" % (args.split_year, args.min_n))
    print("store: %d trades | ledger: %d events | news_in_play: %d | blackout: %d"
          % (len(store), len(events), in_play, blackout))
    print("=" * 92)

    accepted: list[str] = []
    for name, pred in candidates():
        if run_group(store, name, pred, args.split_year, args.min_n) == "PROMOTE":
            accepted.append(name)

    print("\n" + "=" * 92)
    print("PRE-SPECIFIED COMBINATIONS (H4 lead + news lead)")
    print("=" * 92)
    for name, pred in combinations():
        if run_group(store, name, pred, args.split_year, args.min_n) == "PROMOTE":
            accepted.append(name)

    loss_breakdown(store)
    atr_matched_check(store)
    window_sensitivity(store, events, args.split_year)

    print("\n" + "=" * 92)
    if accepted:
        print("CLEARED THE GATE: %s" % ", ".join(accepted))
    else:
        print("CLEARED THE GATE: none. No news condition beats break-even "
              "out-of-sample at the Wilson lower bound.")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
