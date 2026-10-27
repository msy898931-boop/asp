#!/usr/bin/env python3
"""ASTRA AI advisor: an isolated, unvalidated second opinion.

Scope and boundaries, all deliberate:

  * Its own process on its own port. If it dies, nothing else does.
  * Reads the backend API and the bridge over loopback only. It never calls
    into the engine and never writes to the prediction ledger.
  * Its output is a stated view, not a calibrated probability. The pipeline's
    own out-of-sample edge is ~0 (AUC 0.50); nothing here claims better, and
    every advisory is stamped ``validated: false``.
  * Every advisory is appended to a JSONL log so it can be scored against
    outcomes later. Until that scoring exists, treat it as commentary.

Without a provider key the service still answers: it returns the factual
market snapshot with ``configured: false`` and no opinion, so the UI degrades
to "not configured" rather than erroring.

Providers (``ASTRA_ADVISOR_PROVIDER``, default ``groq``):
  * ``groq``    - GROQ_API_KEY, OpenAI-compatible, global free tier.
  * ``gemini``  - GEMINI_API_KEY, region-restricted free tier.
  * ``mistral`` - MISTRAL_API_KEY, OpenAI-compatible, EU-hosted free tier.
    The sign-up is not region-locked the way Groq's and Gemini's are, so it is
    the fallback when those two refuse to issue a key.

Run: python3 advisor/ai_advisor.py --port 8792
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import mtf_read
import self_review

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

API_BASE = os.environ.get("ASTRA_ADVISOR_API", "http://127.0.0.1:8790")
BRIDGE_BASE = os.environ.get("ASTRA_ADVISOR_BRIDGE", "http://127.0.0.1:8791")
SYMBOL = os.environ.get("ASTRA_ADVISOR_SYMBOL", "XAUUSD")
# Multi-timeframe read: the same five frames the trade card votes over, with
# enough bars for the slow EMA (21) plus RSI (14) plus swing history.
MTF_TIMEFRAMES = mtf_read.TIMEFRAMES
MTF_BARS = int(os.environ.get("ASTRA_ADVISOR_MTF_BARS", "220"))
# Provider selection. Groq is the default because its free tier is global;
# Gemini's is region-restricted. Both are plain REST calls.
PROVIDER = os.environ.get("ASTRA_ADVISOR_PROVIDER", "groq").strip().lower()

GROQ_URL = os.environ.get("ASTRA_ADVISOR_GROQ_URL",
                          "https://api.groq.com/openai/v1/chat/completions")
GROQ_MODEL = os.environ.get("ASTRA_ADVISOR_MODEL", "openai/gpt-oss-120b")
GEMINI_MODEL = os.environ.get("ASTRA_ADVISOR_MODEL", "gemini-2.5-flash")
GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
              "{model}:generateContent")
# Mistral is OpenAI-compatible too, and its free Experiment tier is not
# region-locked the way Groq's and Gemini's sign-ups are.
MISTRAL_URL = os.environ.get("ASTRA_ADVISOR_MISTRAL_URL",
                             "https://api.mistral.ai/v1/chat/completions")
MISTRAL_MODEL = os.environ.get("ASTRA_ADVISOR_MODEL", "mistral-small-latest")

# Which env var holds the key for each provider.
_KEY_ENV = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY",
            "mistral": "MISTRAL_API_KEY"}

# Per-provider fallback chains. A provider can refuse a *model* while the key
# is perfectly good: Mistral's free Experiment tier answers HTTP 429 with
# ``x-ratelimit-limit-req-minute: 0`` for a model it does not serve on the free
# plan (``mistral-small-latest`` among them). That is a plan limit, not a spent
# quota, so the chain moves to the next model instead of pausing the provider.
# ``ASTRA_ADVISOR_MODELS`` (comma-separated) overrides the chain outright.
_FALLBACK_MODELS = {
    "groq": (),
    "gemini": (),
    "mistral": ("open-mistral-nemo", "ministral-8b-latest"),
}

# Groq sits behind Cloudflare, which rejects the default urllib user agent with
# a 403 (error 1010). Identify ourselves so the request is served.
_USER_AGENT = os.environ.get("ASTRA_ADVISOR_USER_AGENT", "ASTRA-advisor/1.0")

HTTP_TIMEOUT = float(os.environ.get("ASTRA_ADVISOR_TIMEOUT", "20"))
# How long a full advisory (snapshot + model opinion) is cached. The Groq free
# tier allows ~200k tokens/day and one advisory costs ~1.4k, so anything faster
# than ~10 minutes cannot fit a day of continuous use; the default is chosen to
# fit inside the free tier rather than to be as fresh as possible.
CACHE_TTL = float(os.environ.get("ASTRA_ADVISOR_TTL", "600"))
# The trade card is computed from bars alone — no model call — so it can refresh
# much faster than the opinion without spending any quota.
CARD_TTL = float(os.environ.get("ASTRA_ADVISOR_CARD_TTL", "60"))
# When the provider answers 429 (daily quota spent), stop calling it for this
# long instead of retrying on every request and burning the request rate too.
RATE_LIMIT_BACKOFF = float(os.environ.get("ASTRA_ADVISOR_BACKOFF", "900"))
LOG_PATH = os.environ.get("ASTRA_ADVISOR_LOG",
                          os.path.join(ROOT, "run", "advisor_advisories.jsonl"))

# Self-review: how long to wait before judging a call, and where the scored
# outcomes live. The advisor reads its own record back and injects the misses
# into the next prompt so it does not silently repeat them.
REVIEW_HORIZON_S = float(os.environ.get("ASTRA_ADVISOR_REVIEW_HORIZON", "3600"))
OUTCOME_PATH = os.environ.get("ASTRA_ADVISOR_OUTCOMES",
                              os.path.join(ROOT, "run", "advisor_outcomes.jsonl"))
REVIEW_LESSONS = os.environ.get("ASTRA_ADVISOR_LESSONS", "1").strip() != "0"

# Headlines are pulled from a keyless, free RSS search so the advisor can ground
# its read in the day's actual news instead of guessing. Google News RSS needs
# no key and no quota; set ASTRA_ADVISOR_NEWS=0 to turn the fetch off (the
# advisor then honestly reports news_used=false).
NEWS_ENABLED = os.environ.get("ASTRA_ADVISOR_NEWS", "1").strip() != "0"
NEWS_URL = os.environ.get(
    "ASTRA_ADVISOR_NEWS_URL",
    "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en")
NEWS_QUERY = os.environ.get("ASTRA_ADVISOR_NEWS_QUERY",
                            "gold price XAUUSD when:1d")
NEWS_LIMIT = int(os.environ.get("ASTRA_ADVISOR_NEWS_LIMIT", "8"))
NEWS_TTL = float(os.environ.get("ASTRA_ADVISOR_NEWS_TTL", "900"))

# The economic calendar, so the advisor can warn about the scheduled release
# that will actually move gold (CPI, NFP, FOMC...). Keyless XML from
# FairEconomy's mirror of the Forex Factory calendar (the JSON twin 429s
# quickly; the XML does not). Set ASTRA_ADVISOR_CALENDAR=0 to turn it off. Only
# high-impact events for the currencies that drive gold are kept.
CALENDAR_ENABLED = os.environ.get("ASTRA_ADVISOR_CALENDAR", "1").strip() != "0"
CALENDAR_URL = os.environ.get(
    "ASTRA_ADVISOR_CALENDAR_URL",
    "https://nfs.faireconomy.media/ff_calendar_thisweek.xml")
CALENDAR_CURRENCIES = tuple(
    c.strip().upper() for c in
    os.environ.get("ASTRA_ADVISOR_CALENDAR_CCY", "USD,EUR").split(",") if c.strip())
CALENDAR_HORIZON_H = float(os.environ.get("ASTRA_ADVISOR_CALENDAR_HORIZON", "48"))
CALENDAR_LIMIT = int(os.environ.get("ASTRA_ADVISOR_CALENDAR_LIMIT", "5"))
CALENDAR_TTL = float(os.environ.get("ASTRA_ADVISOR_CALENDAR_TTL", "3600"))
# After a failed fetch (the feed 429s datacenter IPs when hammered) wait before
# trying again, so the server makes at most one request per retry window.
CALENDAR_RETRY_TTL = float(os.environ.get("ASTRA_ADVISOR_CALENDAR_RETRY", "600"))

# Arabic names for the indicators that move gold, so the event reads in Arabic
# even before the model restates it. Matched as lowercase substrings.
_EVENT_AR = (
    ("core cpi", "مؤشر أسعار المستهلك الأساسي (Core CPI)"),
    ("cpi", "مؤشر أسعار المستهلك (CPI)"),
    ("core pce", "مؤشر أسعار نفقات الاستهلاك الأساسي (Core PCE)"),
    ("pce", "مؤشر أسعار نفقات الاستهلاك (PCE)"),
    ("non-farm", "التغيّر في الوظائف غير الزراعية (NFP)"),
    ("nonfarm", "التغيّر في الوظائف غير الزراعية (NFP)"),
    ("unemployment rate", "معدّل البطالة"),
    ("unemployment claims", "طلبات إعانة البطالة"),
    ("federal funds rate", "سعر الفائدة الفيدرالي"),
    ("fomc", "قرار الفائدة الفيدرالي (FOMC)"),
    ("fed", "الفيدرالي"),
    ("interest rate", "قرار سعر الفائدة"),
    ("gdp", "الناتج المحلي الإجمالي (GDP)"),
    ("ppi", "مؤشر أسعار المنتجين (PPI)"),
    ("retail sales", "مبيعات التجزئة"),
    ("ism", "مؤشر ISM"),
    ("pmi", "مؤشر مديري المشتريات (PMI)"),
    ("adp", "تقرير ADP للوظائف"),
    ("crude oil inventories", "مخزونات النفط الخام"),
    ("jolts", "فرص العمل (JOLTS)"),
    ("consumer confidence", "ثقة المستهلك"),
    ("ecb", "قرار البنك المركزي الأوروبي (ECB)"),
    ("speaks", "تصريحات"),
)

# The response contract. Stable so the app and any later scorer can rely on it.
SCHEMA_VERSION = "1.0"

_SYSTEM_PROMPT = (
    "You are a gold (XAUUSD) market commentator embedded in a research tool. "
    "You are given a factual snapshot of one instrument produced by a rule "
    "engine, recent headlines, and any high-impact economic events due soon. "
    "Give a short, concrete read of the market and a stance.\n"
    "Language: write every human-readable field in Arabic (the reader is an "
    "Arabic speaker). Keep JSON keys and the stance value in English.\n"
    "Rules:\n"
    "- Base your answer only on the snapshot, the headlines and the events "
    "provided. Do not invent prices, events or dates.\n"
    "- If the snapshot has no headlines, set news_used to false and say so "
    "plainly in the thesis. Never imply you saw live news you did not.\n"
    "- When a high-impact event is listed in the snapshot, treat it as the day's "
    "pivot: name it, say which outcome helps gold and which hurts it, and shape "
    "the plan around it.\n"
    "- The snapshot carries an `mtf` block: a rule engine's multi-timeframe read "
    "(M5, M15, M30, H1, H4) with a verdict and per-timeframe directions. If its "
    "verdict is not NEUTRAL, align your stance with it and explain the "
    "agreement; if it is NEUTRAL, do not force a trade.\n"
    "- This is commentary, not advice. Do not promise returns.\n"
    "Reply with JSON only, keys: stance (one of LONG, SHORT, NEUTRAL), "
    "confidence (0..1, your own, not the engine's), thesis (one or two Arabic "
    "sentences), risks (one Arabic sentence), news_used (true or false), "
    "event (the Arabic name of the key event you built the read around, or "
    "empty string), plan (one object with Arabic text: bias, trigger, note — a "
    "professional pre-trade plan keyed to the event). Leave every price out of "
    "the plan: the exact entry, stop and target are computed server-side from "
    "closed bars and shown next to it, so do not restate them."
)


def _get_json(url: str) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def fetch_news() -> list[dict]:
    """Recent gold headlines from a keyless RSS search. Never raises."""
    if not NEWS_ENABLED:
        return []
    url = NEWS_URL.format(query=urllib.parse.quote(NEWS_QUERY))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            root = ET.fromstring(resp.read())
    except (urllib.error.URLError, OSError, ValueError, ET.ParseError):
        return []
    out: list[dict] = []
    for item in root.findall(".//item")[:NEWS_LIMIT]:
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        out.append({
            "title": title,
            "published": (item.findtext("pubDate") or "").strip(),
            "source": ((item.findtext("source") or "").strip()),
        })
    return out


_news_cache: dict = {"at": 0.0, "items": []}


def recent_news() -> list[dict]:
    """Headlines with a long TTL: the feed changes slowly and is best-effort."""
    now = time.time()
    if _news_cache["items"] and now - _news_cache["at"] < NEWS_TTL:
        return _news_cache["items"]
    items = fetch_news()
    if items:
        _news_cache["items"], _news_cache["at"] = items, now
    return _news_cache["items"]


def arabic_event_name(title: str) -> str:
    """Arabic label for a known indicator, else the original title."""
    low = (title or "").lower()
    for needle, arabic in _EVENT_AR:
        if needle in low:
            return arabic
    return title


def _parse_calendar_stamp(date: str, tm: str) -> float | None:
    """Forex Factory rows carry '10-14-2026' plus a 12-hour 'time' in UTC."""
    date, tm = (date or "").strip(), (tm or "").strip()
    if not date or not tm:
        return None
    for fmt in ("%m-%d-%Y %I:%M%p", "%m-%d-%Y %I:%M %p"):
        try:
            dt = datetime.strptime(date + " " + tm, fmt).replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue
    return None


def fetch_calendar() -> list[dict]:
    """High-impact USD/EUR events due soon, from the keyless XML feed."""
    if not CALENDAR_ENABLED:
        return []
    try:
        req = urllib.request.Request(CALENDAR_URL,
                                     headers={"User-Agent": _USER_AGENT,
                                              "Accept": "application/xml,text/xml,*/*"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            root = ET.fromstring(resp.read())
    except (urllib.error.URLError, OSError, ValueError, ET.ParseError):
        return []
    now = time.time()
    out: list[dict] = []
    for event in root.findall(".//event"):
        if (event.findtext("impact") or "").strip().lower() != "high":
            continue
        ccy = (event.findtext("country") or "").strip().upper()
        if CALENDAR_CURRENCIES and ccy not in CALENDAR_CURRENCIES:
            continue
        at = _parse_calendar_stamp(event.findtext("date") or "",
                                   event.findtext("time") or "")
        if at is None:
            continue
        hours = (at - now) / 3600.0
        if hours < -1.0 or hours > CALENDAR_HORIZON_H:
            continue
        title = (event.findtext("title") or "").strip()
        out.append({
            "title": title,
            "title_ar": arabic_event_name(title),
            "country": ccy,
            "impact": "high",
            "at_utc": int(at),
            "in_hours": round(hours, 1),
            "forecast": (event.findtext("forecast") or "").strip() or None,
            "previous": (event.findtext("previous") or "").strip() or None,
        })
    out.sort(key=lambda e: e["at_utc"])
    return out[:CALENDAR_LIMIT]


_calendar_cache: dict = {"at": 0.0, "items": [], "failed_at": 0.0}


def upcoming_events() -> list[dict]:
    now = time.time()
    if _calendar_cache["items"] and now - _calendar_cache["at"] < CALENDAR_TTL:
        return _calendar_cache["items"]
    # Hold off after a failure so a rate-limited feed is not hammered.
    if now - _calendar_cache["failed_at"] < CALENDAR_RETRY_TTL:
        return _calendar_cache["items"]
    items = fetch_calendar()
    if items:
        _calendar_cache["items"], _calendar_cache["at"] = items, now
        _calendar_cache["failed_at"] = 0.0
    else:
        _calendar_cache["failed_at"] = now
    return _calendar_cache["items"]


def fetch_bars_by_tf() -> dict:
    """Closed bars for the five read timeframes, or the subset that answers.

    Mirrors ``mobile/serve_mobile.py``: the bridge wraps bars in an envelope
    (``{"payload": {"bars": [...]}}``) and also accepts a bare body, so both
    shapes are tolerated.
    """
    out: dict = {}
    for tf in MTF_TIMEFRAMES:
        url = "%s/v1/candles?tf=%s&limit=%d" % (BRIDGE_BASE, tf, MTF_BARS)
        payload = _get_json(url)
        if not isinstance(payload, dict):
            continue
        if isinstance(payload.get("payload"), dict):
            payload = payload["payload"]
        bars = payload.get("bars") or payload.get("candles") or []
        if bars:
            out[tf] = bars
    return out


def build_snapshot() -> dict:
    """Collect the facts the advisor reasons over. No opinion is formed here."""
    snapshot: dict = {
        "symbol": SYMBOL,
        "captured_at_utc": int(time.time()),
        "price": None,
        "bid": None,
        "ask": None,
        "analysis": None,
        "analysis_available": False,
    }

    tick = _get_json("%s/v1/tick?symbol=%s" % (BRIDGE_BASE, SYMBOL))
    if tick and isinstance(tick.get("payload"), dict):
        payload = tick["payload"]
        snapshot["price"] = payload.get("last")
        snapshot["bid"] = payload.get("bid")
        snapshot["ask"] = payload.get("ask")

    analysis = _get_json("%s/api/v1/analysis/latest" % API_BASE)
    if analysis and isinstance(analysis.get("data"), dict):
        data = analysis["data"]
        # The route answers with {available:false, reason:...} before the first
        # decision; that is a real state, not an error, so record it as-is.
        if data.get("available") is False:
            snapshot["analysis_note"] = data.get("reason")
        else:
            snapshot["analysis_available"] = True
            snapshot["analysis"] = {
                "context": data.get("context"),
                "signal": data.get("signal"),
                "levels": data.get("levels"),
            }

    headlines = recent_news()
    snapshot["news"] = headlines
    snapshot["news_available"] = bool(headlines)
    events = upcoming_events()
    snapshot["events"] = events
    snapshot["events_available"] = bool(events)

    # The multi-timeframe read: exact numbers computed here, never by the model.
    # Only the compact read goes into the prompt; the full card (with levels)
    # stays server-side so a hallucinated price cannot reach the reader.
    mtf = mtf_read.build(fetch_bars_by_tf())
    snapshot["mtf_available"] = bool(mtf.get("available"))
    snapshot["mtf"] = mtf
    return snapshot


def build_prompt(snapshot: dict, lessons: str = "") -> str:
    prompt = _SYSTEM_PROMPT
    if lessons:
        prompt += "\n\n" + lessons
    return prompt + "\n\nSnapshot:\n" + json.dumps(_prompt_view(snapshot),
                                                   indent=2, sort_keys=True)


def _prompt_view(snapshot: dict) -> dict:
    """The snapshot as the model sees it: the MTF read, but not the card levels.

    The entry/stop/target are computed server-side and shown to the reader
    verbatim; withholding them from the prompt removes any temptation for the
    model to restate a number it did not calculate.
    """
    view = dict(snapshot)
    mtf = view.get("mtf")
    if isinstance(mtf, dict):
        view["mtf"] = {
            "available": mtf.get("available"),
            "verdict": mtf.get("verdict"),
            "agreement": mtf.get("agreement"),
            "timeframes": mtf.get("timeframes"),
            "reason": mtf.get("reason"),
            "note": mtf.get("note"),
        }
    return view


def parse_advisory(text: str) -> dict | None:
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
    stance = str(parsed.get("stance", "")).upper()
    if stance not in ("LONG", "SHORT", "NEUTRAL"):
        stance = "NEUTRAL"
    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    plan = parsed.get("plan")
    if not isinstance(plan, dict):
        plan = None
    else:
        plan = {k: str(plan.get(k, "")).strip()
                for k in ("bias", "trigger", "note")}
    return {
        "stance": stance,
        "confidence": max(0.0, min(1.0, confidence)),
        "thesis": str(parsed.get("thesis", "")).strip(),
        "risks": str(parsed.get("risks", "")).strip(),
        "news_used": bool(parsed.get("news_used", False)),
        "event": str(parsed.get("event", "")).strip(),
        "plan": plan,
    }


def _merge_card_levels(opinion: dict, mtf: dict | None) -> dict:
    """Ground the plan in the rule-based card, model or no model.

    The plan's entry/stop/target come from the card computed from closed bars,
    never from the model: it is told not to quote prices, so those levels would
    otherwise be "--" while the card beside them shows real numbers. Bias,
    trigger and note are the model's prose when there is a model, and the card's
    own direction/basis when there is not. When there is no card at all (a
    NEUTRAL read), the plan is left alone — there is nothing to ground it in.
    """
    card = (mtf or {}).get("card") or {}
    if not card:
        return opinion
    plan = opinion.get("plan")
    if not isinstance(plan, dict):
        plan = {}
    if not plan.get("bias"):
        plan["bias"] = mtf_read.direction_label(card.get("direction") or "")
    if not plan.get("note"):
        plan["note"] = card.get("basis", "")
    for key in ("entry", "stop", "target"):
        plan[key] = str(card[key]) if card.get(key) is not None else "--"
    targets = card.get("targets") or []
    plan["targets"] = [str(t) for t in targets]
    opinion["plan"] = plan
    return opinion


def _is_model_plan_limit(headers) -> bool:
    """True when a 429 is a per-model plan limit rather than a spent quota.

    Mistral's free Experiment tier answers 429 with
    ``x-ratelimit-limit-req-minute: 0`` for a model it does not serve on that
    plan: the key is fine and other models still answer, so the chain should
    move on instead of pausing the whole provider.
    """
    try:
        limit = (headers or {}).get("x-ratelimit-limit-req-minute", "")
    except AttributeError:
        return False
    return str(limit).strip() == "0"


_GENERIC_PROVIDER_LINES = frozenset({
    "provider returned error", "error", "internal server error",
    "bad gateway", "upstream error", "an error occurred",
    "service unavailable", "unknown error",
})

# How much of an error body to read and to keep. A gateway nests the real fault
# two or three levels deep (see ``_unwrap_provider_error``), so the first 200
# bytes of a wrapped error can be pure wrapper — the cause sits further in. 4 KB
# covers every wrapped body seen while still bounding what reaches a log line.
_ERROR_BODY_LIMIT = 4096
_ERROR_REASON_LIMIT = 400


def _note_auth_failure(code: int, detail: str) -> None:
    """Record a rejected credential so the payload can say *why* it is dark.

    A 401/403 is a different failure from a network error or a spent quota: the
    key is present but the provider refused it (expired, revoked, or wrong
    provider). Collapsing it into the generic "no usable reply" hides the one
    thing an operator needs to see, so it is kept here and surfaced verbatim.
    """
    global _AUTH_FAILURE
    _AUTH_FAILURE = {"code": code,
                     "message": _redact(_unwrap_provider_error(detail))
                     or ("HTTP %d" % code)}


def _redact(detail: str) -> str:
    """The provider's words, trimmed and with anything key-shaped removed.

    An error body is echoed to the operator and into logs, and a provider may
    quote the credential back inside its message. Anything that looks like a
    bearer token or an ``sk-`` key is replaced before it can be displayed.
    """
    text = (detail or "").strip()[:_ERROR_REASON_LIMIT]
    text = re.sub(r"(?i)\b(bearer)\s+\S+", r"\1 <redacted>", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_\-]{6,}", "<redacted>", text)
    return text


def _strings_in(obj) -> "list[str]":
    """Every plain-text string reachable in a JSON value, outer before inner.

    A string that is itself JSON is not yielded — only its contents are — so a
    gateway's raw passthrough is descended into rather than reported verbatim.
    """
    found: list[str] = []
    if isinstance(obj, str):
        try:
            inner = json.loads(obj)
        except ValueError:
            return [obj]
        if isinstance(inner, (dict, list)):
            return _strings_in(inner)
        return [obj]
    if isinstance(obj, dict):
        for value in obj.values():
            found.extend(_strings_in(value))
    elif isinstance(obj, list):
        for value in obj:
            found.extend(_strings_in(value))
    return found


def _unwrap_provider_error(raw: str) -> str:
    """The most specific sentence inside a nested provider error.

    A gateway in front of the model (OpenRouter, Cloudflare AI Gateway) wraps
    the upstream fault two or three levels deep and repeats its own generic
    line at every level::

        {"error": {"message": "Provider returned error",
                   "metadata": {"raw": "{\\"title\\": \\"Not Found\\",
                                       \\"detail\\": \\"Function '...'
                                       Not found for account ...\\"}"}}}

    The outer "Provider returned error" names nothing an operator can act on,
    while the innermost line names the function, the account, or the model that
    was refused. So every string in the body is collected, the generic wrappers
    are dropped, and the longest remaining line is reported — the specific one.

    Best-effort by design: a body that is not JSON is returned unchanged, so
    nothing that already reads correctly is ever mangled.
    """
    text = (raw or "").strip()
    if not text:
        return text
    try:
        parsed = json.loads(text)
    except ValueError:
        return text
    if not isinstance(parsed, (dict, list)):
        return text
    strings = [s.strip() for s in _strings_in(parsed) if s and s.strip()]
    specific = [s for s in strings
                if s.lower() not in _GENERIC_PROVIDER_LINES]
    if specific:
        return max(specific, key=len)
    return strings[-1] if strings else text


def _note_http_failure(code: int, detail: str) -> None:
    """Record any transport-level failure, not only a rejected key.

    A 400 from a bad model name, a 402 from an empty balance, a 404 from a
    wrong base URL: each is actionable and each used to be flattened into
    "no usable reply", which told the operator nothing about what to change.
    Kept apart from ``_AUTH_FAILURE`` so the 401/403 wording stays specific.
    """
    global _LAST_HTTP_FAILURE
    _LAST_HTTP_FAILURE = {"code": int(code),
                          "message": _redact(_unwrap_provider_error(detail))}


def last_http_failure() -> dict | None:
    """The last transport failure seen on a model call, or None."""
    return _LAST_HTTP_FAILURE


def _note_empty_reply(why: str) -> None:
    """Record a 2xx reply that carried no usable text."""
    global _LAST_HTTP_FAILURE
    _LAST_HTTP_FAILURE = {"code": 0, "message": why}


def failure_reason() -> str:
    """A one-line, human-readable reason for the last failed model call.

    Empty when nothing is known, so a caller can append it to its own sentence
    without printing a dangling bracket.
    """
    failure = _LAST_HTTP_FAILURE
    if not failure:
        return ""
    code, message = failure.get("code"), failure.get("message") or ""
    if code:
        return "HTTP %d%s" % (code, (": " + message) if message else "")
    return message


def auth_failure() -> dict | None:
    """The last credential rejection seen, or None. Cleared by a good call."""
    return _AUTH_FAILURE


def _clear_auth_failure() -> None:
    global _AUTH_FAILURE
    _AUTH_FAILURE = None


def call_openai_compatible(url: str, model: str, prompt: str, api_key: str,
                           label: str) -> str | None:
    """A chat completion on any OpenAI-compatible endpoint.

    A 429 means the daily token budget is spent, not that the key is wrong, so
    it is recorded in ``_rate_limited_until`` (read by ``Advisor._build``) to
    back off instead of hammering the provider on every request. The one
    exception is a per-model plan limit (see ``_is_model_plan_limit``): that
    marks the model, not the provider, and lets the caller try the next one.
    """
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + api_key,
                 "User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        with contextlib.suppress(OSError):
            detail = exc.read().decode("utf-8", "replace")[:_ERROR_BODY_LIMIT]
        sys.stderr.write("[advisor] %s HTTP %s: %s\n" % (label, exc.code, detail))
        _note_http_failure(exc.code, detail)
        if exc.code in (401, 403):
            # A rejected key is not a transient failure; name it so the caller
            # can report "expired/revoked" instead of "no usable reply".
            _note_auth_failure(exc.code, detail)
        elif exc.code == 429 and not _is_model_plan_limit(exc.headers):
            # Back off the provider that answered, not the configured default:
            # with a failover chain the two differ.
            _mark_rate_limited(label if label in _KEY_ENV else None)
        return None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        sys.stderr.write("[advisor] %s request failed: %s\n" % (label, exc))
        _note_empty_reply("request failed: %s" % exc)
        return None
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        # A 2xx that carries an error object or an unexpected shape. OpenRouter
        # and friends answer 200 with ``{"error": {...}}`` when an upstream
        # provider fails, so this is a real failure, not an empty completion.
        err = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(err, dict):
            _note_empty_reply(str(err.get("message") or err))
        else:
            _note_empty_reply("the provider returned no message content")
        return None
    if not (content or "").strip():
        _note_empty_reply("the provider returned an empty message")
        return None
    return content


def call_groq(prompt: str, api_key: str) -> str | None:
    return call_openai_compatible(GROQ_URL, GROQ_MODEL, prompt, api_key, "groq")


# The model a provider tries first. Each reads the same ``ASTRA_ADVISOR_MODEL``
# override, so pinning one model pins it for whichever provider is active.
_MODELS = {"groq": GROQ_MODEL, "gemini": GEMINI_MODEL,
           "mistral": MISTRAL_MODEL}


def _model_for(provider: str) -> str | None:
    """The first model to try for ``provider``, or None if the name is unknown."""
    return _MODELS.get(provider)


def _provider_chain() -> tuple[str, ...]:
    """The providers to try, in order, until one answers.

    ``ASTRA_ADVISOR_PROVIDERS`` (comma-separated, e.g. ``groq,gemini,mistral``)
    sets a failover chain: a provider whose daily quota is spent steps aside for
    the next instead of pausing every model surface. Unset, the chain is the
    single configured ``PROVIDER``, so behaviour is exactly as before. Unknown
    names are dropped; a list with nothing recognisable falls back to
    ``PROVIDER``.
    """
    raw = os.environ.get("ASTRA_ADVISOR_PROVIDERS", "").strip()
    names = ([p.strip().lower() for p in raw.split(",") if p.strip()]
             if raw else [PROVIDER])
    seen: list[str] = []
    for name in names:
        if name in _KEY_ENV and name not in seen:
            seen.append(name)
    return tuple(seen) or (PROVIDER,)


def _key_for(provider: str) -> str:
    """The API key for one provider, or empty if unset."""
    return os.environ.get(_KEY_ENV.get(provider, ""), "").strip()


def _model_chain(provider: str | None = None) -> tuple[str, ...]:
    """The models to try for one provider, in order.

    ``ASTRA_ADVISOR_MODELS`` (comma-separated) replaces the chain outright, so
    an operator can pin their own order. Otherwise the configured model comes
    first and the provider's free-tier fallbacks follow, de-duplicated.
    """
    name = provider or PROVIDER
    override = os.environ.get("ASTRA_ADVISOR_MODELS", "").strip()
    if override:
        chain = [m.strip() for m in override.split(",") if m.strip()]
    else:
        first = _model_for(name)
        chain = ([first] if first else []) + list(_FALLBACK_MODELS.get(name, ()))
    seen: list[str] = []
    for model in chain:
        if model and model not in seen:
            seen.append(model)
    return tuple(seen)


def call_mistral(prompt: str, api_key: str) -> str | None:
    return call_openai_compatible(MISTRAL_URL, MISTRAL_MODEL, prompt, api_key,
                                  "mistral")


def call_model(prompt: str, api_key: str) -> str | None:
    """Dispatch to a provider, failing over down the chain until one answers.

    ``api_key`` is the active provider's key, kept in the signature so every
    existing caller is unchanged; a fallback provider is looked up with its own
    key. Providers with a model chain (Mistral) are walked model by model: a
    per-model plan limit moves to the next model, while a real quota 429 backs
    that provider off and moves to the *next provider*. The provider and model
    that actually answered are recorded so the payload can name them honestly.
    """
    global _LAST_MODEL, _LAST_PROVIDER, _LAST_HTTP_FAILURE
    _LAST_MODEL = None
    _LAST_PROVIDER = None
    _LAST_HTTP_FAILURE = None
    for provider in _provider_chain():
        key = api_key if provider == PROVIDER else _key_for_provider(provider)
        if not key:
            continue
        if _provider_backed_off(provider):
            # This one spent its quota; the next provider still has budget.
            continue
        reply, model = _call_provider(provider, prompt, key)
        if reply is not None:
            _LAST_MODEL = model
            _LAST_PROVIDER = provider
            _clear_auth_failure()
            return reply
    return None


def _call_provider(provider: str, prompt: str, key: str) -> tuple[str | None, str | None]:
    """One provider's call: ``(reply, model_that_answered)``.

    A None reply means this provider failed; the caller decides whether to move
    on. Only a spent quota stops a provider's own model walk early, because a
    plan limit on one model says nothing about the next.
    """
    if provider == "gemini":
        return call_gemini(prompt, key), GEMINI_MODEL
    if provider == "groq":
        return call_groq(prompt, key), GROQ_MODEL
    if provider == "mistral":
        for model in _model_chain("mistral"):
            reply = call_openai_compatible(MISTRAL_URL, model, prompt, key,
                                           "mistral")
            if reply is not None:
                return reply, model
            if _provider_backed_off("mistral"):
                return None, None
        return None, None
    return None, None


def provider_key() -> str:
    """The API key for the active provider, or empty if unset."""
    return os.environ.get(_KEY_ENV.get(PROVIDER, ""), "").strip()


def _key_for_provider(provider: str) -> str:
    """One provider's key, routed through ``provider_key`` for the active one.

    The active provider's key goes through the public ``provider_key`` so an
    existing caller that patches it keeps working; a fallback reads its own env
    var directly, since ``provider_key`` only knows the active provider.
    """
    return provider_key() if provider == PROVIDER else _key_for(provider)


def available_provider() -> str | None:
    """The first provider in the chain that has a key, or None.

    With no failover configured this is simply the active provider when its key
    is set, so a single-provider deployment reads exactly as before.
    """
    for provider in _provider_chain():
        if _key_for_provider(provider):
            return provider
    return None


def available_key() -> str:
    """The key of the first provider in the chain that has one, or empty.

    ``provider_key()`` names one provider; this names the first that can
    actually be called, so a deployment that sets only a fallback key is
    ``configured`` rather than dark.
    """
    provider = available_provider()
    return _key_for_provider(provider) if provider else ""


def active_model() -> str | None:
    """The model a fresh call would try first (the head of the chain)."""
    return _model_for(PROVIDER)


def answered_model() -> str | None:
    """The model that produced the last reply, or None if none has answered."""
    return _LAST_MODEL


# Unix time until which each provider should not be called again (set when it
# answers 429). Keyed by provider so a spent Groq quota steps aside for Gemini
# without darkening it too; module-level so every handler shares the back-off.
_rate_limited_until: dict[str, float] = {}

# The model that produced the last usable reply (see ``call_model``). None until
# one has answered. Reported instead of the configured default so a payload
# never names a model that did not speak.
_LAST_MODEL: str | None = None

# The provider that produced the last usable reply, or None. With a failover
# chain the answering provider is not necessarily ``PROVIDER``, so the payload
# names this rather than the configured default.
_LAST_PROVIDER: str | None = None

# The last credential rejection (HTTP 401/403) seen, as
# ``{"code": int, "message": str}``, or None. Kept separate from the reply so a
# caller can tell "the key is refused" apart from "the provider said nothing".
_AUTH_FAILURE: dict | None = None

# The last transport failure (any HTTP status, or a 2xx with no usable body),
# as ``{"code": int, "message": str}`` with code 0 for a body-level failure.
# Distinct from ``_AUTH_FAILURE``: this covers the failures that are neither a
# rejected key nor a spent quota — a wrong model name, an empty balance, a
# wrong base URL — each of which used to be flattened into "no usable reply".
_LAST_HTTP_FAILURE: dict | None = None


def _mark_rate_limited(provider: str | None = None) -> None:
    """Back off one provider (default: the active one) after a spent-quota 429."""
    global _rate_limited_until
    if not isinstance(_rate_limited_until, dict):
        # A scalar deadline predates the per-provider map (and is what a test
        # sets to reset state); fold it in under the active provider.
        _rate_limited_until = {PROVIDER: _rate_limited_until}
    _rate_limited_until[provider or PROVIDER] = time.time() + RATE_LIMIT_BACKOFF


def _backoff_deadline(provider: str) -> float:
    """The back-off deadline for one provider, tolerating a legacy scalar."""
    if isinstance(_rate_limited_until, dict):
        return float(_rate_limited_until.get(provider, 0.0))
    # A bare number counts for the active provider only, so a chain still
    # fails over to the next one.
    return float(_rate_limited_until) if provider == PROVIDER else 0.0


def _provider_backed_off(provider: str) -> bool:
    """True while ``provider`` is backed off after its own 429."""
    return time.time() < _backoff_deadline(provider)


def rate_limited(provider: str | None = None) -> bool:
    """True while ``provider`` (default: the active one) is backed off.

    With a failover chain this is the *active* provider's state: the caller
    asks "should I skip the call?" and the answer is yes only when every
    provider in the chain is spent. A single-provider deployment reads exactly
    as before.
    """
    if provider is not None:
        return _provider_backed_off(provider)
    chain = _provider_chain()
    return bool(chain) and all(_provider_backed_off(p) for p in chain)


def answered_provider() -> str | None:
    """The provider that produced the last reply, or None."""
    return _LAST_PROVIDER


def call_gemini(prompt: str, api_key: str) -> str | None:
    url = GEMINI_URL.format(model=GEMINI_MODEL) + "?key=" + api_key
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        # Zero temperature for repeatability; an LLM is still not deterministic,
        # which is one reason this never enters the deterministic ledger.
        "generationConfig": {"temperature": 0.0,
                             "responseMimeType": "application/json"},
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        with contextlib.suppress(OSError):
            detail = exc.read().decode("utf-8", "replace")[:_ERROR_BODY_LIMIT]
        sys.stderr.write("[advisor] gemini HTTP %s: %s\n" % (exc.code, detail))
        _note_http_failure(exc.code, detail)
        if exc.code in (401, 403):
            _note_auth_failure(exc.code, detail)
        elif exc.code == 429:
            _mark_rate_limited("gemini")
        return None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        _note_empty_reply("request failed: %s" % exc)
        return None
    try:
        return payload["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        _note_empty_reply("the provider returned no message content")
        return None


def append_log(record: dict) -> None:
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass  # Logging is best-effort; it must never break the response.


def run_review(price_now: float | None) -> dict:
    """Score past calls that are past the horizon, and summarize the record.

    Reads the advisor's own advisory log, appends any newly-due outcomes, and
    returns the running summary. Entirely best-effort: a missing price or file
    yields an empty summary rather than an error.

    A missing price only stops *new* scoring; the outcomes already on file are
    still summarized, so a brief feed outage never blanks the scoreboard.
    """
    outcomes = self_review.read_jsonl(OUTCOME_PATH)
    new: list[dict] = []
    if price_now is not None:
        advisories = self_review.read_jsonl(LOG_PATH)
        new = self_review.score_due(advisories, outcomes,
                                    price_now, time.time(), REVIEW_HORIZON_S)
        for record in new:
            self_review.append_jsonl(OUTCOME_PATH, record)
    return self_review.summarize(outcomes + new)


class Advisor:
    """Builds and caches advisories. Pure logic lives in the module functions."""

    def __init__(self) -> None:
        self._cached: dict | None = None
        self._cached_at = 0.0
        self._card: dict | None = None
        self._card_at = 0.0

    def current(self, force: bool = False) -> dict:
        now = time.time()
        if not force and self._cached and now - self._cached_at < CACHE_TTL:
            return self._cached
        result = self._build()
        self._cached, self._cached_at = result, now
        return result

    def card(self) -> dict:
        """The trade card alone — bars in, levels out, no model call.

        This is what the Telegram bot and the phone read for the levels, so the
        card stays available even when the model's daily quota is spent and the
        opinion cannot be produced.
        """
        now = time.time()
        if self._card is not None and now - self._card_at < CARD_TTL:
            return self._card
        result = mtf_read.build(fetch_bars_by_tf())
        self._card, self._card_at = result, now
        return result

    def review(self) -> dict:
        """The self-review scoreboard on its own, without calling the model.

        The summary carries the full `wrong` list (not just the recent few), so
        a caller can tell which mistakes it has already alerted on.
        """
        snapshot = build_snapshot()
        summary = run_review(snapshot.get("price"))
        return {"summary": summary, "message": self_review.format_review(summary)}

    def _build(self) -> dict:
        snapshot = build_snapshot()
        # The key of the first provider in the chain that has one, so a
        # deployment that sets only a fallback key is configured, not dark.
        api_key = available_key()
        advisory = {
            "schema_version": SCHEMA_VERSION,
            "symbol": snapshot["symbol"],
            "generated_at_utc": int(time.time()),
            # The whole point: this is outside the validated system.
            "validated": False,
            "source": "ai-advisor (isolated)",
            "provider": PROVIDER,
            "configured": bool(api_key),
            "model": active_model() if api_key else None,
            "snapshot": snapshot,
            # The trade card: the only place entry/stop/target come from. Rule-
            # based and unvalidated, computed from closed bars by mtf_read.
            "trade_card": snapshot.get("mtf"),
            "opinion": None,
        }
        if not api_key:
            advisory["note"] = ("AI advisor not configured: set %s to enable a "
                                "second opinion." % _KEY_ENV.get(PROVIDER, "the "
                                "provider key"))
            return advisory

        if rate_limited():
            # A recent 429: the model's daily quota is spent. Skip the call
            # entirely (no point, and it would only spend the request rate too)
            # and say so plainly instead of the generic "unavailable".
            advisory["rate_limited"] = True
            advisory["note"] = ("AI opinion paused: the provider's daily quota "
                                "is spent. The snapshot and trade card below are "
                                "still live.")
            return advisory

        # Learn from the past before answering: score any call that is now due
        # and hand the misses to the model, so a mistake is visible next time.
        review = run_review(snapshot.get("price"))
        advisory["review"] = review
        lessons = (self_review.lessons_block(review)
                   if REVIEW_LESSONS else "")

        reply = call_model(build_prompt(snapshot, lessons), api_key)
        opinion = parse_advisory(reply) if reply else None
        if opinion is None:
            if rate_limited():
                advisory["rate_limited"] = True
                advisory["note"] = ("AI opinion paused: the provider's daily "
                                    "quota is spent. The snapshot and trade card "
                                    "below are still live.")
            else:
                why = failure_reason()
                advisory["note"] = (
                    "AI advisor unavailable: no usable reply (%s)." % why
                    if why else "AI advisor unavailable: no usable reply.")
            return advisory

        advisory["opinion"] = opinion
        _merge_card_levels(opinion, advisory["trade_card"])
        advisory["model"] = answered_model() or active_model()
        # Name the provider that actually spoke: with a failover chain that is
        # not necessarily the configured one, and the reader must be able to tell
        # which budget paid for the read.
        advisory["provider"] = answered_provider() or PROVIDER
        advisory["note"] = ("AI opinion, produced outside the validated system. "
                            "Not calibrated, not scored. Not financial advice.")
        append_log({
            "generated_at_utc": advisory["generated_at_utc"],
            "symbol": snapshot["symbol"],
            "price": snapshot["price"],
            "engine_direction": (snapshot.get("analysis") or {}).get(
                "signal", {}).get("direction"),
            "stance": opinion["stance"],
            "confidence": opinion["confidence"],
            "news_used": opinion["news_used"],
            "event": opinion.get("event") or "",
            "provider": answered_provider() or PROVIDER,
            "model": answered_model() or active_model(),
        })
        return advisory


class Handler(BaseHTTPRequestHandler):
    advisor = Advisor()

    def log_message(self, fmt, *args):  # quieter logs
        sys.stderr.write("[advisor] %s\n" % (fmt % args))

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path in ("/v1/advisor", "/advisor"):
            force = "refresh=1" in query
            self._json(200, {"advisor": self.advisor.current(force=force)})
        elif path in ("/v1/card", "/card"):
            # Model-free: the card comes from bars alone, so it answers even
            # when the opinion is paused on a spent quota.
            self._json(200, {"trade_card": self.advisor.card()})
        elif path in ("/v1/review", "/review"):
            self._json(200, {"review": self.advisor.review()})
        elif path in ("/health", "/v1/health"):
            failure = auth_failure()
            self._json(200, {"service": "ai-advisor", "status": "OK",
                             "provider": PROVIDER,
                             "configured": bool(provider_key()),
                             # ``configured`` only says a key string is set. A
                             # key that expired or was revoked still reads as
                             # configured, so the rejection itself is surfaced
                             # here — that is the state that looks healthy while
                             # every reply is empty.
                             "auth_failure": failure,
                             "rate_limited": rate_limited()})
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
    parser = argparse.ArgumentParser(description="ASTRA isolated AI advisor")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8792)
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    sys.stderr.write("[advisor] listening on http://%s:%d (provider=%s configured=%s)\n"
                     % (args.host, args.port, PROVIDER,
                        bool(provider_key())))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
