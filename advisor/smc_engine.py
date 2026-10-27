#!/usr/bin/env python3
"""ASTRA SMC engine: an isolated Smart Money Concepts / ICT analyst for XAUUSD.

What it is, and what it is not:

  * It reads the closed bars the bridge serves, computes a deterministic SMC
    **fact sheet** (``advisor/smc_read.py``: swing structure, BOS/CHoCH, the
    premium/discount split, equal-highs/lows and sweeps, fair-value gaps, order
    blocks, session ranges), and hands that sheet to an external model with an
    SMC/ICT persona. The model reasons over facts it did not author; it never
    invents a level, because it is not asked for one — the levels are computed.
  * It is fully isolated. Its own process, its own port (8795), its own log. It
    reads the bridge over loopback and writes nothing back: no ledger write, no
    engine call, no order of any kind. If it dies, nothing else does.
  * Every number it returns is the model's claim, not a measurement. The reply
    is stamped ``validated: false`` and the confidence is labelled an estimate.
    Nothing here changes the deterministic pipeline's behaviour.
  * The model's plan is checked for coherence (stop on the losing side, targets
    in travel order, R:R at least 1:2 as the methodology demands). An incoherent
    plan is dropped with the reason recorded, never silently repaired.
  * Without a provider key it still answers with the factual sheet and
    ``configured: false`` so a caller degrades instead of erroring.

Opt in with ``ASTRA_ENABLE_SMC=1`` (off by default); disable with
``ASTRA_DISABLE_SMC=1``.

Run once and print the plan:

    GROQ_API_KEY=gsk_... python3 advisor/smc_engine.py --once

Serve it:

    GROQ_API_KEY=gsk_... python3 advisor/smc_engine.py --port 8795
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Sequence

import ai_advisor
import smc_plan
import smc_read

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SCHEMA_VERSION = "1.0"
SYMBOL = ai_advisor.SYMBOL
BRIDGE_BASE = ai_advisor.BRIDGE_BASE

# Frames the engine reasons across, coarsest last. The lower frames time the
# entry and confirm the change of character; the higher frames set the bias.
ALL_TIMEFRAMES = ("M5", "M15", "M30", "H1", "H4")


def _resolve_timeframes() -> tuple[str, ...]:
    """The frames handed to the model, from ``ASTRA_SMC_TIMEFRAMES``.

    Default: the five structural frames. A comma-separated allow-list narrows
    it, keeping canonical order; unknown labels are dropped and an empty result
    falls back to the full list, because a typo must not blind the engine.
    """
    raw = os.environ.get("ASTRA_SMC_TIMEFRAMES", "").strip()
    if not raw:
        return ALL_TIMEFRAMES
    wanted = {tok.strip().upper() for tok in raw.split(",") if tok.strip()}
    chosen = tuple(tf for tf in ALL_TIMEFRAMES if tf in wanted)
    return chosen or ALL_TIMEFRAMES


TIMEFRAMES = _resolve_timeframes()
BARS_PER_TF = int(os.environ.get("ASTRA_SMC_BARS", "220"))
MIN_BARS = 30

# Each decision frame's length in minutes, so the engine can judge how far
# behind the wall clock its newest closed bar is. Kept local rather than
# imported from the bridge: `advisor` must stay importable without `bridge`.
_TF_MINUTES = {"M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}

# How many whole bars behind the clock a frame may be and still count as live.
# The bridge stamps each bar with its **open** time, so a healthy closed-bar
# read sits between 1 and 2: the newest closed bar opened one interval ago when
# it just closed, and two by the time the next forming bar is itself about to
# close. Above 2 means a closed bar the engine should have seen is absent — the
# state in which structure can describe a market that has already moved.
MAX_LAG_BARS = int(os.environ.get("ASTRA_SMC_MAX_LAG_BARS", "2"))

MAX_PLANS = int(os.environ.get("ASTRA_SMC_MAX_PLANS", "2"))
CACHE_TTL = float(os.environ.get("ASTRA_SMC_CACHE_TTL", "60"))
# The methodology demands at least 1:2 reward-to-risk; a plan below this is
# dropped, not shown with a caveat.
MIN_RR = float(os.environ.get("ASTRA_SMC_MIN_RR", "2.0"))

# The prompt must fit the provider's per-minute token ceiling. The Groq free
# tier (the default provider) allows 8000 tokens per minute, counted on the
# request *including* the system prompt, so a fact sheet that reads beautifully
# is still useless if the call is refused with HTTP 413 before the model ever
# sees it. The budget is deliberately below the ceiling: the provider counts
# tokens with its own tokenizer, which runs a little larger than the character
# estimate below, and a request that lands exactly on the limit is a coin flip.
PROMPT_TOKEN_BUDGET = int(os.environ.get("ASTRA_SMC_TOKEN_BUDGET", "6000"))
# Characters per token for a JSON fact sheet of numbers, keys and short Arabic
# strings. Calibrated on the live failure: a 47628-character prompt was refused
# at 14254 tokens, i.e. ~3.34 characters per token. The value below is set under
# that ratio so the estimate runs *larger* than the provider's own count: over-
# estimating costs a little fact sheet, under-estimating costs the whole request
# to a 413.
CHARS_PER_TOKEN = float(os.environ.get("ASTRA_SMC_CHARS_PER_TOKEN", "3.2"))
# How many headlines to hand the model. The feed carries many; the first few
# are the ones that matter and the rest only cost tokens.
NEWS_IN_PROMPT = int(os.environ.get("ASTRA_SMC_NEWS", "4"))

LOG_PATH = os.environ.get(
    "ASTRA_SMC_LOG", os.path.join(ROOT, "run", "smc_engine_plans.jsonl"))

# The engine's own measured record — the number a user should weigh the model's
# confidence against. This is not produced here and not produced by the model:
# it is the verdict of ``research/smc_zone_study.py``, which built plans
# deterministically from this same fact sheet (no model in the loop), resolved
# them over the real Dukascopy XAUUSD M15 book (2021-2025) with
# ``advisor/smc_resolve.py``, and split in-sample/out-of-sample exactly as
# ``tracker/validation_gate.py`` does. The numbers are pasted in verbatim, the
# same contract ``advisor/playbook.py`` holds its schools to.
#
# The honest headline: the zones do **not** clear break-even on either half.
# A symmetric 1R/1R control resolved at ~49%, so the resolver is not biased and
# this is a real finding, not an accounting artefact. If the study is re-run and
# the numbers move, update this block in the same commit — the tests pin the
# shape (a cited, non-clearing record), not the values, precisely so the values
# can be corrected without rewriting tests.
MEASURED_RECORD: dict = {
    "source": "research/reports/smc_zone_study.md",
    "script": "research/smc_zone_study.py",
    "scope": "deterministic zone plans, XAUUSD M15, 2021-2025 (OOS 2025)",
    "n": 9845,
    "win_pct": 23.2,
    "wilson_low_pct": 22.3,
    "break_even_pct": 27.9,
    "r_per_trade": -0.088,
    "oos_n": 1960,
    "oos_win_pct": 24.4,
    "oos_wilson_low_pct": 22.5,
    "oos_break_even_pct": 26.4,
    "control_win_pct": 49.3,
    "clears_gate": False,
    "why_not": ("the Wilson lower bound sits below break-even in-sample and "
                "out-of-sample; the 2025 point estimate is faintly positive "
                "but not distinguishable from a losing rate, and the "
                "confluence filter does not change that"),
    "note": ("A reading, not an edge. Shown so the model's confidence is never "
             "mistaken for a measurement."),
    # The same zones, resolved to a *nearer* target. The headline above is about
    # the shipped 2R/3R geometry; this block answers the narrower question the
    # study never asked — at which target does the reading clear break-even? —
    # and finds that it does, at 0.5R, in every year including out of sample.
    # The number that matters is the gap to the geometry-matched control, which
    # is entered at the market with the same risk: the zone's *location* is worth
    # about twelve points of win rate, and that is the first measured edge this
    # school has shown. It is still a reading of a plan that the engine does not
    # yet build — the plan builder still targets 2R/3R — so it is evidence, not
    # a signal.
    "exit_study": {
        "source": "research/reports/smc_exit_study.md",
        "script": "research/smc_exit_study.py",
        "scope": ("deterministic zone plans, XAUUSD M15, 2021-2025 (OOS 2025), "
                  "same plans resolved to a nearer final target"),
        "target_r": 0.5,
        "n_in_sample": 8934,
        "win_pct_in_sample": 78.6,
        "wilson_low_in_sample_pct": 77.8,
        "break_even_in_sample_pct": 75.1,
        "n_oos": 2188,
        "win_pct_oos": 77.8,
        "wilson_low_oos_pct": 76.0,
        "break_even_oos_pct": 70.3,
        "control_win_pct_in_sample": 64.5,
        "control_win_pct_oos": 64.6,
        "clears_gate": True,
        "why": ("a 0.5R target clears break-even in-sample and out-of-sample "
                "on the Wilson lower bound, and in each of the five years "
                "separately, while a geometry-matched control entered at the "
                "market fails in every year"),
        "note": ("The zones locate reactions, not journeys. The edge lives at a "
                 "near target; asking the plan for 2R/3R is what made it lose. "
                 "Evidence, not a signal: the engine's own plan builder still "
                 "targets 2R/3R."),
    },
}


def measured_record() -> dict:
    """The engine's measured record, as a fresh dict a caller may embed."""
    return dict(MEASURED_RECORD)


# The SMC/ICT persona, adapted to this project's honesty contract. The user's
# original prompt asked the model to "acknowledge" and to analyse charts; here
# the model is one step in a pipeline, so the instructions are tightened: it
# reasons over the supplied facts, never invents a level, always places the stop
# behind the structural point, and never promises returns. The persona and the
# framework (HTF bias -> liquidity sweep -> LTF CHoCH -> POI entry -> risk) are
# preserved; the "read my chart" framing is replaced with "reason over this
# fact sheet", because a hallucinated level is the failure this design prevents.
_SYSTEM_PROMPT = (
    "You are an institutional-grade quantitative analyst for Gold (XAU/USD) "
    "whose entire framework is Smart Money Concepts (SMC) and Inner Circle "
    "Trader (ICT). You do NOT use retail indicators (RSI, MACD, moving "
    "averages). You read market structure, liquidity, and institutional "
    "footprints.\n"
    "You are given a deterministic SMC fact sheet computed from closed bars: "
    "per timeframe the swing structure with its last BOS/CHoCH, the "
    "premium/discount split of the active leg, equal-highs/equal-lows "
    "liquidity clusters, recent sweeps, fair-value gaps (filled or unfilled), "
    "order blocks and session ranges. These are facts; you interpret them.\n"
    "Language: every human-readable field in Arabic. JSON keys and direction "
    "values stay in English.\n"
    "Method — follow this sequence and say which step produced the plan:\n"
    "1. Bias: read the higher timeframes (H4, H1) and state the true "
    "direction. A CHoCH is the first body close beyond a key structural level "
    "against the prior trend; a BOS is continuation with it.\n"
    "2. Liquidity: a trade requires a swept pool. Name the pool (equal "
    "highs/lows, a session high/low) and when it was swept.\n"
    "3. Confirmation: the lower timeframes (M15, M5) must show a CHoCH in the "
    "intended direction after the sweep.\n"
    "4. Point of interest: the entry sits at an order block or the 50% "
    "equilibrium of a fair-value gap that produced the LTF CHoCH. Buy in "
    "discount, sell in premium — never the reverse.\n"
    "5. Risk: the stop sits safely behind the structural high/low that caused "
    "the sweep. Targets aim at the next internal or external liquidity pool. "
    "Reward-to-risk must be at least 1:2.\n"
    "Rules:\n"
    "- Anchor every price to the fact sheet. Use only the levels given; never "
    "invent a price, a swing, a gap or an event. The entry MUST sit inside an "
    "order block or a fair-value gap listed there: a plan whose entry falls in "
    "empty space is discarded automatically.\n"
    "- A plan is coherent only if the stop sits on the losing side of the "
    "entry and the targets run in the winning direction, nearest first. For a "
    "LONG: stop < entry < target1 < target2 ... For a SHORT: stop > entry > "
    "target1 > target2 ...\n"
    "- Give each plan one to three targets, in R-multiple order. The last is "
    "the full target.\n"
    "- confidence is YOUR OWN probability (0..1) that the plan reaches its "
    "final target before its stop. It is an estimate, not a measured number; "
    "say why in confidence_note. Do not copy a fact-sheet value.\n"
    "- engine_measured_record is the project's own verdict on this fact sheet, "
    "measured on real gold data. It is a finding, not yours to restate as your "
    "own confidence. If it does not clear the gate, you may still give the "
    "plan, but say plainly that the record is unflattering.\n"
    "- exit_study shows the same zones resolving to a nearer target (0.5R) "
    "clear break-even in and out of sample, where a market entry does not: the "
    "zone's edge is at a *near* target. Prefer targets the structure can "
    "plausibly reach — the next internal liquidity — and when the only honest "
    "target is near, say so rather than stretching to 1:2. A reachable 0.5R "
    "that clears beats an unreachable 2R that does not.\n"
    "- Warn explicitly when a high-impact event (CPI, FOMC, NFP) is imminent "
    "or was flagged; SMC structure can be invalidated by news.\n"
    "- tape carries the freshness of the bars, with a per-frame lag_bars. The "
    "forming bar is excluded by design, so a lag_bars of 1 or 2 is normal. If "
    "a frame reads lagging or degraded, the structure may describe a market "
    "that has already moved: say so in market_read and treat the setup as "
    "provisional.\n"
    "- A missing timeframe is not grounds to refuse; reason from the frames "
    "you have and note the gap at most in passing.\n"
    "- Return an empty plans list when the structure genuinely offers no "
    "coherent setup; say why in market_read. Do not force a setup.\n"
    "- This is analysis for a research tool, not advice. Do not promise "
    "returns.\n"
    "Reply with JSON only, keys: market_read (three or four Arabic sentences "
    "covering bias, the sweep, the LTF confirmation and the POI), bias (LONG, "
    "SHORT or NEUTRAL), news_caution (true or false), key_event (the Arabic "
    "name of the event you are cautious about, or empty string), plans (a "
    "list, possibly empty). Each plan: direction (LONG or SHORT), entry "
    "(number), stop (number), targets (list of numbers, nearest first), "
    "confidence (0..1), rationale (one or two Arabic sentences naming the "
    "sweep and the POI), sweep (the Arabic phrase for the swept liquidity), "
    "poi (the Arabic phrase for the block or gap used)."
)


def _bar_lag_bars(newest_closed: float, tf: str, now: float) -> float | None:
    """How many whole bars of ``tf`` behind ``now`` the newest closed bar is.

    ``None`` when the bar time is unusable. The value is a float so a frame can
    be a fraction of a bar behind, which is normal: the forming bar is always
    excluded, so the newest bar the engine sees is the one that just closed.
    """
    if not isinstance(newest_closed, (int, float)) or newest_closed <= 0:
        return None
    minutes = _TF_MINUTES.get(tf)
    if not minutes:
        return None
    return max(0.0, (now - float(newest_closed)) / (minutes * 60.0))


def fetch_all_bars(now: float | None = None) -> tuple[dict, dict]:
    """Closed bars per timeframe, plus an honest freshness report per frame.

    Returns ``(bars_by_tf, tape)``. The bridge stamps every candle reply with
    the freshness it judged upstream; this reads those stamps instead of
    discarding them, so the engine can tell a live tape from a lagging one
    rather than treating both as equally current. A frame the bridge answered
    with an error — a stale feed, an absent terminal — is recorded as such and
    carries no bars, which is the difference between "no read" and "a read of
    nothing".
    """
    now = time.time() if now is None else now
    out: dict = {}
    tape: dict = {}
    for tf in TIMEFRAMES:
        url = "%s/v1/candles?tf=%s&limit=%d" % (BRIDGE_BASE, tf, BARS_PER_TF)
        payload = ai_advisor._get_json(url)
        if not isinstance(payload, dict):
            tape[tf] = {"status": "unreachable"}
            continue
        if isinstance(payload.get("payload"), dict):
            payload = payload["payload"]
        # An error envelope carries no bars by design; name why rather than
        # letting the frame vanish into "missing".
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict) and error.get("code"):
            tape[tf] = {"status": "error", "code": str(error.get("code")),
                        "message": str(error.get("message", ""))}
            continue
        bars = payload.get("bars") or payload.get("candles") or []
        if not isinstance(bars, list) or len(bars) < MIN_BARS:
            tape[tf] = {"status": "insufficient",
                        "count": len(bars) if isinstance(bars, list) else 0}
            continue
        newest = payload.get("newest_closed_time")
        if newest is None:
            newest = bars[-1].get("time")
        lag = _bar_lag_bars(newest, tf, now)
        entry = {"status": "ok", "bars": len(bars),
                 "newest_closed_time": newest,
                 "freshness": payload.get("freshness") or "UNKNOWN",
                 "lag_bars": None if lag is None else round(lag, 2)}
        if lag is not None and lag > MAX_LAG_BARS:
            entry["status"] = "lagging"
        tape[tf] = entry
        out[tf] = bars
    return out, tape


def tape_verdict(tape: dict, timeframes: Sequence[str]) -> dict:
    """A single honest word for the tape the reads rest on.

    ``live`` when every frame is present and none lags past the allowance;
    ``degraded`` when a frame is missing or behind; ``stale`` when nothing
    usable came back at all. The engine still answers on a degraded tape — a
    reading of a thin tape beats no reading — but it says so, and on a stale
    tape it refuses the plan outright rather than describing a market it cannot
    see.
    """
    frames = [tape.get(tf) or {"status": "unreachable"} for tf in timeframes]
    ok = [f for f in frames if f.get("status") == "ok"]
    bad = [f for f in frames if f.get("status") != "ok"]
    if not ok:
        state = "stale"
    elif bad:
        state = "degraded"
    else:
        state = "live"
    return {
        "state": state,
        "frames_read": sorted(f for f in timeframes
                              if (tape.get(f) or {}).get("status") == "ok"),
        "frames_degraded": sorted(f for f in timeframes
                                  if (tape.get(f) or {}).get("status") != "ok"),
        "max_lag_bars": MAX_LAG_BARS,
        "per_frame": {tf: tape.get(tf) for tf in timeframes},
    }


def _measured_plan(snapshot: dict) -> dict | None:
    """The deterministic near-target plan, or None when it is off or absent.

    This is the one plan on the sheet that is a *measurement* rather than a
    reading: ``smc_plan.build_plan`` reproduces the geometry
    ``research/smc_exit_study.py`` resolved and found clearing break-even. It
    carries no model, so it is computed here and reaches the reply whatever the
    provider did — the reading can fail while the measurement still stands.

    The distance the entry sits from price is attached the same way the model's
    plans get it, so the phone panel renders both identically.
    """
    if not smc_plan.enabled():
        return None
    facts_by_tf = (snapshot.get("facts") or {}).get("timeframes") or {}
    plan = smc_plan.build_plan(facts_by_tf)
    if plan is None:
        return None
    price = snapshot.get("price")
    if price is not None:
        plan["distance_from_price"] = round(plan["entry"] - price, 2)
    return plan


def build_snapshot() -> dict:
    """Collect the facts the model reasons over. No opinion is formed here."""
    snapshot: dict = {
        "symbol": SYMBOL,
        "captured_at_utc": int(time.time()),
        "price": None,
        "facts": None,
        "tape": None,
        "news": [],
        "news_available": False,
        "events": [],
        "events_available": False,
        "measured_record": measured_record(),
    }

    tick = ai_advisor._get_json("%s/v1/tick?symbol=%s" % (BRIDGE_BASE, SYMBOL))
    if tick and isinstance(tick.get("payload"), dict):
        snapshot["price"] = tick["payload"].get("last")

    bars_by_tf, tape = fetch_all_bars()
    snapshot["tape"] = tape_verdict(tape, TIMEFRAMES)
    snapshot["facts"] = smc_read.build(bars_by_tf, TIMEFRAMES)

    headlines = ai_advisor.recent_news()
    snapshot["news"] = headlines
    snapshot["news_available"] = bool(headlines)
    events = ai_advisor.upcoming_events()
    snapshot["events"] = events
    snapshot["events_available"] = bool(events)
    return snapshot


def _prompt_view(snapshot: dict) -> dict:
    """The snapshot as the model sees it: the fact sheet, price, news.

    The engine's **measured record** rides along so the model can weigh its own
    confidence against the number the project actually measured on this same
    fact sheet. The persona is told the record is the project's finding and not
    the model's to restate as its own.
    """
    news = snapshot.get("news") or []
    return {
        "symbol": snapshot["symbol"],
        "price": snapshot["price"],
        "smc_facts": snapshot["facts"],
        "tape": snapshot.get("tape"),
        "engine_measured_record": snapshot.get("measured_record"),
        "news": news[:NEWS_IN_PROMPT],
        "events": snapshot["events"],
    }


def _token_estimate(text: str) -> int:
    """The provider's token count for a prompt, as a safe over-estimate.

    No tokenizer ships with the project and the provider's own is not callable
    offline, so the count is characters divided by a conservative ratio. The
    ratio is calibrated on real fact sheets: over-estimating costs a little
    fact sheet, under-estimating costs the whole request to a 413.
    """
    return int(len(text) / CHARS_PER_TOKEN) + 1


def _trim_facts(facts: dict, keep_plans: int) -> dict:
    """The fact sheet with the oldest list entries dropped.

    Each per-timeframe list (fair-value gaps, order blocks, sweeps, liquidity
    clusters) is already capped at a handful; when the whole sheet still runs
    long, the least recent entries of each are the ones to go. The most recent
    entry in every list survives at any keep count, so the structure the model
    reasons over — the current gap, the current block, the current sweep — is
    never the thing that was trimmed away. The lists are shortened in place on a
    copy; the caller's snapshot is untouched.
    """
    trimmed: dict = {}
    for tf, read in (facts.get("timeframes") or {}).items():
        if not isinstance(read, dict):
            trimmed[tf] = read
            continue
        out: dict = {}
        for key, value in read.items():
            if isinstance(value, list) and len(value) > keep_plans:
                out[key] = value[-keep_plans:]
            elif isinstance(value, dict):
                # `liquidity` nests its own lists under equal_highs/equal_lows/
                # sweeps; trim those too, or the deepest lists never shrink.
                out[key] = {
                    k: (v[-keep_plans:] if isinstance(v, list)
                        and len(v) > keep_plans else v)
                    for k, v in value.items()
                }
            else:
                out[key] = value
        trimmed[tf] = out
    return dict(facts, timeframes=trimmed)


def _slim(value):
    """The fact sheet with empty containers and nulls dropped.

    Every call pays for the whole sheet, and the per-frame read is padded with
    ``null``s and empty lists (no sweep on this frame, no gap, no cluster).
    Those carry no information the model can use — an absent key says the same
    thing as an empty one — but the provider counts each as tokens on every one
    of the ~96 calls a day. Numbers are passed through untouched: a rounded
    price would no longer sit inside the zone it was measured from, and the
    entry-in-zone check that discards a floating plan reads these exact values.
    """
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            slimmed = _slim(item)
            if slimmed is None or slimmed == "" or slimmed == [] or slimmed == {}:
                continue
            out[key] = slimmed
        return out
    if isinstance(value, list):
        return [_slim(item) for item in value]
    return value


def build_prompt(snapshot: dict) -> str:
    """The system prompt plus the fact sheet, sized to the provider's budget.

    Compact JSON (no indentation) halves the payload against the pretty-printed
    form at no cost to the model — whitespace is tokens too, and a 413 refuses
    the request before the model reads a single fact. Empty containers and nulls
    are dropped (see ``_slim``) for the same reason. If the sheet is still over
    ``PROMPT_TOKEN_BUDGET``, the oldest entries of each fact list are dropped a
    step at a time until it fits, so a wide fact sheet degrades to a narrower
    one rather than to no read at all.
    """
    view = _prompt_view(snapshot)
    head = _SYSTEM_PROMPT + "\n\nFact sheet:\n"

    def render(v: dict) -> str:
        return head + json.dumps(_slim(v), separators=(",", ":"), sort_keys=True)

    prompt = render(view)
    if _token_estimate(prompt) <= PROMPT_TOKEN_BUDGET:
        return prompt

    facts = view.get("smc_facts") or {}
    for keep in (4, 3, 2, 1):
        view["smc_facts"] = _trim_facts(facts, keep)
        prompt = render(view)
        if _token_estimate(prompt) <= PROMPT_TOKEN_BUDGET:
            return prompt
    # Still over at one entry per list: the sheet is as small as the methodology
    # allows. Return it and let the provider answer — a truncated fact sheet
    # would be worse than a refused one, because a plan built on a silently
    # partial sheet would still be stamped as if the whole structure was read.
    return prompt


def _as_float(value) -> float | None:
    # Only real JSON numbers count. A quoted "2000" is a schema slip, and the
    # engine would rather drop the plan than guess what the model meant.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


# How far outside a computed zone an entry may sit and still count as anchored
# there, as a fraction of that frame's ATR. A few cents of slippage against the
# zone edge is normal; half an ATR away is a different level entirely.
ENTRY_ANCHOR_ATR = float(os.environ.get("ASTRA_SMC_ANCHOR_ATR", "0.5"))


def _env_flag(name: str, default: str = "1") -> bool:
    """A boolean from an environment flag: '0'/'false'/'no'/'off' turn it off."""
    raw = os.environ.get(name, default)
    return str(raw).strip().lower() not in ("0", "false", "no", "off", "")


def _zone_of(entry: float, facts: dict, tol: float) -> str | None:
    """The computed zone ``entry`` sits in on one frame, or None.

    Checks the order blocks and the fair-value gaps the fact sheet actually
    found — never a zone the model described in prose. The name returned is
    what anchors the entry, so the reason a plan was kept is auditable.
    """
    for ob in facts.get("order_blocks") or []:
        lo, hi = _as_float(ob.get("low")), _as_float(ob.get("high"))
        if lo is not None and hi is not None and lo - tol <= entry <= hi + tol:
            return "order block (%s)" % ob.get("kind")
    for f in facts.get("fair_value_gaps") or []:
        lo, hi = _as_float(f.get("low")), _as_float(f.get("high"))
        if lo is not None and hi is not None and lo - tol <= entry <= hi + tol:
            return "fair-value gap (%s)" % f.get("kind")
    return None


def entry_anchor(entry: float, facts_by_tf: dict) -> tuple[str | None, str | None]:
    """Which timeframe's computed zone the entry sits at, and what zone it is.

    This is the check the design promised but never enforced: the prompt tells
    the model to anchor every price to the fact sheet, and this verifies that
    the *entry* really does land on a zone the code computed — an order block
    or a fair-value gap — rather than a level the model made up. Returns
    ``(timeframe, zone)``, or ``(None, None)`` when the entry anchors nowhere.

    The tolerance is each frame's own ATR (``ENTRY_ANCHOR_ATR`` of it), so a
    quiet frame demands a tighter match than a volatile one — the same way a
    trader reads a level differently on M5 than on H4.
    """
    for tf, facts in (facts_by_tf or {}).items():
        if not isinstance(facts, dict):
            continue
        atr_value = _as_float(facts.get("atr")) or 0.0
        tol = ENTRY_ANCHOR_ATR * atr_value
        zone = _zone_of(entry, facts, tol)
        if zone is not None:
            return tf, zone
    return None, None


def validate_plan(raw: dict, min_rr: float = MIN_RR,
                  facts_by_tf: dict | None = None,
                  require_anchor: bool | None = None) -> tuple[dict | None, str]:
    """Check one model plan for coherence. Returns (plan, reason).

    A coherent plan has a direction, numeric levels, the stop on the losing side
    of the entry, targets in travel order, and reward-to-risk at least
    ``min_rr`` (the methodology's own floor). Anything else is dropped with a
    reason; the engine never repairs a number it cannot trust.

    When ``facts_by_tf`` is supplied and anchoring is on
    (``ASTRA_SMC_REQUIRE_ANCHOR``, default on), the entry must also land on a
    zone the fact sheet computed — an order block or a fair-value gap. A plan
    whose entry floats in empty space is dropped with the reason, because that
    is exactly the fabricated level the design exists to prevent. Callers that
    have no sheet (unit tests, callers reading a bare reply) pass nothing and
    the check is skipped rather than failing the plan.
    """
    if not isinstance(raw, dict):
        return None, "not an object"
    direction = str(raw.get("direction", "")).upper()
    if direction not in ("LONG", "SHORT"):
        return None, "direction must be LONG or SHORT"

    entry = _as_float(raw.get("entry"))
    stop = _as_float(raw.get("stop"))
    if entry is None or entry <= 0:
        return None, "entry is not a positive number"
    if stop is None or stop <= 0:
        return None, "stop is not a positive number"

    raw_targets = raw.get("targets")
    if isinstance(raw_targets, (int, float)) and not isinstance(raw_targets, bool):
        raw_targets = [raw_targets]
    if not isinstance(raw_targets, list):
        return None, "targets must be a list"
    targets: list[float] = []
    for t in raw_targets[:3]:
        value = _as_float(t)
        if value is None or value <= 0:
            return None, "a target is not a positive number"
        targets.append(round(value, 2))
    if not targets:
        return None, "no targets"

    if direction == "LONG":
        if stop >= entry:
            return None, "LONG stop is not below the entry"
        if any(targets[i] <= targets[i - 1] for i in range(1, len(targets))):
            return None, "LONG targets are not ascending"
        if targets[0] <= entry:
            return None, "LONG first target is not above the entry"
    else:
        if stop <= entry:
            return None, "SHORT stop is not above the entry"
        if any(targets[i] >= targets[i - 1] for i in range(1, len(targets))):
            return None, "SHORT targets are not descending"
        if targets[0] >= entry:
            return None, "SHORT first target is not below the entry"

    risk = abs(entry - stop)
    reward = abs(targets[-1] - entry)
    if risk <= 0:
        return None, "risk is zero"
    rr = reward / risk
    if rr < min_rr:
        return None, ("reward-to-risk %.2f is below the %.1f minimum"
                      % (rr, min_rr))

    # The anchoring check: the entry must sit on a zone the fact sheet computed,
    # not on a level the model invented. Skipped only when the caller supplied
    # no sheet or turned it off — never silently when a sheet was passed.
    anchor_tf: str | None = None
    anchor_zone: str | None = None
    if facts_by_tf is not None:
        if require_anchor is None:
            require_anchor = _env_flag("ASTRA_SMC_REQUIRE_ANCHOR", "1")
        if require_anchor:
            anchor_tf, anchor_zone = entry_anchor(entry, facts_by_tf)
            if anchor_zone is None:
                return None, ("entry %.2f is not anchored to a computed order "
                              "block or fair-value gap" % entry)

    confidence = _as_float(raw.get("confidence"))
    if confidence is not None:
        confidence = max(0.0, min(1.0, confidence))

    plan = {
        "direction": direction,
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "targets": targets,
        "target": targets[-1],
        "risk": round(risk, 2),
        "reward": round(reward, 2),
        "reward_risk": round(rr, 2),
        "confidence": confidence,
        "rationale": str(raw.get("rationale", "")).strip(),
        "sweep": str(raw.get("sweep", "")).strip(),
        "poi": str(raw.get("poi", "")).strip(),
        # Which computed zone the entry was verified against. None when the
        # anchoring check was off or no sheet was passed to validate_plan.
        "anchored": anchor_zone is not None,
        "anchor_timeframe": anchor_tf,
        "anchor_zone": anchor_zone,
        # The model's own claim, never a measured edge.
        "confidence_validated": False,
    }
    return plan, "ok"


def parse_engine(text: str, facts_by_tf: dict | None = None) -> dict | None:
    """Pull the JSON object out of a model reply, tolerating prose or fences.

    ``facts_by_tf`` is the computed fact sheet's ``timeframes`` map, passed so
    each plan's entry can be checked against a zone the code actually found.
    """
    if not text:
        return None
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        newline = candidate.find("\n")
        if newline != -1:
            candidate = candidate[newline + 1:]
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(candidate[start:end + 1])
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None

    bias = str(parsed.get("bias", "")).upper()
    if bias not in ("LONG", "SHORT", "NEUTRAL"):
        bias = "NEUTRAL"

    plans: list[dict] = []
    rejected: list[dict] = []
    raw_plans = parsed.get("plans")
    if isinstance(raw_plans, list):
        for raw in raw_plans[:MAX_PLANS * 2]:
            plan, reason = validate_plan(raw, facts_by_tf=facts_by_tf)
            if plan is None:
                rejected.append({"plan": raw if isinstance(raw, dict) else None,
                                 "reason": reason})
            elif len(plans) < MAX_PLANS:
                plans.append(plan)
            else:
                rejected.append({"plan": None, "reason": "over the plan limit"})

    return {
        "market_read": str(parsed.get("market_read", "")).strip(),
        "bias": bias,
        "plans": plans,
        "rejected": rejected,
        "news_caution": bool(parsed.get("news_caution", False)),
        "key_event": str(parsed.get("key_event", "")).strip(),
    }


def stale_tape_note(tape: dict | None) -> str:
    """The Arabic-facing reason a stale tape yields no plan.

    "Unreachable" and "stale" are different faults with the same consequence:
    no usable tape, so no plan. The note names which, because one is a bridge
    the user can restart and the other is a feed that is genuinely behind.
    """
    tape = tape or {}
    frames = tape.get("frames_degraded") or []
    listed = ", ".join(frames) if frames else "كل الأطر"
    per_frame = tape.get("per_frame") or {}
    statuses = {str((per_frame.get(f) or {}).get("status"))
                for f in frames} or {"unreachable"}
    if statuses == {"unreachable"}:
        return ("تعذّر إصدار خطة: جسر بيانات السوق غير متاح (%s). "
                "لا شمعات مغلقة موثوقة، لذلك لا تُعرض خطة." % listed)
    return ("تعذّر إصدار خطة: الأطر المغلقة غير محدّثة (%s). "
            "القراءة على شريط قديم قد تصف سوقاً تحرّك بالفعل، لذلك لا تُعرض خطة."
            % listed)


def assemble(snapshot: dict, parsed: dict) -> dict:
    """Attach the honest stamps and the fact sheet to a parsed reply."""
    price = snapshot.get("price")
    for plan in parsed["plans"]:
        if price is not None:
            plan["distance_from_price"] = round(plan["entry"] - price, 2)
    parsed["engine"] = {
        "timeframes_prompted": list(TIMEFRAMES),
        "timeframes_read": sorted((snapshot.get("facts") or {})
                                  .get("timeframes", {})),
        "timeframes_missing": (snapshot.get("facts") or {})
        .get("missing_timeframes", []),
        "facts_note": (snapshot.get("facts") or {}).get("facts_note"),
        "confluence": (snapshot.get("facts") or {}).get("confluence"),
        # The freshness of the bars this read rests on, so a plan is never read
        # without knowing how current the market it describes actually was.
        "tape": snapshot.get("tape"),
        # The project's measured record for this school, kept clearly apart from
        # the model's own confidence so a claim is never read as a measurement.
        "measured_record": snapshot.get("measured_record"),
        "min_reward_risk": MIN_RR,
        "entry_anchor_required": _env_flag("ASTRA_SMC_REQUIRE_ANCHOR", "1"),
        "entry_anchor_tolerance_atr": ENTRY_ANCHOR_ATR,
        "note": ("SMC/ICT analysis engine, isolated from the validated "
                 "pipeline. Structure is computed from closed bars; the plan "
                 "and its confidence are the model's own estimates, not "
                 "measured edges. Shadow analysis only: no orders are placed."),
    }
    return parsed


def append_log(record: dict) -> None:
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass  # Logging is best-effort; it must never break the response.


class Engine:
    """Builds and caches SMC reads. Pure logic lives in the module functions."""

    def __init__(self) -> None:
        self._cached: dict | None = None
        self._cached_at = 0.0

    def current(self, force: bool = False) -> dict:
        now = time.time()
        if not force and self._cached and now - self._cached_at < CACHE_TTL:
            return self._cached
        result = self._build()
        self._cached, self._cached_at = result, now
        return result

    def _build(self) -> dict:
        snapshot = build_snapshot()
        api_key = ai_advisor.available_key()
        result = {
            "schema_version": SCHEMA_VERSION,
            "symbol": snapshot["symbol"],
            "generated_at_utc": int(time.time()),
            "validated": False,
            "source": "smc-engine (isolated)",
            "framework": "SMC/ICT",
            "provider": ai_advisor.PROVIDER,
            "configured": bool(api_key),
            "model": ai_advisor.active_model() if api_key else None,
            "snapshot": snapshot,
            "analysis": None,
            # The deterministic near-target plan — the one number on this sheet
            # that is a *measurement* rather than a reading. Computed here, with
            # no model, so it reaches the reply even when the provider refuses,
            # rate-limits or is unconfigured: the reading can fail while the
            # measurement stands. A stale tape still suppresses it, because a
            # plan anchored to old bars describes a market that has moved.
            "measured_plan": None,
        }
        tape = snapshot.get("tape") or {}
        if tape.get("state") != "stale":
            result["measured_plan"] = _measured_plan(snapshot)

        if not api_key:
            result["note"] = ("SMC engine not configured: set %s to enable the "
                              "SMC analyst."
                              % ai_advisor._KEY_ENV.get(ai_advisor.PROVIDER,
                                                        "the provider key"))
            return result

        # A stale tape is the one state where the engine refuses to plan. Every
        # read is anchored to closed bars, so if those bars are old the plan
        # would describe a market that has already moved — the exact error the
        # anchoring rule exists to prevent, one level up. No model call is made:
        # a plan is not produced and then hidden, it is never asked for.
        if tape.get("state") == "stale":
            result["stale_tape"] = True
            result["note"] = stale_tape_note(tape)
            return result

        if ai_advisor.rate_limited():
            result["rate_limited"] = True
            result["note"] = ("SMC engine paused: the provider's daily quota is "
                              "spent. The fact sheet below is still live.")
            return result

        reply = ai_advisor.call_model(build_prompt(snapshot), api_key)
        facts_by_tf = (snapshot.get("facts") or {}).get("timeframes") or {}
        parsed = parse_engine(reply, facts_by_tf) if reply else None
        if parsed is None:
            auth = ai_advisor.auth_failure()
            if auth:
                # The key is present but the provider refused it (expired or
                # revoked). Naming it beats the generic "no usable reply".
                result["auth_failed"] = True
                result["note"] = ("SMC engine rejected by the provider: %s "
                                  "(HTTP %s). Check %s on the host: the key may "
                                  "be expired or revoked."
                                  % (auth["message"], auth["code"],
                                     ai_advisor._KEY_ENV.get(
                                         ai_advisor.PROVIDER, "the provider key")))
            elif ai_advisor.rate_limited():
                result["rate_limited"] = True
                result["note"] = ("SMC engine paused: the provider's daily "
                                  "quota is spent. The fact sheet below is "
                                  "still live.")
            else:
                why = ai_advisor.failure_reason()
                failure = ai_advisor.last_http_failure() or {}
                if failure.get("code") == 413:
                    # The request was refused for its size, not its content.
                    # Naming the ceiling and the budget turns a dead end into a
                    # dial: the operator can raise ASTRA_SMC_TOKEN_BUDGET, or
                    # lower ASTRA_SMC_BARS / ASTRA_SMC_NEWS, and retry.
                    result["prompt_too_large"] = True
                    result["note"] = (
                        "SMC engine unavailable: the request is larger than the "
                        "provider's per-minute token ceiling (HTTP 413: %s). "
                        "The fact sheet was already trimmed to fit the "
                        "configured budget; lower ASTRA_SMC_BARS or "
                        "ASTRA_SMC_NEWS, or raise ASTRA_SMC_TOKEN_BUDGET on a "
                        "plan with a higher limit." % (why or "request too "
                                                       "large"))
                else:
                    result["note"] = (
                        "SMC engine unavailable: no usable reply (%s)." % why
                        if why else "SMC engine unavailable: no usable reply.")
            return result

        result["analysis"] = assemble(snapshot, parsed)
        result["model"] = ai_advisor.answered_model() or result["model"]
        result["provider"] = ai_advisor.answered_provider() or result["provider"]
        result["note"] = ("SMC/ICT analysis engine output. Structure is "
                          "computed from closed bars; the plan and confidence "
                          "are the model's estimates, produced outside the "
                          "validated pipeline. Shadow analysis only, not "
                          "financial advice.")
        append_log({
            "generated_at_utc": result["generated_at_utc"],
            "symbol": snapshot["symbol"],
            "price": snapshot.get("price"),
            "bias": parsed["bias"],
            "plan_count": len(parsed["plans"]),
            "plans": parsed["plans"],
            "provider": ai_advisor.answered_provider() or ai_advisor.PROVIDER,
            "model": ai_advisor.answered_model() or ai_advisor.active_model(),
        })
        return result


def _print_once(result: dict) -> None:
    """Human-readable dump for ``--once``. Arabic text, plain numbers."""
    model = result.get("model") or "?"
    print("SMC engine · %s · framework=%s · provider=%s model=%s configured=%s"
          % (result["symbol"], result["framework"], result["provider"], model,
             result["configured"]))
    mp = result.get("measured_plan")
    if mp:
        print("\nخطة مقيسة (بلا موديل): %s" % mp["direction"])
        print("   entry %s · stop %s · target %s · R:R %s"
              % (mp["entry"], mp["stop"], mp["target"], mp["reward_risk"]))
        print("   %s" % mp["rationale"])
    if not result["configured"]:
        print(result["note"])
        return
    analysis = result.get("analysis")
    if analysis is None:
        print(result.get("note"))
        return
    print("bias: %s" % analysis["bias"])
    print("read: %s" % analysis["market_read"])
    if analysis["news_caution"]:
        print("news caution: %s" % (analysis["key_event"] or "high-impact event"))
    if not analysis["plans"]:
        print("no plans")
    for i, p in enumerate(analysis["plans"], 1):
        conf = p["confidence"]
        conf_txt = "n/a" if conf is None else "%.0f%%" % (conf * 100)
        print("\n#%d %s  confidence(est): %s" % (i, p["direction"], conf_txt))
        print("   entry %s · stop %s · targets %s · R:R %s" % (
            p["entry"], p["stop"],
            " ← ".join(str(x) for x in p["targets"]), p["reward_risk"]))
        if p["sweep"]:
            print("   sweep: %s" % p["sweep"])
        if p["poi"]:
            print("   POI: %s" % p["poi"])
        if p["rationale"]:
            print("   %s" % p["rationale"])
    for bad in analysis["rejected"]:
        print("dropped a plan: %s" % bad["reason"])
    print("\n%s" % result["note"])


class Handler(BaseHTTPRequestHandler):
    engine = Engine()

    def log_message(self, fmt, *args):
        sys.stderr.write("[smc-engine] %s\n" % (fmt % args))

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path in ("/v1/smc", "/smc", "/v1/analysis", "/analysis"):
            force = "refresh=1" in query
            self._json(200, {"smc": self.engine.current(force=force)})
        elif path in ("/health", "/v1/health"):
            auth = ai_advisor.auth_failure()
            self._json(200, {"service": "smc-engine", "status": "OK",
                             "framework": "SMC/ICT",
                             "provider": ai_advisor.PROVIDER,
                             "configured": bool(ai_advisor.provider_key()),
                             "rate_limited": ai_advisor.rate_limited(),
                             "auth_ok": auth is None,
                             "auth_error": auth["message"] if auth else None})
        else:
            self._json(404, {"error": "unknown route", "path": path})

    def do_HEAD(self):
        self.do_GET()

    def _json(self, status: int, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ASTRA isolated SMC engine")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8795)
    parser.add_argument("--once", action="store_true",
                        help="build one read, print it, and exit")
    args = parser.parse_args(argv)

    if args.once:
        _print_once(Engine().current(force=True))
        return 0

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    sys.stderr.write(
        "[smc-engine] listening on http://%s:%d (provider=%s configured=%s)\n"
        % (args.host, args.port, ai_advisor.PROVIDER,
           bool(ai_advisor.provider_key())))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
