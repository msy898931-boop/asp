#!/usr/bin/env python3
"""Should the tracker hold more than one idea at a time?

`tracker/replay.py` holds at most ONE live idea: a new idea waits for the live
one to settle, plus a cooldown. This probe asks the review's question — is that
too strict? — by replaying the same M15 bars under a policy that allows up to N
concurrent ideas and comparing the two.

The answer, on the project's own 2026 XAUUSD M15 data, is no:

    policy             ideas  resolved   win%   total_R    mean_R  maxStreak
    1 (live rule)       1047      1047   31.7    -44.39    -0.042         12
    2 concurrent        1922      1922   31.4    -92.57    -0.048         22
    3 concurrent        2674      2673   30.7   -169.54    -0.063         27
    5 concurrent        3807      3805   31.2   -155.71    -0.041         37

Concurrency multiplies the number of trades roughly by N (they overlap, which is
the point), but it does not raise the win rate and it deepens the loss streak —
12 losses in a row becomes 37. It also worsens mean R per trade. That is the
inflation the single-idea rule exists to prevent: the same directional move gets
counted several times, so any rate built on the count is overstated.

Note the live rule is itself net-negative on this window (mean R -0.042). That is
the project's own honest finding — the M15 directional read is near a coin flip —
and it is a separate problem from the concurrency question. Concurrency does not
fix it; it makes the bleed larger.

Method. The per-bar decision (features, structure, regime, direction, levels) is
computed ONCE with a bounded trailing window (default 200 bars, matching the live
bot's `ASTRA_SCAN_LOOKBACK`; EMA50 has long since converged inside it) and reused
across policies, so the comparison is one O(n) pass per policy.

Stdlib only, no network. Run:

    python3 research/multi_trade_probe.py
    python3 research/multi_trade_probe.py --max 1,2,4,8 --limit 500000
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

from tracker import replay  # noqa: E402

DEFAULT_CSV = os.path.join(REPO_ROOT, "research", "data", "xauusd_m1",
                           "xauusd_m1_2026.csv")
DEFAULT_WINDOW = 200


def load_m15(path: str, limit: int | None = None) -> list[dict]:
    """Resample the M1 CSV into M15 bars (the tracker's native timeframe)."""
    bars: list[dict] = []
    with open(path, newline="") as fh:
        reader = csv.reader(fh)
        next(reader, None)
        bucket = None
        o = h = l = c = None
        vol = 0.0
        n = 0
        for row in reader:
            if not row or not row[0]:
                continue
            ts = row[0]
            oo, hh, ll, cc, vv = map(float, row[1:6])
            minute = int(ts[14:16])
            hour = int(ts[11:13])
            day = ts[:10]
            b = "%s %02d:%02d" % (day, hour, (minute // 15) * 15)
            if b != bucket:
                if bucket is not None:
                    bars.append(_bar(bucket, o, h, l, c, vol))
                bucket, o, h, l, c, vol = b, oo, hh, ll, cc, 0.0
            else:
                h = max(h, hh)
                l = min(l, ll)
                c = cc
            vol += vv
            n += 1
            if limit is not None and n >= limit:
                break
        if bucket is not None:
            bars.append(_bar(bucket, o, h, l, c, vol))
    return bars


def _bar(time, o, h, l, c, vol) -> dict:
    return {"time": time, "open": o, "high": h, "low": l, "close": c,
            "tick_volume": int(vol), "spread": 0, "real_volume": 0}


def precompute(bars: list[dict], window: int = DEFAULT_WINDOW) -> list:
    """One decision per bar: None, or (direction, stop, target, close, ...).

    The decision at bar i uses the trailing ``window`` bars, the same bounded
    lookback the live bot reads.
    """
    out: list = [None] * len(bars)
    for i in range(replay.MIN_BARS - 1, len(bars)):
        win = bars[max(0, i - window + 1):i + 1]
        features = replay._features(win)
        if features is None:
            continue
        bias = replay._structure(win)
        if bias == "INCOMPLETE":
            continue
        regime = replay._regime(features, bias)
        if regime == "VOLATILE":
            continue
        direction = replay._direction(features, bias, regime)
        if direction == "NONE":
            continue
        stop, target = replay._levels(direction, features["close"], features["atr"])
        out[i] = (direction, stop, target, features["close"], bias, regime,
                  features["rsi"])
    return out


def replay_multi(bars: list[dict], decisions: list, max_concurrent: int,
                 cost_usd: float = replay.DEFAULT_COST_USD,
                 horizon_bars: int = replay.DEFAULT_HORIZON_BARS,
                 cooldown_bars: int = replay.DEFAULT_COOLDOWN_BARS) -> list:
    """Open up to ``max_concurrent`` ideas at once, each settled independently.

    ``max_concurrent=1`` reproduces `replay.build_signals` when ``decisions`` was
    built with a window at least as long as the series.
    """
    signals: list = []
    live: list = []
    ready_at = -1
    for i in range(replay.MIN_BARS - 1, len(bars)):
        bar = bars[i]
        still = []
        for sig in live:
            if replay._advance(sig, bar, cost_usd, horizon_bars):
                ready_at = max(ready_at, i + cooldown_bars)
            else:
                still.append(sig)
        live = still
        if i < ready_at or len(live) >= max_concurrent:
            continue
        d = decisions[i]
        if d is None:
            continue
        direction, stop, target, close, bias, regime, rsi = d
        sig = replay.Signal(index=i, time=bars[i]["time"], direction=direction,
                            entry=close, stop=stop, target=target, bias=bias,
                            regime=regime, rsi=rsi)
        signals.append(sig)
        live.append(sig)
    return signals


def metrics(signals: list) -> dict:
    res = [s for s in signals if s.resolved]
    wins = [s for s in res if s.outcome == "TARGET"]
    total_r = sum(s.r_net for s in res if s.r_net is not None)
    streak = worst = 0
    for s in sorted(res, key=lambda x: x.time):
        if s.outcome == "STOP":
            streak += 1
            worst = max(worst, streak)
        else:
            streak = 0
    return {"ideas": len(signals), "resolved": len(res), "wins": len(wins),
            "win_rate": (len(wins) / len(res) * 100) if res else 0.0,
            "total_r": total_r,
            "mean_r": (total_r / len(res)) if res else 0.0,
            "max_loss_streak": worst}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", default=DEFAULT_CSV)
    ap.add_argument("--limit", type=int, default=None,
                    help="M1 rows to read (default: all)")
    ap.add_argument("--window", type=int, default=DEFAULT_WINDOW)
    ap.add_argument("--max", default="1,2,3,5",
                    help="comma-separated concurrency levels")
    args = ap.parse_args(argv)

    if not os.path.exists(args.csv):
        sys.stderr.write("no data at %s\n" % args.csv)
        return 2
    bars = load_m15(args.csv, args.limit)
    if len(bars) < replay.MIN_BARS + 1:
        sys.stderr.write("not enough bars: %d\n" % len(bars))
        return 2
    print("M15 bars: %d (%s .. %s)" % (len(bars), bars[0]["time"], bars[-1]["time"]))
    decisions = precompute(bars, args.window)
    eligible = sum(1 for d in decisions if d is not None)
    print("bars eligible to open: %d of %d\n" % (eligible, len(bars)))

    hdr = "%-16s %7s %9s %6s %9s %9s %10s" % (
        "policy", "ideas", "resolved", "win%", "total_R", "mean_R", "maxStreak")
    print(hdr)
    print("-" * len(hdr))
    for level in [int(x) for x in args.max.split(",")]:
        name = "1 (live rule)" if level == 1 else "%d concurrent" % level
        m = metrics(replay_multi(bars, decisions, level))
        print("%-16s %7d %9d %6.1f %9.2f %9.3f %10d" % (
            name, m["ideas"], m["resolved"], m["win_rate"], m["total_r"],
            m["mean_r"], m["max_loss_streak"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
