"""Resolve SMC/ICT plans against the forward tape, so a plan becomes a result.

The SMC engine writes each plan it produces to ``run/smc_engine_plans.jsonl``
and, until now, nothing ever read that file back. A plan therefore carried a
``confidence`` the model chose and no record of whether it was right — an
unfalsifiable claim, which is the one thing the rest of this project refuses to
ship. This module closes that loop: given a plan and the bars that came after
it, it decides the outcome (TARGET, STOP, TIMEOUT or EXPIRED), charges the
spread, and reports the result in R — the same accounting the tracker uses, so
an SMC rate can be read next to a tracker rate without a translation step.

Design notes, mirroring ``tracker/replay.py`` on purpose:

  * **Pure stdlib, no network, no model, no wall clock.** The caller passes the
    bars and, when it has one, the timestamp the plan was made at; every value
    is a function of those alone, so a resolution is reproducible and testable.
  * **A plan is a limit order, not a market order.** SMC enters at an order
    block or a gap, which is usually *away* from the current price. So the plan
    first has to be *reached*: a bar must trade through the entry before the
    trade exists. A plan price never returns to is EXPIRED — never a win, never
    a loss, and excluded from the rate.
  * **The stop wins a tie.** When one bar touches both the stop and a target,
    the resolution is a STOP. Intra-bar order is unknowable from OHLC, and the
    pessimistic reading is the only honest one.
  * **Spread is charged on resolution**, so a gross winner can be a net loser —
    the reason the number that matters is ``r_net``, not ``r_gross``.
  * **The statistics are duplicated, not imported.** ``tracker/validation_gate``
    holds the canonical Wilson interval and break-even solver, but ``advisor``
    is a separate process tree that must stay importable on its own; reaching
    into ``tracker`` from here would couple the two and break the isolation the
    SMC engine is built around. The formulas are identical and are pinned by
    tests that compare them against ``validation_gate`` directly.

Nothing here forms an opinion or an order. It only decides what already
happened.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

# Round-trip spread charged per resolved trade, in USD/oz — the same figure the
# tracker uses, so the two are comparable. Overridable by the caller.
DEFAULT_COST_USD = 0.30

# How long a plan may wait to be reached, and how long the trade may then run,
# both in bars of the frame it was read from. M15 * 32 = 8 hours, matching the
# tracker's horizon so a slow SMC setup is not judged on a faster clock.
DEFAULT_PENDING_BARS = 32
DEFAULT_HORIZON_BARS = 32

# Below this many scored trades no rate is stated — a thin sample is not a rate.
DEFAULT_MIN_SCORED = 30

Z_95 = 1.96


# ── numeric primitives ───────────────────────────────────────────────────

def _num(value) -> Optional[float]:
    """A real finite number, or None. Never guesses what a value meant."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _bars_from(bars: Sequence[dict], generated_at: Optional[float]) -> List[dict]:
    """The bars strictly after ``generated_at``, or all of them when it is None.

    A plan may only be resolved against price it could not have seen. When the
    timestamp is known the bar it was made on is dropped too, because that bar's
    close is the price the plan was built from.
    """
    if generated_at is None:
        return list(bars)
    return [b for b in bars if (_num(b.get("time")) or 0) > generated_at]


# ── the resolution ───────────────────────────────────────────────────────

def resolve_plan(plan: Dict, bars: Sequence[dict],
                 generated_at: Optional[float] = None,
                 cost_usd: float = DEFAULT_COST_USD,
                 pending_bars: int = DEFAULT_PENDING_BARS,
                 horizon_bars: int = DEFAULT_HORIZON_BARS) -> Dict:
    """Decide one plan against the forward bars. Returns a resolved record.

    The outcome is one of:

      * ``TARGET`` — the entry was reached and the full (last) target hit first.
      * ``STOP``   — the entry was reached and the stop hit first, or on the
        same bar as a target (the stop wins a tie).
      * ``TIMEOUT``— the entry was reached but neither level was, within the
        horizon; closed at the last close.
      * ``EXPIRED``— price never traded through the entry within
        ``pending_bars``; the plan never became a trade and is excluded from
        every rate.

    ``r_gross`` and ``r_net`` are set for every outcome except EXPIRED, where
    they are None because no trade happened. ``targets_reached`` counts how many
    rungs were touched before resolution, which is information the single
    outcome cannot carry.
    """
    out: Dict = {
        "outcome": "EXPIRED",
        "resolved": False,
        "r_gross": None,
        "r_net": None,
        "exit_price": None,
        "resolved_time": None,
        "triggered": False,
        "trigger_time": None,
        "bars_waited": None,
        "bars_held": None,
        "targets_reached": 0,
        "reason": None,
    }

    direction = str(plan.get("direction", "")).upper() if isinstance(plan, dict) else ""
    if direction not in ("LONG", "SHORT"):
        out["reason"] = "direction must be LONG or SHORT"
        return out
    entry = _num(plan.get("entry"))
    stop = _num(plan.get("stop"))
    if entry is None or stop is None:
        out["reason"] = "entry and stop must be numbers"
        return out
    raw_targets = plan.get("targets")
    if isinstance(raw_targets, (int, float)) and not isinstance(raw_targets, bool):
        raw_targets = [raw_targets]
    if not isinstance(raw_targets, list):
        out["reason"] = "targets must be a list"
        return out
    targets: List[float] = []
    for t in raw_targets:
        v = _num(t)
        if v is not None:
            targets.append(v)
    if not targets:
        out["reason"] = "no numeric targets"
        return out

    risk = abs(entry - stop)
    if risk <= 0:
        out["reason"] = "risk is zero"
        return out
    final_target = targets[-1]

    forward = _bars_from(bars, generated_at)
    if not forward:
        out["reason"] = "no forward bars"
        return out

    # Phase 1 — wait for the entry to be reached. Until it is, no trade exists.
    trigger_index: Optional[int] = None
    for i, bar in enumerate(forward[:pending_bars]):
        low, high = _num(bar.get("low")), _num(bar.get("high"))
        if low is None or high is None:
            continue
        if low <= entry <= high:
            trigger_index = i
            break
    if trigger_index is None:
        out["reason"] = ("entry was never reached within %d bars"
                         % pending_bars)
        return out

    out["triggered"] = True
    out["trigger_time"] = forward[trigger_index].get("time")
    out["bars_waited"] = trigger_index

    # Phase 2 — the trade is live. The stop wins a tie on any bar, including the
    # trigger bar, because intra-bar order is unknowable and pessimism is honest.
    for j in range(trigger_index, min(len(forward), trigger_index + horizon_bars)):
        bar = forward[j]
        low, high = _num(bar.get("low")), _num(bar.get("high"))
        if low is None or high is None:
            continue
        if direction == "LONG":
            hit_stop = low <= stop
            reached = [t for t in targets if high >= t]
        else:
            hit_stop = high >= stop
            reached = [t for t in targets if low <= t]
        out["targets_reached"] = max(out["targets_reached"], len(reached))
        if hit_stop:
            return _settle(out, direction, entry, stop, risk, stop,
                           bar.get("time"), cost_usd, j - trigger_index, "STOP")
        hit_final = (high >= final_target) if direction == "LONG" \
            else (low <= final_target)
        if hit_final:
            return _settle(out, direction, entry, stop, risk, final_target,
                           bar.get("time"), cost_usd, j - trigger_index, "TARGET")

    # Neither level within the horizon: close at the last bar the horizon covers.
    last_j = min(len(forward), trigger_index + horizon_bars) - 1
    last_bar = forward[last_j]
    exit_price = _num(last_bar.get("close"))
    if exit_price is None:
        out["reason"] = "the horizon's last bar has no close"
        return out
    return _settle(out, direction, entry, stop, risk, exit_price,
                   last_bar.get("time"), cost_usd, last_j - trigger_index,
                   "TIMEOUT")


def _settle(out: Dict, direction: str, entry: float, stop: float, risk: float,
            exit_price: float, time_value, cost_usd: float, held: int,
            outcome: str) -> Dict:
    """Stamp the outcome, the exit, and the gross/net R for one resolution."""
    gross_delta = exit_price - entry
    if direction == "SHORT":
        gross_delta = -gross_delta
    out["outcome"] = outcome
    out["resolved"] = True
    out["exit_price"] = round(exit_price, 2)
    out["resolved_time"] = time_value
    out["bars_held"] = held
    out["r_gross"] = round(gross_delta / risk, 3)
    # gross_delta is already directional, so the spread is a flat debit.
    out["r_net"] = round((gross_delta - cost_usd) / risk, 3)
    out["reason"] = "ok"
    return out


# ── statistics (mirrors tracker/validation_gate, kept local by design) ────

def wilson(k: int, n: int, z: float = Z_95) -> tuple:
    """Wilson score interval for a binomial rate. Returns (low, high).

    Identical to ``tracker.validation_gate.wilson``; duplicated so ``advisor``
    stays importable without ``tracker``. A test asserts the two agree.
    """
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    d = 1.0 + z * z / n
    centre = p + z * z / (2.0 * n)
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return (max(0.0, (centre - half) / d), min(1.0, (centre + half) / d))


def break_even_win_rate(avg_win_r: float, avg_loss_r: float) -> Optional[float]:
    """The win rate at which net R is zero. None when no rate can break even."""
    denom = avg_win_r - avg_loss_r
    if denom <= 0:
        return None
    p = -avg_loss_r / denom
    return p if 0.0 <= p <= 1.0 else None


def summarize(rows: Sequence[Dict],
              min_scored: int = DEFAULT_MIN_SCORED) -> Dict:
    """Score resolved plans into the honest numbers a reader can act on.

    Only TARGET and STOP count as scored; a TIMEOUT is neither a win nor a loss,
    and an EXPIRED plan never became a trade — both are excluded from the rate,
    exactly as the tracker's summary does. Below ``min_scored`` trades the rate
    is withheld (``rate_stated: False``) rather than reported thin, and the
    Wilson lower bound is always shown next to the point estimate because the
    pessimistic end is the honest reading.
    """
    scored = [r for r in rows if r.get("outcome") in ("TARGET", "STOP")]
    wins = [r for r in scored if r["outcome"] == "TARGET"]
    losses = [r for r in scored if r["outcome"] == "STOP"]
    n, k = len(scored), len(wins)
    timeouts = [r for r in rows if r.get("outcome") == "TIMEOUT"]
    # Only plans that actually became a trade carry R; an EXPIRED plan never
    # filled, so it is not a trade and must not dilute the per-trade figure.
    rs = [r["r_net"] for r in rows if r.get("r_net") is not None]
    total_r = sum(rs)
    traded = len(rs)
    avg_win = (sum(r["r_net"] for r in wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(r["r_net"] for r in losses) / len(losses)) if losses else 0.0
    be = break_even_win_rate(avg_win, avg_loss)
    lo, hi = wilson(k, n)
    stated = n >= min_scored
    return {
        "resolved": len([r for r in rows if r.get("resolved")]),
        "traded": traded,
        "scored": n,
        "wins": k,
        "losses": len(losses),
        "timeouts": len(timeouts),
        "expired": len([r for r in rows if r.get("outcome") == "EXPIRED"]),
        "win_rate": (k / n) if n else None,
        "wilson_low": lo if n else None,
        "wilson_high": hi if n else None,
        "break_even": be,
        "avg_win_r": round(avg_win, 3),
        "avg_loss_r": round(avg_loss, 3),
        "total_r": round(total_r, 2),
        "r_per_trade": (total_r / traded) if traded else 0.0,
        "min_scored": min_scored,
        "rate_stated": stated,
        "note": ("A resolved rate, not a promise. EXPIRED plans never traded and "
                 "TIMEOUTs are neither wins nor losses, so neither is counted in "
                 "the rate; R is averaged over traded plans only. Below %d scored "
                 "trades no rate is stated." % min_scored),
    }


def resolve_log(records: Sequence[Dict], bars_by_tf: Dict[str, Sequence[dict]],
                tf: str = "M15", cost_usd: float = DEFAULT_COST_USD,
                pending_bars: int = DEFAULT_PENDING_BARS,
                horizon_bars: int = DEFAULT_HORIZON_BARS) -> List[Dict]:
    """Resolve every plan in a plan-log dump against one frame's forward bars.

    Each ``record`` is a line of ``run/smc_engine_plans.jsonl``: it carries
    ``generated_at_utc`` and a ``plans`` list. Returns one resolved row per plan,
    tagged with the record's timestamp so a caller can bucket by period. Plans
    the log could not have resolved (no bars after them) come back EXPIRED with
    the reason recorded, never dropped silently.
    """
    bars = list(bars_by_tf.get(tf) or [])
    rows: List[Dict] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        generated_at = _num(record.get("generated_at_utc"))
        plans = record.get("plans") or []
        if not isinstance(plans, list):
            continue
        for plan in plans:
            if not isinstance(plan, dict):
                continue
            resolved = resolve_plan(plan, bars, generated_at=generated_at,
                                    cost_usd=cost_usd,
                                    pending_bars=pending_bars,
                                    horizon_bars=horizon_bars)
            resolved["generated_at_utc"] = generated_at
            resolved["plan"] = {
                "direction": plan.get("direction"),
                "entry": plan.get("entry"),
                "stop": plan.get("stop"),
                "targets": plan.get("targets"),
                "confidence": plan.get("confidence"),
            }
            rows.append(resolved)
    return rows


# ── CLI: resolve a real plan log against a real bar file ─────────────────

def _load_bars_csv(path: str, limit: int = 200000) -> List[dict]:
    """Bars from a Dukascopy-style CSV (epoch-ms timestamp, OHLCV)."""
    import csv
    import gzip
    opener = gzip.open if path.endswith(".gz") else open
    out: List[dict] = []
    with opener(path, "rt") as fh:
        for i, row in enumerate(csv.DictReader(fh)):
            if i >= limit:
                break
            try:
                ts = int(row["timestamp"])
                if ts > 10 ** 11:      # epoch ms -> seconds
                    ts //= 1000
                out.append({"time": ts, "open": float(row["open"]),
                            "high": float(row["high"]), "low": float(row["low"]),
                            "close": float(row["close"])})
            except (ValueError, KeyError):
                continue
    return out


def _load_log(path: str) -> List[Dict]:
    """Plan-log records from a JSONL dump, skipping unreadable lines."""
    import json
    out: List[Dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except ValueError:
                    continue
                if isinstance(parsed, dict):
                    out.append(parsed)
    except OSError:
        return []
    return out


def main(argv: Optional[list] = None) -> int:
    """Resolve a plan log against a bar file and print the honest summary.

    Example:

        python3 advisor/smc_resolve.py --log run/smc_engine_plans.jsonl \\
            --bars research/data/xauusd_m1/2025.csv.gz --tf M15
    """
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Resolve SMC plan logs against the forward tape")
    parser.add_argument("--log", required=True,
                        help="the plan log (JSONL) the engine writes")
    parser.add_argument("--bars", required=True,
                        help="a bar CSV (epoch-ms or epoch-s timestamps)")
    parser.add_argument("--tf", default="M15",
                        help="frame the bars are on (labels the output)")
    parser.add_argument("--cost", type=float, default=DEFAULT_COST_USD)
    parser.add_argument("--pending-bars", type=int,
                        default=DEFAULT_PENDING_BARS)
    parser.add_argument("--horizon-bars", type=int,
                        default=DEFAULT_HORIZON_BARS)
    parser.add_argument("--min-scored", type=int, default=DEFAULT_MIN_SCORED)
    args = parser.parse_args(argv)

    records = _load_log(args.log)
    bars = _load_bars_csv(args.bars)
    if not records:
        print("no plan records in %s" % args.log)
        return 0
    if not bars:
        print("no bars in %s" % args.bars)
        return 1

    rows = resolve_log(records, {args.tf: bars}, tf=args.tf, cost_usd=args.cost,
                       pending_bars=args.pending_bars,
                       horizon_bars=args.horizon_bars)
    summary = summarize(rows, min_scored=args.min_scored)
    print("SMC plan resolution · frame=%s · %d plans · %d bars"
          % (args.tf, len(rows), len(bars)))
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
