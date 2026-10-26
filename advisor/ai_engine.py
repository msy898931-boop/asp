#!/usr/bin/env python3
"""ASTRA AI engine: an isolated, AI-driven analysis and trade engine.

What it is, and what it is not:

  * It reads every timeframe the bridge serves (M1 through MN1) plus the
    day's news and high-impact calendar, hands the whole picture to an
    external model, and asks for concrete trades with numeric entry, stop,
    targets and the model's own win-rate estimate.
  * It is fully isolated. Its own process, its own port, its own log. It reads
    the bridge and the API over loopback and writes nothing back: no ledger
    write, no engine call, no order of any kind. If it dies, nothing else does.
  * Every number it returns is the model's claim, not a measurement. The
    response is stamped ``validated: false`` and the win-rate is labelled as an
    estimate. Nothing here changes the deterministic pipeline's behaviour.
  * The model's levels are checked for internal coherence (stop on the correct
    side, targets in travel order, positive R:R). An incoherent trade is
    dropped and the reason recorded, never silently repaired.
  * Without a provider key it still answers with the factual snapshot and
    ``configured: false`` so a caller degrades instead of erroring.

Run once and print the trades:

    GROQ_API_KEY=gsk_... python3 advisor/ai_engine.py --once

Serve it:

    GROQ_API_KEY=gsk_... python3 advisor/ai_engine.py --port 8794
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import ai_advisor
import playbook

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SCHEMA_VERSION = "1.0"
SYMBOL = ai_advisor.SYMBOL
BRIDGE_BASE = ai_advisor.BRIDGE_BASE
API_BASE = ai_advisor.API_BASE

# Every frame the bridge can serve, coarsest last. The engine reasons across
# the lot; the lower frames time the entry, the higher frames set the bias.
ALL_TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1")


def _resolve_timeframes() -> tuple[str, ...]:
    """The frames the engine hands the model, from ``ASTRA_ENGINE_TIMEFRAMES``.

    Default: every frame. A comma-separated allow-list narrows it, keeping the
    canonical order, so an operator who finds the daily/weekly context noisy can
    run the engine on the lower frames alone without editing code. Unknown
    labels are dropped rather than trusted; an empty result falls back to the
    full list, because a typo must not silently blind the engine.
    """
    raw = os.environ.get("ASTRA_ENGINE_TIMEFRAMES", "").strip()
    if not raw:
        return ALL_TIMEFRAMES
    wanted = {tok.strip().upper() for tok in raw.split(",") if tok.strip()}
    chosen = tuple(tf for tf in ALL_TIMEFRAMES if tf in wanted)
    return chosen or ALL_TIMEFRAMES


TIMEFRAMES = _resolve_timeframes()
BARS_PER_TF = int(os.environ.get("ASTRA_ENGINE_BARS", "220"))
# A frame with fewer closed bars than this cannot carry a read.
MIN_BARS = 30

MAX_TRADES = int(os.environ.get("ASTRA_ENGINE_MAX_TRADES", "3"))
CACHE_TTL = float(os.environ.get("ASTRA_ENGINE_CACHE_TTL", "60"))
HTTP_TIMEOUT = ai_advisor.HTTP_TIMEOUT
LOG_PATH = os.environ.get(
    "ASTRA_ENGINE_LOG", os.path.join(ROOT, "run", "ai_engine_trades.jsonl"))
# A level more than this many ATRs from the rule-based entry is flagged as
# unanchored. It is kept, not dropped: the engine is independent by design, but
# the reader is told when a number sits outside the day's range.
FAR_ATR = float(os.environ.get("ASTRA_ENGINE_FAR_ATR", "6"))

_SYSTEM_PROMPT = (
    "You are a gold (XAUUSD) trading analyst. You are given a factual snapshot "
    "produced by a rule engine: a read for every available timeframe with the "
    "last close, EMAs, RSI, ATR and structure bias, a rule-based trade card as "
    "an anchor, and the day's headlines and high-impact events.\n"
    "Language: every human-readable field in Arabic. JSON keys and the "
    "direction values stay in English.\n"
    "Rules:\n"
    "- Anchor every price to the snapshot. Use the last close and ATR you are "
    "given; never invent a price, an event or a date.\n"
    "- A trade is coherent only if the stop sits on the losing side of the "
    "entry and the targets run in the winning direction, nearest first. For a "
    "LONG: stop < entry < target1 < target2 ... For a SHORT: stop > entry > "
    "target1 > target2 ...\n"
    "- Give each trade between one and five targets, in R-multiple order. The "
    "last target is the full target; the ones before it are partial exits.\n"
    "- win_rate is YOUR OWN probability (0..1) that this trade reaches its "
    "final target before its stop. It is an estimate, not a measured number: "
    "state the reasoning behind it in win_rate_note. Do not copy the engine's "
    "score.\n"
    "- Read the timeframes by role: the lower frames (M1..M15) time the entry, "
    "the middle frames (M30, H1) carry the intraday trend, and the higher "
    "frames (H4, D1, W1, MN1) set the bias and the room to move. The higher "
    "frames INFORM the direction; they do not veto it. A setup that runs with "
    "the higher-frame bias is stronger, and one that runs against it is lower "
    "conviction — say which it is in the rationale, but still give the trade "
    "when the lower and middle frames agree on a coherent plan.\n"
    "- A timeframe listed under missing_timeframes, or absent entirely, is not "
    "a reason to refuse: reason from the frames you were given and note the gap "
    "in market_read at most in passing. Never withhold every trade only because "
    "a higher frame is missing.\n"
    "- Prefer a trade when a coherent plan exists. Return an empty trades list "
    "only when the frames genuinely conflict on direction and no plan can be "
    "anchored, or when no coherent trade exists; say why in market_read. Do not "
    "force a trade that the numbers do not support, but do not default to "
    "NEUTRAL out of caution either.\n"
    "- The snapshot carries a playbook: named schools (trend following, "
    "counter-trend fade, post-release momentum, release+trend confluence, "
    "regime-filtered trend) each with the record the project measured on real "
    "data. Say which school a trade belongs to in its rationale, using the "
    "school's plain name. The playbook's measured numbers are the project's "
    "findings, not yours to restate as your own win_rate: if a school's record "
    "does not clear the gate, you may still give the trade, but say the school "
    "is unproven rather than implying an edge.\n"
    "- This is analysis for a research tool, not advice. Do not promise "
    "returns.\n"
    "Reply with JSON only, keys: market_read (two or three Arabic sentences "
    "covering the timeframe picture), bias (LONG, SHORT or NEUTRAL), trades "
    "(a list, possibly empty), news_used (true or false), key_event (the "
    "Arabic name of the event you built the read around, or empty string). "
    "Each trade object has: direction (LONG or SHORT), entry (number), stop "
    "(number), targets (list of numbers, nearest first), win_rate (0..1), "
    "rationale (one Arabic sentence), timeframe (the frame that times it)."
)


def fetch_all_bars() -> dict:
    """Closed bars for every timeframe the bridge answers for.

    Reuses the advisor's tolerant envelope handling, but walks the full frame
    list and drops any frame too short to carry a read.
    """
    out: dict = {}
    for tf in TIMEFRAMES:
        url = "%s/v1/candles?tf=%s&limit=%d" % (BRIDGE_BASE, tf, BARS_PER_TF)
        payload = ai_advisor._get_json(url)
        if not isinstance(payload, dict):
            continue
        if isinstance(payload.get("payload"), dict):
            payload = payload["payload"]
        bars = payload.get("bars") or payload.get("candles") or []
        if isinstance(bars, list) and len(bars) >= MIN_BARS:
            out[tf] = bars
    return out


def build_snapshot() -> dict:
    """Collect the facts the model reasons over. No opinion is formed here."""
    snapshot: dict = {
        "symbol": SYMBOL,
        "captured_at_utc": int(time.time()),
        "price": None,
        "timeframes": {},
        "missing_timeframes": [],
        "rule_card": None,
        "news": [],
        "news_available": False,
        "events": [],
        "events_available": False,
    }

    tick = ai_advisor._get_json("%s/v1/tick?symbol=%s" % (BRIDGE_BASE, SYMBOL))
    if tick and isinstance(tick.get("payload"), dict):
        snapshot["price"] = tick["payload"].get("last")

    bars_by_tf = fetch_all_bars()
    for tf in TIMEFRAMES:
        bars = bars_by_tf.get(tf)
        if not bars:
            snapshot["missing_timeframes"].append(tf)
            continue
        read = ai_advisor.mtf_read.timeframe_read(bars)
        if read is not None:
            snapshot["timeframes"][tf] = read

    # The rule-based card: the engine's own read, kept as an anchor and shown
    # beside the model's numbers so the two can be compared.
    try:
        card = ai_advisor.mtf_read.build(bars_by_tf).get("card")
    except Exception:  # noqa: BLE001 - a bad read must not sink the snapshot
        card = None
    snapshot["rule_card"] = card

    headlines = ai_advisor.recent_news()
    snapshot["news"] = headlines
    snapshot["news_available"] = bool(headlines)
    events = ai_advisor.upcoming_events()
    snapshot["events"] = events
    snapshot["events_available"] = bool(events)
    return snapshot


def _prompt_view(snapshot: dict) -> dict:
    """The snapshot as the model sees it: every frame, the anchor card, news."""
    return {
        "symbol": snapshot["symbol"],
        "price": snapshot["price"],
        "timeframes": snapshot["timeframes"],
        "missing_timeframes": snapshot["missing_timeframes"],
        "rule_card": snapshot["rule_card"],
        "playbook": playbook.catalogue(),
        "news": snapshot["news"],
        "events": snapshot["events"],
    }


def build_prompt(snapshot: dict) -> str:
    return (_SYSTEM_PROMPT + "\n\nSnapshot:\n"
            + json.dumps(_prompt_view(snapshot), indent=2, sort_keys=True))


def _as_float(value) -> float | None:
    # Only real JSON numbers count. A quoted "2000" is a schema slip, and the
    # engine would rather drop the trade than guess what the model meant.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    if out != out or out in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return out


def validate_trade(raw: dict, price: float | None = None) -> tuple[dict | None, str]:
    """Check one model trade for coherence. Returns (trade, reason).

    A coherent trade has a direction, numeric levels, the stop on the losing
    side of the entry, and targets in travel order. Anything else is dropped
    with a reason; the engine never repairs a number it cannot trust.
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
    for t in raw_targets[:5]:
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

    win_rate = _as_float(raw.get("win_rate"))
    if win_rate is None:
        win_rate = None
    else:
        win_rate = max(0.0, min(1.0, win_rate))

    trade = {
        "direction": direction,
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "targets": targets,
        "target": targets[-1],
        "risk": round(risk, 2),
        "reward": round(reward, 2),
        "reward_risk": round(reward / risk, 2),
        "win_rate": win_rate,
        "rationale": str(raw.get("rationale", "")).strip(),
        "timeframe": str(raw.get("timeframe", "")).strip(),
        # The engine's own claim, never a measured edge.
        "win_rate_validated": False,
    }

    if price is not None:
        trade["distance_from_price"] = round(entry - price, 2)
    return trade, "ok"


def parse_engine(text: str) -> dict | None:
    """Pull the JSON object out of a model reply, tolerating prose or fences."""
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

    trades: list[dict] = []
    rejected: list[dict] = []
    raw_trades = parsed.get("trades")
    if isinstance(raw_trades, list):
        for raw in raw_trades[:MAX_TRADES * 2]:
            trade, reason = validate_trade(raw)
            if trade is None:
                rejected.append({"trade": raw if isinstance(raw, dict) else None,
                                 "reason": reason})
            elif len(trades) < MAX_TRADES:
                trades.append(trade)
            else:
                rejected.append({"trade": None, "reason": "over the trade limit"})

    return {
        "market_read": str(parsed.get("market_read", "")).strip(),
        "bias": bias,
        "trades": trades,
        "rejected": rejected,
        "news_used": bool(parsed.get("news_used", False)),
        "key_event": str(parsed.get("key_event", "")).strip(),
    }


def _anchor_check(trade: dict, card: dict | None, atr: float | None) -> dict:
    """How far a model trade sits from the rule-based card, in ATRs."""
    check = {"anchored": True, "note": ""}
    if not card or atr is None or atr <= 0:
        check["note"] = "no rule card to compare against"
        return check
    card_entry = _as_float(card.get("entry"))
    if card_entry is None:
        check["note"] = "rule card has no entry"
        return check
    gap = abs(trade["entry"] - card_entry) / atr
    check["entry_gap_atr"] = round(gap, 2)
    if gap > FAR_ATR:
        check["anchored"] = False
        check["note"] = ("entry sits %.1f ATR from the rule-based entry" % gap)
    return check


def _rule_atr(snapshot: dict) -> float | None:
    for tf in ("M15", "M5", "M30", "H1"):
        read = snapshot["timeframes"].get(tf) or {}
        atr = _as_float(read.get("atr"))
        if atr and atr > 0:
            return atr
    return None


def _regime_proxy(read: dict) -> str:
    """A regime label from an M15 read, using the replay's own slope threshold.

    The read carries ``ema_fast``/``ema_slow`` and a ``structure`` vote, which
    together are the replay's trend test; it does not carry volatility, so this
    proxy never reports VOLATILE. It is named a proxy for exactly that reason:
    a trade labelled TRENDING_UP here means the trend test passed, not that the
    replay's full regime (volatility included) agreed.
    """
    fast = _as_float(read.get("ema_fast"))
    slow = _as_float(read.get("ema_slow"))
    if not fast or not slow:
        return ""
    slope = (fast - slow) / slow
    structure = read.get("structure")
    if abs(slope) < playbook.TREND_SLOPE_THRESHOLD:
        return "RANGING"
    up = slope > 0
    agrees = (up and isinstance(structure, int) and structure > 0) or (
        not up and isinstance(structure, int) and structure < 0)
    if agrees:
        return "TRENDING_UP" if up else "TRENDING_DOWN"
    return "RANGING"


def _school_context(trade: dict, snapshot: dict) -> dict:
    """The playbook context for a model trade, from facts the engine already has.

    The playbook is scored on the *rule* engine's context rows, so a model trade
    is mapped onto the same shape: the M15 read gives the regime proxy, RSI and
    the ATR scale, the trade gives the direction, and the H4 read gives
    alignment. Nothing here is invented; a field the engine cannot supply is
    simply absent and the corresponding school will not fire.
    """
    m15 = snapshot["timeframes"].get("M15") or {}
    h4 = snapshot["timeframes"].get("H4") or {}
    direction = str(trade.get("direction") or "").upper()
    context: dict = {
        "direction": direction,
        "regime": _regime_proxy(m15),
        "rsi": m15.get("rsi"),
        "atr_pct": (m15.get("atr") or 0.0) / trade["entry"] if trade.get("entry") else 0.0,
    }
    h4_dir = h4.get("direction")
    if h4_dir in ("LONG", "SHORT") and direction in ("LONG", "SHORT"):
        context["h4_align"] = "ALIGNED" if h4_dir == direction else "AGAINST"
    # The research's "after a release" window is two hours. The calendar keeps
    # events from one hour in the past onward (``fetch_calendar`` drops anything
    # older), so the live engine can only see a release up to ~1h old — a
    # narrower window than the school was measured on, which the school's
    # ``scope`` says. A high-impact event just fired sets recent_release.
    for event in snapshot.get("events") or []:
        in_hours = event.get("in_hours")
        if isinstance(in_hours, (int, float)) and -2.0 <= in_hours <= 0.0:
            context["recent_release"] = True
            break
    return context


def _playbook_label(trade: dict, snapshot: dict) -> dict:
    """Which named school this trade belongs to, and that school's real record.

    The label is description, not endorsement: it names the strategy and quotes
    the measured record the research produced. A school whose record does not
    clear the gate is reported as such, so the model's ``win_rate`` claim is
    never mistaken for the school's measurement.
    """
    context = _school_context(trade, snapshot)
    school = playbook.primary(context)
    label: dict = {"context": context, "school": None, "measured": None,
                   "note": "no named school matches this trade"}
    if school is not None:
        label["school"] = school.key
        label["school_name"] = school.name
        label["school_name_ar"] = school.name_ar
        label["school_family"] = school.school
        label["school_family_ar"] = school.family_ar
        label["thesis"] = school.thesis
        label["measured"] = school.measured
        if school.measured and school.measured.get("clears_gate"):
            label["note"] = "school's measured record clears the promotion gate"
        elif school.measured:
            label["note"] = ("school's measured record does NOT clear the gate: "
                             + str(school.measured.get("why_not", "")))
            m = school.measured
            label["note_ar"] = (
                "المدرسة «%s»: النتيجة المقيسة على بيانات حقيقية n=%s، نسبة نجاح "
                "%s%%، R/صفقة %s — لم تجتز بوابة الترقية."
                % (school.name_ar or school.name, m.get("n"),
                   m.get("win_pct"), m.get("r_per_trade")))
        else:
            label["note"] = "school not scored yet"
    return label


def assemble(snapshot: dict, parsed: dict) -> dict:
    """Attach the anchor checks and the honest stamps to a parsed reply."""
    card = snapshot.get("rule_card")
    atr = _rule_atr(snapshot)
    price = snapshot.get("price")
    for trade in parsed["trades"]:
        trade["anchor"] = _anchor_check(trade, card, atr)
        trade["playbook"] = _playbook_label(trade, snapshot)
        if price is not None:
            trade["distance_from_price"] = round(trade["entry"] - price, 2)
    parsed["engine"] = {
        "timeframes_prompted": list(TIMEFRAMES),
        "timeframes_read": sorted(snapshot["timeframes"]),
        "timeframes_missing": snapshot["missing_timeframes"],
        "rule_card": card,
        "playbook": playbook.catalogue(),
        "playbook_clears_gate": playbook.clears_gate_count(),
        "note": ("AI analysis engine, isolated from the validated pipeline. "
                 "Each trade is labelled with its playbook school and that "
                 "school's measured record from the real replay. The model's "
                 "win_rate is its own estimate, not the measured rate. Shadow "
                 "analysis only: no orders are placed."),
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
    """Builds and caches engine reads. Pure logic lives in the module functions."""

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
        api_key = ai_advisor.provider_key()
        result = {
            "schema_version": SCHEMA_VERSION,
            "symbol": snapshot["symbol"],
            "generated_at_utc": int(time.time()),
            "validated": False,
            "source": "ai-engine (isolated)",
            "provider": ai_advisor.PROVIDER,
            "configured": bool(api_key),
            "model": ai_advisor.active_model() if api_key else None,
            "snapshot": snapshot,
            "analysis": None,
        }
        if not api_key:
            result["note"] = ("AI engine not configured: set %s to enable the "
                              "analysis engine."
                              % ai_advisor._KEY_ENV.get(ai_advisor.PROVIDER,
                                                        "the provider key"))
            return result

        if ai_advisor.rate_limited():
            result["rate_limited"] = True
            result["note"] = ("AI engine paused: the provider's daily quota is "
                              "spent. The snapshot below is still live.")
            return result

        reply = ai_advisor.call_model(build_prompt(snapshot), api_key)
        parsed = parse_engine(reply) if reply else None
        if parsed is None:
            auth = ai_advisor.auth_failure()
            if auth:
                # The key is present but the provider refused it. Say so with
                # the provider's own words: this is the one failure an operator
                # cannot see from the outside (the routes are all gated).
                result["auth_failed"] = True
                result["note"] = ("AI engine rejected by the provider: %s "
                                  "(HTTP %s). Check %s on the host: the key may "
                                  "be expired or revoked."
                                  % (auth["message"], auth["code"],
                                     ai_advisor._KEY_ENV.get(
                                         ai_advisor.PROVIDER, "the provider key")))
            elif ai_advisor.rate_limited():
                result["rate_limited"] = True
                result["note"] = ("AI engine paused: the provider's daily quota "
                                  "is spent. The snapshot below is still live.")
            else:
                result["note"] = "AI engine unavailable: no usable reply."
            return result

        result["analysis"] = assemble(snapshot, parsed)
        result["model"] = ai_advisor.answered_model() or result["model"]
        result["note"] = ("AI analysis engine output. Each trade is labelled "
                          "with its playbook school and that school's measured "
                          "record; the model's win-rate is its own estimate, "
                          "not a measured edge. Produced outside the validated "
                          "pipeline. Shadow analysis only, not financial advice.")
        append_log({
            "generated_at_utc": result["generated_at_utc"],
            "symbol": snapshot["symbol"],
            "price": snapshot.get("price"),
            "bias": parsed["bias"],
            "trade_count": len(parsed["trades"]),
            "trades": parsed["trades"],
            "schools": [t.get("playbook", {}).get("school")
                        for t in result["analysis"]["trades"]],
            "provider": ai_advisor.PROVIDER,
            "model": ai_advisor.answered_model() or ai_advisor.active_model(),
        })
        return result


def _print_once(result: dict) -> None:
    """Human-readable dump for ``--once``. Arabic text, plain numbers."""
    model = result.get("model") or "?"
    print("AI engine · %s · provider=%s model=%s configured=%s"
          % (result["symbol"], result["provider"], model,
             result["configured"]))
    if not result["configured"]:
        print(result["note"])
        return
    analysis = result.get("analysis")
    if analysis is None:
        print(result.get("note"))
        return
    print("bias: %s" % analysis["bias"])
    print("read: %s" % analysis["market_read"])
    if not analysis["trades"]:
        print("no trades")
    for i, t in enumerate(analysis["trades"], 1):
        rate = t["win_rate"]
        rate_txt = "n/a" if rate is None else "%.0f%%" % (rate * 100)
        print("\n#%d %s (%s)  win-rate(est): %s" % (
            i, t["direction"], t["timeframe"] or "?", rate_txt))
        print("   entry %s · stop %s · targets %s · R:R %s" % (
            t["entry"], t["stop"],
            " ← ".join(str(x) for x in t["targets"]), t["reward_risk"]))
        if t["rationale"]:
            print("   %s" % t["rationale"])
        pb = t.get("playbook") or {}
        if pb.get("school"):
            m = pb.get("measured") or {}
            print("   playbook: %s (%s) · measured n=%s win=%s%% R/trade=%s · %s"
                  % (pb["school_name"], pb["school_family"],
                     m.get("n", "?"), m.get("win_pct", "?"),
                     m.get("r_per_trade", "?"), pb["note"]))
        elif pb:
            print("   playbook: %s" % pb.get("note", "no school"))
        if not t["anchor"]["anchored"]:
            print("   ⚠ %s" % t["anchor"]["note"])
    for bad in analysis["rejected"]:
        print("dropped a trade: %s" % bad["reason"])
    print("\n%s" % result["note"])


class Handler(BaseHTTPRequestHandler):
    engine = Engine()

    def log_message(self, fmt, *args):
        sys.stderr.write("[ai-engine] %s\n" % (fmt % args))

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path in ("/v1/engine", "/engine", "/v1/analysis", "/analysis"):
            force = "refresh=1" in query
            self._json(200, {"engine": self.engine.current(force=force)})
        elif path in ("/health", "/v1/health"):
            auth = ai_advisor.auth_failure()
            self._json(200, {"service": "ai-engine", "status": "OK",
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
    parser = argparse.ArgumentParser(description="ASTRA isolated AI engine")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8794)
    parser.add_argument("--once", action="store_true",
                        help="build one read, print it, and exit")
    args = parser.parse_args(argv)

    if args.once:
        _print_once(Engine().current(force=True))
        return 0

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    sys.stderr.write(
        "[ai-engine] listening on http://%s:%d (provider=%s configured=%s)\n"
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
