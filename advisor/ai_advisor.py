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
import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

API_BASE = os.environ.get("ASTRA_ADVISOR_API", "http://127.0.0.1:8790")
BRIDGE_BASE = os.environ.get("ASTRA_ADVISOR_BRIDGE", "http://127.0.0.1:8791")
SYMBOL = os.environ.get("ASTRA_ADVISOR_SYMBOL", "XAUUSD")
# Provider selection. Groq is the default because its free tier is global;
# Gemini's is region-restricted. Both are plain REST calls.
PROVIDER = os.environ.get("ASTRA_ADVISOR_PROVIDER", "groq").strip().lower()

GROQ_URL = os.environ.get("ASTRA_ADVISOR_GROQ_URL",
                          "https://api.groq.com/openai/v1/chat/completions")
GROQ_MODEL = os.environ.get("ASTRA_ADVISOR_MODEL", "llama-3.3-70b-versatile")
GEMINI_MODEL = os.environ.get("ASTRA_ADVISOR_MODEL", "gemini-2.5-flash")
GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
              "{model}:generateContent")

# Which env var holds the key for each provider.
_KEY_ENV = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY"}

HTTP_TIMEOUT = float(os.environ.get("ASTRA_ADVISOR_TIMEOUT", "20"))
CACHE_TTL = float(os.environ.get("ASTRA_ADVISOR_TTL", "120"))
LOG_PATH = os.environ.get("ASTRA_ADVISOR_LOG",
                          os.path.join(ROOT, "run", "advisor_advisories.jsonl"))

# The response contract. Stable so the app and any later scorer can rely on it.
SCHEMA_VERSION = "1.0"

_SYSTEM_PROMPT = (
    "You are a gold (XAUUSD) market commentator embedded in a research tool. "
    "You are given a factual snapshot of one instrument produced by a rule "
    "engine. Give a short, concrete read of the market and a stance.\n"
    "Rules:\n"
    "- Base your answer only on the snapshot and on news you can actually "
    "recall or verify. Do not invent prices, events or dates.\n"
    "- If you cannot verify current news, set news_used to false and say so "
    "plainly in the thesis. Never imply you saw live news you did not.\n"
    "- This is commentary, not advice. Do not promise returns.\n"
    "Reply with JSON only, keys: stance (one of LONG, SHORT, NEUTRAL), "
    "confidence (0..1, your own, not the engine's), thesis (one or two "
    "sentences), risks (one sentence), news_used (true or false)."
)


def _get_json(url: str) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


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
    return snapshot


def build_prompt(snapshot: dict) -> str:
    return (_SYSTEM_PROMPT + "\n\nSnapshot:\n"
            + json.dumps(snapshot, indent=2, sort_keys=True))


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
    return {
        "stance": stance,
        "confidence": max(0.0, min(1.0, confidence)),
        "thesis": str(parsed.get("thesis", "")).strip(),
        "risks": str(parsed.get("risks", "")).strip(),
        "news_used": bool(parsed.get("news_used", False)),
    }


def call_groq(prompt: str, api_key: str) -> str | None:
    """OpenAI-compatible chat completion on Groq."""
    body = json.dumps({
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(
        GROQ_URL, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + api_key})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
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


class Advisor:
    """Builds and caches advisories. Pure logic lives in the module functions."""

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
            "opinion": None,
        }
        if not api_key:
            advisory["note"] = ("AI advisor not configured: set %s to enable a "
                                "second opinion." % _KEY_ENV.get(PROVIDER, "the "
                                "provider key"))
            return advisory

        reply = call_model(build_prompt(snapshot), api_key)
        opinion = parse_advisory(reply) if reply else None
        if opinion is None:
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
            "provider": PROVIDER,
            "model": active_model(),
        })
        return advisory


class Handler(BaseHTTPRequestHandler):
    advisor = Advisor()

    def log_message(self, fmt, *args):  # quieter logs
        sys.stderr.write("[advisor] %s\n" % (fmt % args))

    def do_GET(self):  # noqa: N802
        path, _, query = self.path.partition("?")
        if path in ("/v1/advisor", "/advisor"):
            force = "refresh=1" in query
            self._json(200, {"advisor": self.advisor.current(force=force)})
        elif path in ("/health", "/v1/health"):
            self._json(200, {"service": "ai-advisor", "status": "OK",
                             "provider": PROVIDER,
                             "configured": bool(provider_key())})
        else:
            self._json(404, {"error": "unknown route", "path": path})

    def do_HEAD(self):  # noqa: N802
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
                     % (args.host, args.port, _PROVIDER,
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
