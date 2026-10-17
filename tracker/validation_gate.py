"""The promotion gate: the rule that decides whether a change may touch live.

A backtest can always be tuned until it looks good, so "it improved the
backtest" is not evidence. This module holds the one standard a change must
clear before it is allowed near the engine, and it is deliberately hard to
satisfy:

  * **Both halves must clear break-even.** The candidate is scored in-sample
    (the years used to find it) and out-of-sample (years it never saw). An
    in-sample win with an out-of-sample loss is curve-fitting, not an edge.
  * **The Wilson lower bound must clear break-even**, not the point estimate.
    A 36.5% rate on 40 trades is not a 36.5% edge; the interval is wide, and
    the honest reading is the pessimistic end of it.
  * **The sample must be big enough** to say anything at all.

Everything here is pure arithmetic over counts and R values — no data access,
no randomness — so the same inputs always give the same verdict and a reviewer
can re-derive it by hand.
"""

from __future__ import annotations

import math

Z_95 = 1.96
DEFAULT_MIN_N = 100          # scored trades required on *each* half


def wilson(k: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for a binomial rate. Returns (low, high) fractions.

    Preferred over the normal approximation because it stays inside [0, 1] and
    behaves at small n and extreme rates, which is exactly where a trade sample
    lives.
    """
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    d = 1.0 + z * z / n
    centre = p + z * z / (2.0 * n)
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return (max(0.0, (centre - half) / d), min(1.0, (centre + half) / d))


def break_even_win_rate(avg_win_r: float, avg_loss_r: float) -> float | None:
    """The win rate at which net R is zero, given the average win and loss.

    Solves  p*avg_win + (1-p)*avg_loss = 0  ->  p = -avg_loss / (avg_win - avg_loss).
    Returns None when there is no separating solution (e.g. wins and losses are
    both non-positive), which means no win rate can break even.
    """
    denom = avg_win_r - avg_loss_r
    if denom <= 0:
        return None
    p = -avg_loss_r / denom
    return p if 0.0 <= p <= 1.0 else None


def evaluate(rows: list[dict]) -> dict:
    """Score one half (a list of trade rows) on the metrics the gate reads.

    ``rows`` carry an ``outcome`` (TARGET/STOP/TIMEOUT) and ``r_net``. Only
    TARGET/STOP count as scored; a timeout is neither a win nor a loss and is
    excluded from the rate, exactly as the tracker's summary does.
    """
    scored = [r for r in rows if r.get("outcome") in ("TARGET", "STOP")]
    wins = [r for r in scored if r["outcome"] == "TARGET"]
    losses = [r for r in scored if r["outcome"] == "STOP"]
    n, k = len(scored), len(wins)

    rs = [r["r_net"] for r in rows if r.get("r_net") is not None]
    total_r = sum(rs)
    avg_win = (sum(r["r_net"] for r in wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(r["r_net"] for r in losses) / len(losses)) if losses else 0.0
    be = break_even_win_rate(avg_win, avg_loss)
    lo, hi = wilson(k, n)

    return {
        "n": n,
        "wins": k,
        "losses": len(losses),
        "timeouts": len([r for r in rows if r.get("outcome") == "TIMEOUT"]),
        "win_rate": (k / n) if n else None,
        "wilson_low": lo if n else None,
        "wilson_high": hi if n else None,
        "break_even": be,
        "avg_win_r": avg_win,
        "avg_loss_r": avg_loss,
        "total_r": round(total_r, 2),
        "r_per_trade": (total_r / len(rows)) if rows else 0.0,
    }


def gate(in_sample: dict, out_of_sample: dict,
         min_n: int = DEFAULT_MIN_N) -> dict:
    """The verdict: PROMOTE, REJECT, or INSUFFICIENT, with the reason.

    Order matters. A half with too few trades cannot be judged at all, so a
    shortfall is reported as INSUFFICIENT before any break-even test runs —
    otherwise a thin sample would be scored as a failure when the honest
    reading is "not enough evidence yet". Only when both halves are big enough
    does the gate apply the break-even tests, and a change is promoted only if
    both halves clear every one of them.
    """
    thin = [name for name, half in (("in-sample", in_sample),
                                    ("out-of-sample", out_of_sample))
            if half["n"] < min_n]
    if thin:
        thin_reasons = ["%s: %d scored trades, need %d"
                        % (name, (in_sample if name == "in-sample"
                                  else out_of_sample)["n"], min_n)
                        for name in thin]
        return {"verdict": "INSUFFICIENT", "reasons": thin_reasons,
                "in_sample": in_sample, "out_of_sample": out_of_sample}

    reasons: list[str] = []
    for name, half in (("in-sample", in_sample), ("out-of-sample", out_of_sample)):
        if half["break_even"] is None:
            reasons.append("%s: no break-even (win/loss do not separate)" % name)
            continue
        if half["wilson_low"] <= half["break_even"]:
            reasons.append(
                "%s: win rate %.1f%% (Wilson low %.1f%%) does not clear "
                "break-even %.1f%%"
                % (name, 100.0 * (half["win_rate"] or 0.0),
                   100.0 * half["wilson_low"], 100.0 * half["break_even"]))
        if half["total_r"] <= 0:
            reasons.append("%s: net R is %.1f (not positive)" % (name, half["total_r"]))

    verdict = "PROMOTE" if not reasons else "REJECT"
    return {"verdict": verdict, "reasons": reasons,
            "in_sample": in_sample, "out_of_sample": out_of_sample}


def split_rows(rows: list[dict], split_year: int) -> tuple[list[dict], list[dict]]:
    """Split rows into (before split_year, at/after) using the UTC year.

    Kept here rather than at the call site so every experiment splits on the
    same boundary and the in/out halves are never accidentally swapped.
    """
    import datetime as dt
    is_rows: list[dict] = []
    oos_rows: list[dict] = []
    for r in rows:
        year = dt.datetime.fromtimestamp(r["time"], dt.timezone.utc).year
        (is_rows if year < split_year else oos_rows).append(r)
    return is_rows, oos_rows
