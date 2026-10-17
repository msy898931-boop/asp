#!/usr/bin/env python3
"""akoan engine: does the news explain the results, or not?

This is the module the whole engine exists for. It takes the joined ledger
(`link.py`) and, for each way of slicing it by news, asks three questions in
order:

  1. **Is there a record?** n >= MIN_N, else the bucket is reported as
     `insufficient_data` and no rate is stated.
  2. **Is the rate different from chance?** a Fisher exact test against the
     complement, and a Wilson interval so the reader sees the width.
  3. **Is the difference from the *base rate* real, and does it survive
     correction?** `edge_vs_base` names the gap, Benjamini-Hochberg across all
     buckets controls the false discovery rate, and a label-shuffle permutation
     test checks each dimension as a whole.

The default answer is **NO_EDGE_MEASURED**. A dimension is only called
`EDGE_CANDIDATE` when it clears all three — enough data, a significant
difference from its complement, *and* survival under FDR correction. With the
ledger as young as it is, the honest output is almost always "not enough data
yet", and the engine says exactly that instead of manufacturing a finding.

The engine is pure: it is handed rows and returns a dict. No network, no clock,
no file I/O — so a published akoan result is reproducible from its inputs.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import stats as st  # noqa: E402

# How many scored trades a bucket needs before any rate is stated.
MIN_N = int(os.environ.get("ASTRA_AKOAN_MIN_N", str(st.MIN_N)))
FDR_Q = float(os.environ.get("ASTRA_AKOAN_FDR_Q", "0.10"))
PERM_TRIALS = int(os.environ.get("ASTRA_AKOAN_PERM_TRIALS", "2000"))

# The dimensions scanned, and the Arabic label each bucket value is shown under.
DIMENSIONS = ("blackout", "recent_release", "upcoming", "alignment", "news_in_play")

VALUE_AR = {
    "blackout": "وقت خبر (محظور)",
    "quiet": "هادئ",
    "after_release": "بعد صدور خبر",
    "no_release": "بلا خبر حديث",
    "before_release": "قبل صدور خبر",
    "no_upcoming": "بلا خبر قريب",
    "WITH_NEWS": "مع الخبر",
    "AGAINST_NEWS": "ضد الخبر",
    "NEUTRAL": "محايد",
    "news_in_play": "الخبر مؤثّر",
    "no_news": "بلا خبر",
}

DIM_AR = {
    "blackout": "نافذة الخبر",
    "recent_release": "خبر صدر قريبًا",
    "upcoming": "خبر قريب",
    "alignment": "اتجاه الصفقة مقابل الخبر",
    "news_in_play": "وجود خبر",
}


def _bucket_rows(rows: list[dict], key: str, value: object) -> list[dict]:
    return [r for r in rows if r.get(key) == value]


def build_dimensions(rows: list[dict], min_n: int = MIN_N) -> list[st.Dimension]:
    """One Dimension per news axis, every bucket honestly labelled."""
    scored = [r for r in rows if r.get("win") is not None]
    base = st.base_rate(scored)
    dims: list[st.Dimension] = []

    for key in DIMENSIONS:
        # Every dimension value is a label written by `dimension_keys`, so the
        # set is str-typed even though the rows come in as plain dicts.
        values = sorted({str(r.get(key)) for r in scored
                         if r.get(key) is not None})
        dim = st.Dimension(name=key)
        for value in values:
            bucket = _bucket_rows(scored, key, value)
            n = len(bucket)
            wins = sum(1 for r in bucket if r["win"])
            losses = n - wins
            timeouts = len([r for r in rows
                            if r.get(key) == value and r.get("label") == "TIMEOUT"])
            net_r = sum(r.get("r_net") or 0.0 for r in bucket)
            complement = [r for r in scored if r.get(key) != value]

            if n < min_n:
                dim.buckets.append(st.Bucket(
                    value=value, n=n, wins=wins, losses=losses, timeouts=timeouts,
                    net_r=net_r, win_rate=None, ci_low=None, ci_high=None,
                    base_rate=base, edge=None, p_value=None,
                    note="insufficient_data: %d scored, need %d" % (n, min_n)))
                continue

            ci = st.wilson_interval(wins, n)
            edge = st.edge_vs_base(wins, n, base)
            p = None
            if complement:
                p = st.fisher_exact_p(wins, losses,
                                      sum(1 for r in complement if r["win"]),
                                      sum(1 for r in complement if not r["win"]))
            dim.buckets.append(st.Bucket(
                value=value, n=n, wins=wins, losses=losses, timeouts=timeouts,
                net_r=net_r, win_rate=wins / n,
                ci_low=ci[0] if ci else None, ci_high=ci[1] if ci else None,
                base_rate=base, edge=edge, p_value=p))

        dim.permutation_p = st.permutation_p(
            scored, lambda r, k=key: r.get(k), trials=PERM_TRIALS)
        # A dimension with only one populated bucket has nothing to compare
        # against; say so rather than presenting a rate as if it were a test.
        populated = [b for b in dim.buckets if b.win_rate is not None]
        dim.comparable = len(populated) >= 2
        dims.append(dim)
    return dims


def apply_fdr(dims: list[st.Dimension], q: float = FDR_Q) -> int:
    """Mark buckets that survive Benjamini-Hochberg across every dimension."""
    flat = [(d, b) for d in dims for b in d.buckets if b.p_value is not None]
    if not flat:
        return 0
    keep = st.benjamini_hochberg([b.p_value for _, b in flat], q)
    survivors = 0
    for (_, bucket), ok in zip(flat, keep):
        bucket.significant = bool(ok)
        if ok:
            survivors += 1
    return survivors


def verdict(dims: list[st.Dimension], scored_n: int, min_n: int = MIN_N) -> str:
    """The one-line honest answer the result carries."""
    if scored_n < min_n:
        return "INSUFFICIENT_DATA"
    if any(b.significant for d in dims for b in d.buckets):
        return "EDGE_CANDIDATE"
    if any(b.p_value is not None for d in dims for b in d.buckets):
        return "NO_EDGE_MEASURED"
    return "INSUFFICIENT_DATA"


def analyse(rows: list[dict], min_n: int = MIN_N, q: float = FDR_Q) -> dict:
    """The full akoan result: every dimension, corrected, with its verdict."""
    scored = [r for r in rows if r.get("win") is not None]
    dims = build_dimensions(rows, min_n=min_n)
    survivors = apply_fdr(dims, q)
    v = verdict(dims, len(scored), min_n)

    base = st.base_rate(scored)
    wins = sum(1 for r in scored if r["win"])
    overall_ci = st.wilson_interval(wins, len(scored)) if scored else None

    result: dict = {
        "engine": "akoan",
        "schema_version": "1.0",
        "question": "هل يفسّر الخبر نتائج الصفقات؟",
        "scored": len(scored),
        "unscored": len(rows) - len(scored),
        "wins": wins,
        "losses": len(scored) - wins,
        "net_r": round(sum(r.get("r_net") or 0.0 for r in scored), 2),
        "min_n": min_n,
        "fdr_q": q,
        "verdict": v,
        "survivors": survivors,
        "dimensions": [d.as_dict() for d in dims],
    }
    if base is not None:
        result["base_rate"] = round(base, 4)
    if overall_ci:
        result["overall_win_rate"] = round(wins / len(scored), 4)
        result["overall_ci_95"] = [round(overall_ci[0], 4), round(overall_ci[1], 4)]

    result["verdict_ar"] = {
        "EDGE_CANDIDATE": "مرشّح لحافة: بقيّة بعد تصحيح التعدّد — بدو تأكيد بعيّنة أكبر.",
        "NO_EDGE_MEASURED": "ما انقاس تأثير للخبر على النتائج. لا حافة.",
        "INSUFFICIENT_DATA": "ما في بيانات كافية للحكم بعد.",
    }.get(v, v)
    return result
