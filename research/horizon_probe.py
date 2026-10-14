#!/usr/bin/env python3
"""Phase 0 probe: is there an edge in the *size* of the next move, not its sign?

The T27 result was directional accuracy 0.517 over a one-bar horizon — a coin
flip. This probe asks the cheaper question instead:

    Does the recent past tell us how *big* the next move will be?

Volatility clusters: calm begets calm, storm begets storm. If that holds here,
a "don't trade now" filter is real value and the model has a target it can
actually hit. If it does not hold, we have saved the work of building it.

Method (causal, chronological):
  * trailing vol  = stdev of log returns over the W bars ending at i
  * forward vol   = stdev of log returns over the H bars starting at i
  * fit nothing on 2025; report 2025 as out-of-sample

Reported per horizon H:
  * Spearman rank correlation(trailing, forward) on OOS
  * AUC: P(a random high-vol case scores above a random low-vol case) — 0.50 is
    a coin flip, which is exactly the directional result this is measured against
  * directional accuracy over the same horizon, as the control

Stdlib only, no network. Run:
    python3 research/horizon_probe.py
"""

from __future__ import annotations

import math
import os
import sys
from datetime import datetime, timezone
from typing import List, Sequence, Tuple

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

from src.models.realdata import load_corpus  # noqa: E402

CORPUS = os.path.join(REPO_ROOT, "research", "features_real", "corpus",
                      "real_corpus_2021_2025.json.gz")
# dev 2021-2024, OOS 2025 (the same year split T27 used).
OOS_START = 1735689600        # 2025-01-01 00:00 UTC
HORIZONS = (1, 4, 16, 96)     # 15m, 1h, 4h, 1d at one row per M15 bar


def stdev(values: Sequence[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(var)


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Rank correlation. Ranks are average-ranked for ties, then Pearson on ranks."""
    def ranks(vals: Sequence[float]) -> List[float]:
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        out = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    rx, ry = ranks(xs), ranks(ys)
    n = len(rx)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return cov / (vx * vy) if vx and vy else 0.0


def auc(scores: Sequence[float], labels: Sequence[int]) -> float:
    """P(score of a positive > score of a negative), ties counted as half."""
    pos = sorted(s for s, l in zip(scores, labels) if l == 1)
    neg = sorted(s for s, l in zip(scores, labels) if l == 0)
    if not pos or not neg:
        return float("nan")
    # Rank-sum (Mann-Whitney) in O((n+m) log n) via binary search.
    import bisect
    total = 0.0
    for p in pos:
        lo = bisect.bisect_left(neg, p)
        hi = bisect.bisect_right(neg, p)
        total += lo + (hi - lo) / 2.0
    return total / (len(pos) * len(neg))


def directional_controls(closes: Sequence[float], times: Sequence[int],
                         horizon: int, start: int) -> Tuple[float, float, float]:
    """Three numbers for the direction question, OOS only:

    * base_rate   — always predict up. On a rising market this is a high number
                    that looks like skill; T27's 0.517 is suspiciously equal to
                    it, which is the whole point of printing it here.
    * momentum    — predict the previous bar's direction continues.
    * reversal    — predict it flips.

    If none of these beats the base rate, there is no directional edge, only
    drift — which is exactly what T27 found.
    """
    ups = total = 0
    mom_hits = rev_hits = mom_n = 0
    for i in range(1, len(closes) - horizon):
        if times[i] < start:
            continue
        up = closes[i + horizon] > closes[i]
        total += 1
        ups += 1 if up else 0
        last_up = closes[i] > closes[i - 1]
        mom_hits += 1 if (up == last_up) else 0
        rev_hits += 1 if (up != last_up) else 0
        mom_n += 1
    if not total:
        return float("nan"), float("nan"), float("nan")
    return (ups / total, mom_hits / mom_n, rev_hits / mom_n)


def probe_horizon(closes: Sequence[float], times: Sequence[int],
                  horizon: int, start: int) -> dict:
    window = max(horizon, 4)
    logret = [0.0] * len(closes)
    for i in range(1, len(closes)):
        logret[i] = math.log(closes[i] / closes[i - 1])

    trailing: List[float] = []
    forward: List[float] = []
    for i in range(window, len(closes) - horizon):
        trailing.append(stdev(logret[i - window + 1:i + 1]))
        forward.append(stdev(logret[i + 1:i + 1 + horizon]))

    times_oos = [times[i] for i in range(window, len(closes) - horizon)
                 if times[i] >= start]
    tra = [t for i, t in zip(range(window, len(closes) - horizon), trailing)
           if times[i] >= start]
    fwd = [f for i, f in zip(range(window, len(closes) - horizon), forward)
           if times[i] >= start]

    # Split forward vol at its OOS median; can trailing vol rank the two halves?
    med = sorted(fwd)[len(fwd) // 2]
    labels = [1 if f >= med else 0 for f in fwd]
    base_rate, momentum, reversal = directional_controls(
        closes, times, horizon, start)
    return {
        "horizon_bars": horizon,
        "minutes": horizon * 15,
        "oos_n": len(fwd),
        "vol_spearman": spearman(tra, fwd),
        "vol_auc": auc(tra, labels),
        "dir_base_rate": base_rate,
        "dir_momentum": momentum,
        "dir_reversal": reversal,
    }


def per_year_vol_auc(closes: Sequence[float], times: Sequence[int],
                     horizon: int) -> List[Tuple[int, float, int]]:
    """vol_AUC within each calendar year — the stability check. An edge that
    only exists in one year is a regime artifact, not a law."""
    window = max(horizon, 4)
    logret = [0.0] * len(closes)
    for i in range(1, len(closes)):
        logret[i] = math.log(closes[i] / closes[i - 1])

    buckets: dict = {}
    for i in range(window, len(closes) - horizon):
        year = datetime.fromtimestamp(times[i], tz=timezone.utc).year
        tra = stdev(logret[i - window + 1:i + 1])
        fwd = stdev(logret[i + 1:i + 1 + horizon])
        buckets.setdefault(year, ([], []))
        buckets[year][0].append(tra)
        buckets[year][1].append(fwd)

    out = []
    for year in sorted(buckets):
        tra, fwd = buckets[year]
        med = sorted(fwd)[len(fwd) // 2]
        labels = [1 if f >= med else 0 for f in fwd]
        out.append((year, auc(tra, labels), len(fwd)))
    return out


def main() -> int:
    print("loading corpus ...", flush=True)
    feature_sets, closes = load_corpus(CORPUS)
    times = [fs.asOfBarOpenSec for fs in feature_sets]
    print("rows=%d  span=%s..%s" % (
        len(closes),
        datetime.fromtimestamp(times[0], tz=timezone.utc).date(),
        datetime.fromtimestamp(times[-1], tz=timezone.utc).date()))

    print("\n=== Phase 0 probe: volatility predictability (OOS = 2025) ===")
    print("%-8s %-8s %-12s %-8s %-10s %-10s %-10s" %
          ("horizon", "minutes", "vol_spearman", "vol_AUC",
           "dir_base", "dir_mom", "dir_rev"))
    results = []
    for h in HORIZONS:
        r = probe_horizon(closes, times, h, OOS_START)
        results.append(r)
        print("%-8d %-8d %-12.3f %-8.3f %-10.3f %-10.3f %-10.3f" % (
            r["horizon_bars"], r["minutes"], r["vol_spearman"], r["vol_auc"],
            r["dir_base_rate"], r["dir_momentum"], r["dir_reversal"]))
    print("\nvol_AUC 0.50 = coin flip. dir_base is 'always predict up'; if the")
    print("direction baselines sit at dir_base, the direction question has no")
    print("edge — which is what T27 found at 0.517. vol_AUC well above 0.50 is")
    print("an edge in *size*: a 'do not trade now' filter, a target we can hit.")

    print("\n=== stability: vol_AUC per calendar year (horizon=16) ===")
    for year, a, n in per_year_vol_auc(closes, times, 16):
        print("  %d  vol_AUC=%.3f  n=%d" % (year, a, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
