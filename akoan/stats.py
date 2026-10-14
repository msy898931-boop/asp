#!/usr/bin/env python3
"""akoan statistics core: the honest tests, on the standard library alone.

akoan's whole job is to refuse to claim an edge it cannot show. That makes the
statistics the product, so they live here in one place, pure and testable — no
network, no clock, no I/O.

Three ideas carry the module:

  * **A rate means nothing without its interval.** `wilson_interval` gives the
    Wilson score interval for a proportion, which stays inside [0, 1] and
    behaves at small n where the normal approximation does not.

  * **A win rate means nothing without its base rate.** The project's own
    research (AGENTS.md, "Model edge") showed a 51.7% directional accuracy that
    was just gold's 51.7% drift. So the edge we report is the *difference* from
    the base rate, never the raw rate — `edge_vs_base`.

  * **Testing many things finds edges by chance.** `benjamini_hochberg` controls
    the false discovery rate across the dimensions akoan scans, so a "finding"
    must survive correction before it is called one.

Nothing here reads the news or the ledger; it is arithmetic over counts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# Below this many samples a rate is not stated at all — the same floor the
# tracker uses (tracker/replay.py MIN_BUCKET), kept identical on purpose.
MIN_N = 8

Z_95 = 1.959963984540054  # two-sided 95%


def _log_choose(n: int, k: int) -> float:
    return (math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1))


def wilson_interval(wins: int, n: int, z: float = Z_95) -> tuple[float, float] | None:
    """Wilson score interval for wins/n, or None when n is too small.

    Preferred over normal-approximation here because n is often tiny and the
    rate is often near 0 or 1, where that approximation breaks down.
    """
    if n <= 0 or wins < 0 or wins > n:
        return None
    if n < MIN_N:
        return None
    p = wins / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def fisher_exact_p(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p for the 2x2 table [[a, b], [c, d]].

    Exact, not asymptotic, because the buckets are small. This is what tells us
    whether a bucket's rate really differs from everything else, rather than
    looking like it does on a handful of trades.
    """
    if min(a, b, c, d) < 0:
        return 1.0
    r1, r2 = a + b, c + d
    c1 = a + c
    n = r1 + r2
    if n == 0:
        return 1.0

    def prob(x: int) -> float:
        return math.exp(_log_choose(r1, x) + _log_choose(r2, c1 - x)
                        - _log_choose(n, c1))

    lo, hi = max(0, c1 - r2), min(r1, c1)
    observed = prob(a)
    # Sum every table at least as extreme as the one seen (the standard
    # two-sided definition: p of outcomes with probability <= p(observed)).
    total = 0.0
    for x in range(lo, hi + 1):
        px = prob(x)
        if px <= observed * (1 + 1e-9):
            total += px
    return min(1.0, total)


def base_rate(rows: list[dict], win_key: str = "win") -> float | None:
    """The base rate a bucket must beat: the overall share of winners.

    The lesson from `horizon_diagnostic.py` — a model can look skilful purely
    because the market drifted up. Comparing against this, not against 0.5,
    is what makes the comparison meaningful.
    """
    scored = [r for r in rows if r.get(win_key) is not None]
    if len(scored) < MIN_N:
        return None
    return sum(1 for r in scored if r[win_key]) / len(scored)


def edge_vs_base(bucket_wins: int, bucket_n: int,
                 base: float | None) -> float | None:
    """Bucket win rate minus the base rate. None when either side is too thin."""
    if base is None or bucket_n < MIN_N:
        return None
    return bucket_wins / bucket_n - base


def benjamini_hochberg(pvals: list[float], q: float = 0.10) -> list[bool]:
    """Which hypotheses survive Benjamini-Hochberg FDR control at level q.

    Returns one boolean per input p-value, in the original order. Scanning
    dozens of dimensions is exactly the setting where a naive p<0.05 finds
    noise, so nothing is called a finding until it clears this.
    """
    m = len(pvals)
    if m == 0:
        return []
    if m == 1:
        return [pvals[0] <= q]
    order = sorted(range(m), key=lambda i: pvals[i])
    threshold_rank = 0
    for rank, idx in enumerate(order, start=1):
        if pvals[idx] <= q * rank / m:
            threshold_rank = rank
    keep = [False] * m
    for rank, idx in enumerate(order, start=1):
        if rank <= threshold_rank:
            keep[idx] = True
    return keep


def permutation_p(rows: list[dict], group_key, win_key: str = "win",
                  trials: int = 2000, seed: int = 12345) -> float | None:
    """A label-shuffle p-value for "does this partition predict wins?".

    The partition is fixed and only the win labels are shuffled, so the test
    asks whether the observed between-group difference is larger than chance
    would produce with the same group sizes. Deterministic for a given seed.
    """
    import random

    scored = [r for r in rows if r.get(win_key) is not None]
    n = len(scored)
    if n < MIN_N:
        return None
    labels = [bool(r[win_key]) for r in scored]
    groups = [group_key(r) for r in scored]
    distinct = {g for g in groups if g is not None}
    if len(distinct) < 2:
        return None

    def spread(labs: list[bool]) -> float:
        agg: dict = {}
        for lab, g in zip(labs, groups):
            if g is None:
                continue
            wins, tot = agg.get(g, (0, 0))
            agg[g] = (wins + (1 if lab else 0), tot + 1)
        rates = [w / t for (w, t) in agg.values() if t > 0]
        if len(rates) < 2:
            return 0.0
        return max(rates) - min(rates)

    observed = spread(labels)
    rng = random.Random(seed)
    hits = 0
    for _ in range(trials):
        shuffled = labels[:]
        rng.shuffle(shuffled)
        if spread(shuffled) >= observed - 1e-12:
            hits += 1
    # +1 keeps the p-value strictly positive (it can never be exactly zero).
    return (hits + 1) / (trials + 1)


@dataclass
class Bucket:
    """One dimension value and its record, with the honest verdict attached."""

    value: str
    n: int
    wins: int
    losses: int
    timeouts: int
    net_r: float
    win_rate: float | None
    ci_low: float | None
    ci_high: float | None
    base_rate: float | None
    edge: float | None
    p_value: float | None
    significant: bool = False
    note: str = ""

    def as_dict(self) -> dict:
        out = {
            "value": self.value,
            "n": self.n,
            "wins": self.wins,
            "losses": self.losses,
            "timeouts": self.timeouts,
            "net_r": round(self.net_r, 2),
        }
        if self.win_rate is not None:
            out["win_rate"] = round(self.win_rate, 4)
        if self.ci_low is not None:
            out["ci_95"] = [round(self.ci_low, 4), round(self.ci_high, 4)]
        if self.base_rate is not None:
            out["base_rate"] = round(self.base_rate, 4)
        if self.edge is not None:
            out["edge_vs_base"] = round(self.edge, 4)
        if self.p_value is not None:
            out["p_value"] = round(self.p_value, 4)
        out["significant"] = self.significant
        if self.note:
            out["note"] = self.note
        return out


@dataclass
class Dimension:
    """A way of slicing the ledger, e.g. "was a release inside the blackout?"."""

    name: str
    buckets: list[Bucket] = field(default_factory=list)
    permutation_p: float | None = None
    # A dimension can only test anything if it has at least two buckets with
    # enough data. One bucket alone has nothing to be compared against, and
    # that is worth saying rather than showing a rate in a vacuum.
    comparable: bool = False

    def as_dict(self) -> dict:
        out = {
            "name": self.name,
            "comparable": self.comparable,
            "buckets": [b.as_dict() for b in self.buckets],
        }
        if self.permutation_p is not None:
            out["permutation_p"] = round(self.permutation_p, 4)
        return out
