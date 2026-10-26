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

import ai_advisor
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

MAX_PLANS = int(os.environ.get("ASTRA_SMC_MAX_PLANS", "2"))
CACHE_TTL = float(os.environ.get("ASTRA_SMC_CACHE_TTL", "60"))
# The methodology demands at least 1:2 reward-to-risk; a plan below this is
# dropped, not shown with a caveat.
MIN_RR = float(os.environ.get("ASTRA_SMC_MIN_RR", "2.0"))
LOG_PATH = os.environ.get(
    "ASTRA_SMC_LOG", os.path.join(ROOT, "run", "smc_engine_plans.jsonl"))

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
    "for each timeframe, the swing structure with its last BOS/CHoCH, the "
    "premium/discount split of the active leg, equal-highs/equal-lows liquidity "
    "clusters, recent liquidity sweeps, fair-value gaps (filled or unfilled), "
    "order blocks, and the session ranges. These are facts; you interpret "
    "them.\n"
    "Language: every human-readable field in Arabic. JSON keys and the "
    "direction values stay in English.\n"
    "Method — follow this exact sequence and say which step produced the plan:\n"
    "1. Bias: read the higher timeframes (H4, H1) and state the true "
    "direction. A CHoCH is the first body close beyond a key structural level "
    "against the prior trend; a BOS is continuation with it.\n"
    "2. Liquidity: a trade requires that liquidity has been swept. Name the "
    "pool (equal highs/lows, a session high/low) that was swept and when.\n"
    "3. Confirmation: the lower timeframes (M15, M5) must show a CHoCH in the "
    "intended direction after the sweep.\n"
    "4. Point of interest: the entry sits at an order block or the 50% "
    "equilibrium of a fair-value gap that produced the LTF CHoCH. Buy in "
    "discount, sell in premium — never the reverse.\n"
    "5. Risk: the stop sits safely behind the structural high/low that caused "
    "the sweep. Targets aim at the next internal or external liquidity pool. "
    "Reward-to-risk must be at least 1:2.\n"
    "Rules:\n"
    "- Anchor every price to the fact sheet. Use only the levels you were "
    "given; never invent a price, a swing, a gap or an event. The entry MUST "
    "sit inside an order block or a fair-value gap listed in the fact sheet: a "
    "plan whose entry falls in empty space is discarded automatically.\n"
    "- A plan is coherent only if the stop sits on the losing side of the entry "
    "and the targets run in the winning direction, nearest first. For a LONG: "
    "stop < entry < target1 < target2 ... For a SHORT: stop > entry > "
    "target1 > target2 ...\n"
    "- Give each plan one to three targets, in R-multiple order. The last is "
    "the full target.\n"
    "- confidence is YOUR OWN probability (0..1) that the plan reaches its "
    "final target before its stop. It is an estimate, not a measured number; "
    "say why in confidence_note. Do not copy a fact-sheet value.\n"
    "- Explicitly warn when a high-impact event (CPI, FOMC, NFP) is imminent "
    "or was flagged, because SMC structure can be invalidated by news.\n"
    "- A missing timeframe is not grounds to refuse; reason from the frames you "
    "have and note the gap in market_read at most in passing.\n"
    "- Return an empty plans list when the structure genuinely offers no "
    "coherent setup; say why in market_read. Do not force a setup.\n"
    "- This is analysis for a research tool, not advice. Do not promise "
    "returns.\n"
    "Reply with JSON only, keys: market_read (three or four Arabic sentences "
    "covering bias, the sweep, the LTF confirmation and the POI), bias (LONG, "
    "SHORT or NEUTRAL), news_caution (true or false), key_event (the Arabic "
    "name of the event you are cautious about, or empty string), plans (a list, "
    "possibly empty). Each plan object has: direction (LONG or SHORT), "
    "entry (number), stop (number), targets (list of numbers, nearest first), "
    "confidence (0..1), rationale (one or two Arabic sentences naming the "
    "sweep and the POI), sweep (the Arabic phrase for the liquidity that was "
    "swept), poi (the Arabic phrase for the order block or gap used)."
)


def fetch_all_bars() -> dict:
    """Closed bars for every timeframe the bridge answers for.

    Reuses the advisor's tolerant envelope handling and drops any frame too
    short to carry a read.
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
        "facts": None,
        "news": [],
        "news_available": False,
        "events": [],
        "events_available": False,
    }

    tick = ai_advisor._get_json("%s/v1/tick?symbol=%s" % (BRIDGE_BASE, SYMBOL))
    if tick and isinstance(tick.get("payload"), dict):
        snapshot["price"] = tick["payload"].get("last")

    bars_by_tf = fetch_all_bars()
    snapshot["facts"] = smc_read.build(bars_by_tf, TIMEFRAMES)

    headlines = ai_advisor.recent_news()
    snapshot["news"] = headlines
    snapshot["news_available"] = bool(headlines)
    events = ai_advisor.upcoming_events()
    snapshot["events"] = events
    snapshot["events_available"] = bool(events)
    return snapshot


def _prompt_view(snapshot: dict) -> dict:
    """The snapshot as the model sees it: the fact sheet, price, news."""
    return {
        "symbol": snapshot["symbol"],
        "price": snapshot["price"],
        "smc_facts": snapshot["facts"],
        "news": snapshot["news"],
        "events": snapshot["events"],
    }


def build_prompt(snapshot: dict) -> str:
    return (_SYSTEM_PROMPT + "\n\nFact sheet:\n"
            + json.dumps(_prompt_view(snapshot), indent=2, sort_keys=True))


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
        api_key = ai_advisor.provider_key()
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
        }
        if not api_key:
            result["note"] = ("SMC engine not configured: set %s to enable the "
                              "SMC analyst."
                              % ai_advisor._KEY_ENV.get(ai_advisor.PROVIDER,
                                                        "the provider key"))
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
                result["note"] = "SMC engine unavailable: no usable reply."
            return result

        result["analysis"] = assemble(snapshot, parsed)
        result["model"] = ai_advisor.answered_model() or result["model"]
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
            "provider": ai_advisor.PROVIDER,
            "model": ai_advisor.answered_model() or ai_advisor.active_model(),
        })
        return result


def _print_once(result: dict) -> None:
    """Human-readable dump for ``--once``. Arabic text, plain numbers."""
    model = result.get("model") or "?"
    print("SMC engine · %s · framework=%s · provider=%s model=%s configured=%s"
          % (result["symbol"], result["framework"], result["provider"], model,
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
