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
  * ``groq``   - GROQ_API_KEY, OpenAI-compatible, global free tier.
  * ``gemini`` - GEMINI_API_KEY, region-restricted free tier.

Run: python3 advisor/ai_advisor.py --port 8792
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
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

# Which env var holds the key for each provider.
_KEY_ENV = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY"}

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
    "agreement; if it is NEUTRAL, do not force a trade. Do not restate entry, "
    "stop or target numbers — those are computed server-side and shown "
    "verbatim; leave them as \"--\".\n"
    "- This is commentary, not advice. Do not promise returns.\n"
    "Reply with JSON only, keys: stance (one of LONG, SHORT, NEUTRAL), "
    "confidence (0..1, your own, not the engine's), thesis (one or two Arabic "
    "sentences), risks (one Arabic sentence), news_used (true or false), "
    "event (the Arabic name of the key event you built the read around, or "
    "empty string), plan (one object with Arabic text: bias, trigger, "
    "entry, stop, target, note — a professional pre-trade plan keyed to the "
    "event; use \"--\" for any number you cannot ground in the snapshot)."
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
                for k in ("bias", "trigger", "entry", "stop", "target", "note")}
    return {
        "stance": stance,
        "confidence": max(0.0, min(1.0, confidence)),
        "thesis": str(parsed.get("thesis", "")).strip(),
        "risks": str(parsed.get("risks", "")).strip(),
        "news_used": bool(parsed.get("news_used", False)),
        "event": str(parsed.get("event", "")).strip(),
        "plan": plan,
    }


def call_groq(prompt: str, api_key: str) -> str | None:
    """OpenAI-compatible chat completion on Groq.

    A 429 means the daily token budget is spent, not that the key is wrong, so
    it is recorded in ``_rate_limited_until`` (read by ``Advisor._build``) to
    back off instead of hammering the provider on every request.
    """
    body = json.dumps({
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(
        GROQ_URL, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + api_key,
                 "User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        with contextlib.suppress(OSError):
            detail = exc.read().decode("utf-8", "replace")[:200]
        sys.stderr.write("[advisor] groq HTTP %s: %s\n" % (exc.code, detail))
        if exc.code == 429:
            _mark_rate_limited()
        return None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        sys.stderr.write("[advisor] groq request failed: %s\n" % (exc,))
        return None
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


def call_model(prompt: str, api_key: str) -> str | None:
    """Dispatch to the configured provider. Unknown providers are inert."""
    if PROVIDER == "gemini":
        return call_gemini(prompt, api_key)
    if PROVIDER == "groq":
        return call_groq(prompt, api_key)
    return None


def provider_key() -> str:
    """The API key for the active provider, or empty if unset."""
    return os.environ.get(_KEY_ENV.get(PROVIDER, ""), "").strip()


def active_model() -> str | None:
    return {"groq": GROQ_MODEL, "gemini": GEMINI_MODEL}.get(PROVIDER)


# Unix time until which the provider should not be called again (set when it
# answers 429). Module-level so every request handler shares the back-off.
_rate_limited_until = 0.0


def _mark_rate_limited() -> None:
    global _rate_limited_until
    _rate_limited_until = time.time() + RATE_LIMIT_BACKOFF


def rate_limited() -> bool:
    """True while the provider is being backed off after a 429."""
    return time.time() < _rate_limited_until


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
    except (urllib.error.URLError, OSError, ValueError):
        return None
    try:
        return payload["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
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
        api_key = provider_key()
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
                advisory["note"] = "AI advisor unavailable: no usable reply."
            return advisory

        advisory["opinion"] = opinion
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
            "provider": PROVIDER,
            "model": active_model(),
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
            self._json(200, {"service": "ai-advisor", "status": "OK",
                             "provider": PROVIDER,
                             "configured": bool(provider_key()),
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
