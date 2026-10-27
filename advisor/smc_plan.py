"""The deterministic SMC plan: the measured edge, built without a model.

``research/smc_exit_study.py`` measured where this school's zones actually have
an edge and found the answer is *a near target*. The same plans that lose with
targets at 2R/3R — 21-24% against a 25-28% break-even — clear break-even when
resolved to **0.5R**: in-sample 0.778 against 0.751, out-of-sample 0.760 against
0.703, and in each of the five years separately. A geometry-matched control
entered at the market with the same risk lands on the random-walk baseline and
fails in every year, so the edge is the zone's *location*, not gold's tendency to
mean-revert.

Until now that finding was only a fact on the sheet; the engine's plan came from
the model, which targets 1:2 by instruction. This module turns the measurement
into the plan it measured: the nearest order block or fair-value gap on the
structure-bias side, the stop one ATR behind the zone, and a target at
``NEAR_TARGET_R`` — the geometry the study resolved, reproduced exactly.

Why a second plan and not a change to the model's: the model's plan is a reading
of the same sheet with judgement on top, and it is not what was measured. Shipping
the *measured* geometry as its own, deterministic plan means the number a reader
acts on is the number that cleared the gate, and it carries no model, so it works
when the provider is down, rate-limited or unconfigured.

Design notes, mirroring ``research/smc_zone_study.build_plan`` on purpose:

  * **Pure, no network, no model, no wall clock.** Every value is a function of
    the fact sheet it is handed, so the plan is reproducible and testable.
  * **The same zones the sheet computed**, never one the model described in
    prose. The entry is a zone's midpoint, so it is anchored by construction.
  * **Off by default.** ``ASTRA_SMC_DETERMINISTIC_PLAN=1`` turns it on. A plan
    that is evidence about a measurement is opt-in, the way every other
    measurement in this project is.

Nothing here forms an opinion about the market or places an order. It reproduces
a measurement.
"""

from __future__ import annotations

import os
from typing import Optional

# The target the exit study resolved, in R. 0.5R is where the Wilson lower bound
# cleared break-even on both halves and in every year; every target from 1R out
# failed in-sample. Changing this without re-running the study would put a
# number on the sheet that no measurement supports, so the two move together.
NEAR_TARGET_R = float(os.environ.get("ASTRA_SMC_NEAR_TARGET_R", "0.5"))

# How far behind the zone the stop sits, in ATRs — the study's geometry, which
# is what was resolved. Not a tuning knob: a different stop is a different plan
# and would need its own study.
STOP_ATR = float(os.environ.get("ASTRA_SMC_STOP_ATR", "1.0"))

# The decision frame. The study measured M15 and its H1 re-run reached the same
# verdict, but the numbers carried on the sheet are M15's, so M15 is the frame
# this plan is built from unless the operator says otherwise.
DECISION_TF = os.environ.get("ASTRA_SMC_DECISION_TF", "M15")


def _num(value) -> Optional[float]:
    """A real finite number, or None. Never guesses what a value meant."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _zones(facts: dict) -> list:
    """Every (low, high, kind) zone the sheet computed, order blocks first."""
    out = []
    for ob in facts.get("order_blocks") or []:
        lo, hi = _num(ob.get("low")), _num(ob.get("high"))
        if lo is not None and hi is not None and hi > lo:
            out.append((lo, hi, "order block (%s)" % ob.get("kind")))
    for f in facts.get("fair_value_gaps") or []:
        lo, hi = _num(f.get("low")), _num(f.get("high"))
        if lo is not None and hi is not None and hi > lo:
            out.append((lo, hi, "fair-value gap (%s)" % f.get("kind")))
    return out


def build_plan(facts_by_tf: dict, tf: str = DECISION_TF) -> Optional[dict]:
    """The deterministic near-target plan, or None when no zone qualifies.

    Returns a plan shaped exactly like the model's — same keys, same
    ``confidence_validated: False`` stamp — so it flows through the same reply
    and the same phone panel. ``source`` marks it as the measured geometry, so a
    reader can always tell which plan was measured and which was reasoned.

    The direction comes from the frame's structure bias; the entry is the
    midpoint of the **nearest** zone on the correct side of price; the stop sits
    one ATR behind the zone; the single target is ``NEAR_TARGET_R`` away. None of
    these is a judgement — they are the study's builder, reproduced.
    """
    facts = (facts_by_tf or {}).get(tf)
    if not isinstance(facts, dict):
        return None
    close = _num(facts.get("close"))
    atr = _num(facts.get("atr"))
    bias = (facts.get("structure") or {}).get("bias")
    if close is None or atr is None or atr <= 0:
        return None
    if bias not in ("bullish", "bearish"):
        return None

    zones = _zones(facts)
    if not zones:
        return None

    if bias == "bullish":
        # The nearest zone whose top is below price: where a buyer steps in.
        below = [z for z in zones if z[1] < close]
        if not below:
            return None
        lo, hi, kind = max(below, key=lambda z: z[1])
        entry = (lo + hi) / 2.0
        stop = lo - STOP_ATR * atr
        if stop >= entry:
            return None
        risk = entry - stop
        direction, target = "LONG", entry + NEAR_TARGET_R * risk
        anchor_side = "below"
    else:
        above = [z for z in zones if z[0] > close]
        if not above:
            return None
        lo, hi, kind = min(above, key=lambda z: z[0])
        entry = (lo + hi) / 2.0
        stop = hi + STOP_ATR * atr
        if stop <= entry:
            return None
        risk = stop - entry
        direction, target = "SHORT", entry - NEAR_TARGET_R * risk
        anchor_side = "above"

    reward = abs(target - entry)
    return {
        "direction": direction,
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "targets": [round(target, 2)],
        "target": round(target, 2),
        "risk": round(risk, 2),
        "reward": round(reward, 2),
        "reward_risk": round(reward / risk, 2) if risk else None,
        "confidence": None,
        "rationale": ("خطة هندسية مقيسة: الدخول عند منتصف %s، والوقف خلفه "
                      "بمقدار ATR، والهدف %.2fR — وهي الهندسة التي اجتازت "
                      "التعادل في الدراسة." % (kind, NEAR_TARGET_R)),
        "sweep": "",
        "poi": kind,
        "anchored": True,
        "anchor_timeframe": tf,
        "anchor_zone": kind,
        "anchor_side": anchor_side,
        "confidence_validated": False,
        # The mark that separates the measured geometry from the reasoned one.
        "source": "measured-geometry",
        "measured_target_r": NEAR_TARGET_R,
        "measured_record_source": "research/reports/smc_exit_study.md",
    }


def enabled() -> bool:
    """Whether the deterministic plan is switched on (default: no)."""
    raw = os.environ.get("ASTRA_SMC_DETERMINISTIC_PLAN", "0")
    return str(raw).strip().lower() not in ("0", "false", "no", "off", "")
