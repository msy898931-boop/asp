#!/usr/bin/env python3
"""ASTRA mobile launcher: serve the PWA and reverse-proxy the backend API.

The phone can only reach the machine's public port, while the backend binds to
loopback. This tiny standard-library server bridges the two:

    GET /                -> mobile/index.html (the app shell)
    GET /api/v1/<...>    -> http://127.0.0.1:<api-port>/api/v1/<...>
    GET /bridge/<...>    -> http://127.0.0.1:<bridge-port>/<...>

It is read-only by construction: only GET/HEAD are proxied, and the upstream is
fixed to loopback, so the app cannot be used to reach anything else.

Access control is optional. Set ASTRA_ACCESS_TOKEN (or --access-token) and every
route but /api/v1/health needs the key; leave it unset and the server is open
exactly as it always was. A first visit with `?k=KEY` sets a cookie and
redirects to a clean URL, so the key is not left in the address bar.

Run: python3 mobile/serve_mobile.py --port 12000
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Web Push lives in a sibling module. It imports only the standard library at
# module load; the third-party crypto package it needs is imported lazily
# inside, so a host without it still starts and answers "not configured".
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import webpush  # noqa: E402

# Where /astra.apk points when no local build is staged: the rolling release
# the Android workflow republishes on every mobile/android change. Set
# ASTRA_REPO (owner/repo) or ASTRA_APK_URL to point at a different fork.
_APK_REPO = os.environ.get("ASTRA_REPO", "msy898931-boop/asp")
APK_FALLBACK_URL = (
    os.environ.get("ASTRA_APK_URL")
    or f"https://github.com/{_APK_REPO}/releases/download/android/astra.apk"
)

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}

# The only extensions the static shell may serve. Anything else — most of all
# the server's own .py source and its .pyc — is a 404 even when it sits inside
# HERE. The shell is a fixed, known set of assets, so an allowlist costs
# nothing and closes the "read the source over HTTP" path entirely.
_SERVE_EXTS = frozenset(_CONTENT_TYPES)

_PROXY_TIMEOUT = 6.0

# The AI engine answers from cache almost always, but a cold read does a
# synchronous model round-trip (the advisor's own budget defaults to 20s). The
# plain 6s proxy budget would turn a healthy-but-cold engine into a 503, so the
# /engine/ route gets its own, wider budget.
_ENGINE_PROXY_TIMEOUT = float(os.environ.get("ASTRA_ENGINE_TIMEOUT", "30"))

# Access control. Unset by default, and an unset token means the server is
# open exactly as it was before this gate existed — so nothing changes until
# the operator sets it. Set ASTRA_ACCESS_TOKEN to require it. This mirrors the
# ASTRA_UPGRADE_URL convention: a dormant gate, not a new default.
ACCESS_TOKEN = os.environ.get("ASTRA_ACCESS_TOKEN", "").strip()
_COOKIE_NAME = "astra_key"


def _token_matches(given: str) -> bool:
    """Constant-time compare, so a wrong token leaks no timing signal."""
    if not ACCESS_TOKEN:
        return True
    return hmac.compare_digest(given or "", ACCESS_TOKEN)


def _auth_required() -> bool:
    return bool(ACCESS_TOKEN)


def _forwarded_proto_https(handler) -> bool:
    """True when the request reached us over HTTPS.

    The service runs behind a TLS-terminating proxy (Render, Fly), so the
    socket itself is plain HTTP and the only honest signal is the proxy's
    ``X-Forwarded-Proto`` header. Used to decide whether the unlock cookie may
    carry ``Secure``: sending it over plain HTTP (a local dev run) would make
    the browser drop the cookie and lock the operator out.
    """
    proto = handler.headers.get("X-Forwarded-Proto", "")
    return proto.split(",")[0].strip().lower() == "https"


# Gate hardening. A single static key with unlimited guesses is a guessing
# oracle: an attacker can try keys as fast as the network allows. These bound
# how many *failed* tries one client address may make before it is refused for
# a cool-down. A correct key is never counted and clears the record, so an
# operator who mistypes the key a few times is never locked out. The numbers
# are deliberately generous — a human typing a key does not hit ten failures in
# five minutes — so only a brute-force pattern trips it.
_GATE_MAX_FAILURES = int(os.environ.get("ASTRA_GATE_MAX_FAILURES", "10"))
_GATE_WINDOW_S = float(os.environ.get("ASTRA_GATE_WINDOW_S", "300"))
_GATE_LOCKOUT_S = float(os.environ.get("ASTRA_GATE_LOCKOUT_S", "900"))
_GATE_MAX_TRACKED = 4096

# Whether X-Forwarded-For may be believed when attributing a failed guess.
# Default **off**: the header is attacker-controlled, and trusting it by
# default turns the rate limiter into a no-op (rotate the header, guess
# forever). Set ASTRA_TRUST_FORWARDED_FOR=1 only when the service really sits
# behind a proxy that overwrites the header (Render, Fly). Even then only the
# rightmost hop is used.
_TRUST_FORWARDED_FOR = os.environ.get(
    "ASTRA_TRUST_FORWARDED_FOR", "").strip().lower() in ("1", "true", "yes", "on")


class _RateLimiter:
    """Per-address failure counter for the access gate.

    Counts only failures, so a legitimate visitor is invisible to it; a client
    that crosses `max_failures` within `window_s` is refused for `lockout_s`.
    State is in-process and bounded: a restart forgets it (fine for one
    always-on container, and it adds no disk or shared-state dependency), and
    the tracked set is pruned so a flood of distinct addresses cannot grow it
    without limit.
    """

    def __init__(self, max_failures: int, window_s: float, lockout_s: float) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self.lockout_s = lockout_s
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = {}
        self._until: dict[str, float] = {}

    def blocked(self, key: str, now: float) -> bool:
        """True while `key` is in its cool-down; forgets it once it has passed."""
        with self._lock:
            until = self._until.get(key)
            if until is None:
                return False
            if now >= until:
                self._until.pop(key, None)
                self._hits.pop(key, None)
                return False
            return True

    def fail(self, key: str, now: float) -> None:
        with self._lock:
            hits = [t for t in self._hits.get(key, ()) if now - t < self.window_s]
            hits.append(now)
            if len(hits) >= self.max_failures:
                self._until[key] = now + self.lockout_s
                hits = []
            self._hits[key] = hits
            self._prune(now)

    def succeed(self, key: str) -> None:
        """A correct key clears the record, so it can never be locked out."""
        with self._lock:
            self._hits.pop(key, None)
            self._until.pop(key, None)

    def retry_after(self, key: str, now: float) -> int:
        with self._lock:
            until = self._until.get(key)
            return max(0, int(until - now)) if until else 0

    def reset(self) -> None:
        """Forget all state. Used by tests so one case cannot leak into the next."""
        with self._lock:
            self._hits.clear()
            self._until.clear()

    def _prune(self, now: float) -> None:
        if len(self._hits) < _GATE_MAX_TRACKED and len(self._until) < _GATE_MAX_TRACKED:
            return
        self._hits = {k: v for k, v in self._hits.items()
                      if v and now - v[-1] < self.window_s}
        self._until = {k: v for k, v in self._until.items() if v > now}


_GATE_LIMITER = _RateLimiter(_GATE_MAX_FAILURES, _GATE_WINDOW_S, _GATE_LOCKOUT_S)


def _cookie_value(header: str) -> str:
    for part in (header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == _COOKIE_NAME:
            return value
    return ""


def _parse_query(path: str) -> dict:
    _, _, query = path.partition("?")
    return {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}



def _build_stamp() -> str:
    """A short token that changes whenever the shell changes.

    Caching layers (the service worker, the browser's own cache) key off the
    URL, so appending this to styles.css/app.js makes a redeploy fetch the new
    files immediately instead of serving the previously cached shell.
    """
    h = hashlib.sha1()
    for name in ("index.html", "styles.css", "app.js", "i18n.js", "panels.js",
                 "advisor.js", "tracker.js", "alerts.js", "vol.js", "akoan.js",
                 "engine.js", "ticker.js", "sw.js",
                 "terminal.html", "terminal.css", "terminal.js"):
        try:
            with open(os.path.join(HERE, name), "rb") as fh:
                h.update(fh.read())
        except OSError:
            pass
    return h.hexdigest()[:10]


_BUILD = _build_stamp()
_SHELL_ASSET_RE = re.compile(
    rb'(href|src)="(styles\.css|app\.js|i18n\.js|panels\.js|advisor\.js|'
    rb'tracker\.js|alerts\.js|vol\.js|akoan\.js|engine\.js|ticker\.js|'
    rb'terminal\.css|terminal\.js)')
_STARTED_AT = time.time()


def _commit_stamp() -> str:
    """The deployed commit, from whichever env var the host sets, or 'unknown'."""
    for key in ("ASTRA_COMMIT", "RENDER_GIT_COMMIT", "GITHUB_SHA"):
        value = os.environ.get(key, "").strip()
        if value:
            return value[:12]
    return "unknown"


def _product_version() -> str:
    """The product version, read once from the repo-root VERSION file.

    The per-artifact tags (android, desktop, api-v1.0) each cover one surface;
    this is the single tag for the product as a whole, so 'what version is
    live?' has one answer. Falls back to 'unknown' if the file is absent, so a
    partial checkout still serves.
    """
    try:
        with open(os.path.join(ROOT, "VERSION"), "r", encoding="utf-8") as fh:
            return fh.read().strip() or "unknown"
    except OSError:
        return "unknown"


def _stamp_shell(body: bytes) -> bytes:
    return _SHELL_ASSET_RE.sub(
        lambda m: b'%s="%s?v=%s"' % (m.group(1), m.group(2), _BUILD.encode()),
        body,
    )


# ── volatility reading ───────────────────────────────────────────────────
#
# The phone has no way to reach the bot, and the bot's calibrated model is the
# one measured edge the project has. This route re-scores the *same* artifact
# with the *same* code the bot uses, so the number on the phone cannot drift
# from the number the bot reports. Best-effort and cached: a slow or empty
# bridge yields null, never a fabricated reading.

_VOL_TIMEFRAMES = ("M5", "M15", "H1", "H4", "D1")
_VOL_CACHE_TTL = 20.0


class _TimedCache:
    """A single cached body with the timestamp it was produced at.

    A typed slot rather than a bare dict: the three routes below each hold a
    `(at, body)` pair, and a dict of mixed float/bytes/None makes the
    arithmetic and the None check impossible to reason about statically.
    """

    __slots__ = ("at", "body")

    def __init__(self) -> None:
        self.at: float = 0.0
        self.body: bytes | None = None

    def fresh(self, now: float, ttl: float) -> bytes | None:
        """The cached body if it is younger than `ttl`, else None."""
        if self.body is not None and now - self.at < ttl:
            return self.body
        return None


_vol_cache = _TimedCache()
_vol_lock = threading.Lock()


def _load_volatility():
    """The bot's volatility module, imported from its real location, or None.

    Two deployment shapes: the repo keeps it at <root>/bridge/telegram_bot,
    while the container puts it at /app/resources/bridge/telegram_bot.
    """
    for cand in (
        os.environ.get("ASTRA_VOLATILITY_DIR", ""),
        os.path.join(ROOT, "bridge", "telegram_bot"),
        os.path.join(ROOT, "resources", "bridge", "telegram_bot"),
    ):
        if cand and os.path.isfile(os.path.join(cand, "volatility.py")):
            if cand not in sys.path:
                sys.path.insert(0, cand)
            break
    try:
        import volatility
        return volatility
    except ImportError:
        return None


def _tier(probability: float, threshold: float) -> str:
    if probability >= threshold:
        return "high"
    return "elevated" if probability >= 0.4 else "calm"


def _compute_volatility(bridge_base: str, timeout: float = 5.0) -> dict:
    """The current volatility reading, or a null body when it cannot be scored."""
    mod = _load_volatility()
    if mod is None:
        return {"available": False, "reason": "model unavailable"}
    try:
        model = mod.VolatilityModel.load()
    except (OSError, ValueError, KeyError):
        return {"available": False, "reason": "model unavailable"}

    bars_by_tf = {}
    for tf in _VOL_TIMEFRAMES:
        url = "%s/v1/candles?tf=%s&limit=220" % (bridge_base, tf)
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError):
            continue
        # The bridge wraps candles in an envelope ({"payload": {"bars": [...]}});
        # accept a bare body too so the route is not coupled to the envelope.
        if isinstance(payload.get("payload"), dict):
            payload = payload["payload"]
        bars = payload.get("bars") or payload.get("candles") or []
        if bars:
            bars_by_tf[tf] = bars

    if not bars_by_tf:
        return {"available": False, "reason": "market data unavailable"}
    probability = model.predict(bars_by_tf)
    if probability is None:
        return {"available": False, "reason": "not enough closed bars"}
    return {
        "available": True,
        "probability": round(probability, 4),
        "threshold": model.threshold,
        "tier": _tier(probability, model.threshold),
        "timeframes": sorted(bars_by_tf),
    }


def volatility_body(bridge_base: str, now: float | None = None) -> bytes:
    """Cached JSON body for /vol.json."""
    now = time.time() if now is None else now
    with _vol_lock:
        cached = _vol_cache.fresh(now, _VOL_CACHE_TTL)
        if cached is not None:
            return cached
    body = json.dumps(_compute_volatility(bridge_base),
                      ensure_ascii=False).encode("utf-8")
    with _vol_lock:
        _vol_cache.at, _vol_cache.body = now, body
    return body


# ── bot liveness ─────────────────────────────────────────────────────────
#
# The interactive Telegram poller is a long-running process on this same host,
# and until now nothing observed whether it was alive: the hourly watchdog only
# watched the GitHub scanner. The bot writes a heartbeat file every minute; this
# route exposes its age, so a dead poller is visible instead of silent.

_BOT_STALE_AFTER = 180.0  # three missed 60s beats


def _heartbeat_path() -> str:
    env = os.environ.get("ASTRA_BOT_HEARTBEAT", "").strip()
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), ".astra", "bot_heartbeat.json")


def bot_heartbeat_body(now: float | None = None) -> bytes:
    """Freshness of the bot's heartbeat, as JSON."""
    now = time.time() if now is None else now
    try:
        with open(_heartbeat_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        at = float(data.get("at", 0))
    except (OSError, ValueError, TypeError):
        return json.dumps({"available": False, "reason": "no heartbeat"},
                          ensure_ascii=False).encode("utf-8")
    age = max(0.0, now - at)
    if age > _BOT_STALE_AFTER:
        return json.dumps({"available": False, "reason": "stale",
                           "age_s": int(age), "pid": data.get("pid")},
                          ensure_ascii=False).encode("utf-8")
    return json.dumps({"available": True, "age_s": int(age),
                       "pid": data.get("pid")},
                      ensure_ascii=False).encode("utf-8")


# ── akoan reading ────────────────────────────────────────────────────────
#
# The phone can reach neither the trade ledger nor the calendar feed. This route
# runs the akoan engine on the server (over the committed corpus and the local
# event ledger, both of which the server can read) and returns the *compact*
# result — verdict, base rate, and one bucket per dimension. Same engine as the
# CLI, so the panel cannot disagree with the report.
#
# It is the slowest route (the corpus replay takes a few seconds), so it is
# cached for minutes and computed behind a lock: one run serves every phone.

_AKOAN_CACHE_TTL = 600.0
_akoan_cache = _TimedCache()
_akoan_lock = threading.Lock()
_akoan_compute_lock = threading.Lock()


def _load_akoan():
    """The akoan package, imported from its real location, or None."""
    for cand in (os.environ.get("ASTRA_AKOAN_DIR", ""), os.path.join(ROOT, "akoan")):
        if cand and os.path.isfile(os.path.join(cand, "engine.py")):
            if cand not in sys.path:
                sys.path.insert(0, cand)
            break
    try:
        import study
        return study
    except ImportError:
        return None


def _compute_akoan() -> dict:
    """The compact akoan result, or an 'available: false' body."""
    mod = _load_akoan()
    if mod is None:
        return {"available": False, "reason": "engine unavailable"}
    try:
        result = mod.study()
    except Exception as exc:  # noqa: BLE001 - a bad corpus must not take the app down
        return {"available": False, "reason": "study failed: %s" % exc}
    compact = _akoan_compact(result)
    compact["available"] = True
    return compact


def _akoan_compact(result: dict) -> dict:
    """The result trimmed for a phone screen: no per-bucket prose, no raw rows."""
    dims = []
    for dim in result.get("dimensions", []):
        buckets = [{
            "value": b.get("value"),
            "n": b.get("n"),
            "win_rate": b.get("win_rate"),
            "edge_vs_base": b.get("edge_vs_base"),
            "net_r": b.get("net_r"),
            "significant": bool(b.get("significant")),
            "insufficient": b.get("win_rate") is None,
        } for b in dim.get("buckets", [])]
        dims.append({"name": dim.get("name"),
                     "comparable": bool(dim.get("comparable")),
                     "buckets": buckets})
    cov = result.get("coverage") or {}
    return {
        "engine": "akoan",
        "verdict": result.get("verdict"),
        "scored": result.get("scored"),
        "wins": result.get("wins"),
        "losses": result.get("losses"),
        "base_rate": result.get("base_rate"),
        "overall_win_rate": result.get("overall_win_rate"),
        "net_r": result.get("net_r"),
        "survivors": result.get("survivors"),
        "min_n": result.get("min_n"),
        "fdr_q": result.get("fdr_q"),
        "dimensions": dims,
        "coverage": {"events": cov.get("events", 0),
                     "released": cov.get("released", 0),
                     "span_days": cov.get("span_days", 0)},
        "bars_m15": result.get("corpus_bars_m15"),
        "news_coverage_pct": result.get("news_coverage_pct"),
    }


def akoan_body(now: float | None = None) -> bytes:
    """Cached JSON body for /akoan.json."""
    now = time.time() if now is None else now
    with _akoan_lock:
        cached = _akoan_cache.fresh(now, _AKOAN_CACHE_TTL)
        if cached is not None:
            return cached
    # One compute at a time: the replay is CPU-heavy and every phone polls.
    with _akoan_compute_lock:
        with _akoan_lock:
            cached = _akoan_cache.fresh(now, _AKOAN_CACHE_TTL)
            if cached is not None:
                return cached
        body = json.dumps(_compute_akoan(), ensure_ascii=False).encode("utf-8")
        with _akoan_lock:
            _akoan_cache.at, _akoan_cache.body = now, body
    return body


# ── news reading ─────────────────────────────────────────────────────────
#
# The phone cannot reach the calendar or the RSS feeds. This route runs the
# news engine on the server (which can) and returns the digest: the blackout
# window, the upcoming schedule, the just-printed releases with their gold
# read, and the day's headlines. Cached for minutes — the feeds change slowly
# and one run serves every phone.

_NEWS_CACHE_TTL = 180.0
_news_cache = _TimedCache()
_news_lock = threading.Lock()


def _load_news_engine():
    """The news engine, imported from its real location, or None."""
    for cand in (os.environ.get("ASTRA_NEWS_DIR", ""), os.path.join(ROOT, "advisor")):
        if cand and os.path.isfile(os.path.join(cand, "news_engine.py")):
            if cand not in sys.path:
                sys.path.insert(0, cand)
            break
    try:
        import news_engine
        return news_engine
    except ImportError:
        return None


def _compute_news() -> dict:
    mod = _load_news_engine()
    if mod is None:
        return {"engine": "news", "available": False,
                "reason": "news engine unavailable"}
    try:
        return mod.digest()
    except Exception as exc:  # noqa: BLE001 - a feed failure must not 500
        return {"engine": "news", "available": False,
                "reason": "news engine error", "detail": str(exc)}


def news_body(now: float | None = None) -> bytes:
    """Cached JSON body for /news.json."""
    now = time.time() if now is None else now
    with _news_lock:
        cached = _news_cache.fresh(now, _NEWS_CACHE_TTL)
        if cached is not None:
            return cached
    body = json.dumps(_compute_news(), ensure_ascii=False).encode("utf-8")
    with _news_lock:
        _news_cache.at, _news_cache.body = now, body
    return body


_FORECAST_CACHE_TTL = 180.0
_forecast_cache = _TimedCache()
_forecast_lock = threading.Lock()


def _load_news_forecast():
    """The pre-news forecast module, imported from its real location, or None."""
    for cand in (os.environ.get("ASTRA_NEWS_DIR", ""), os.path.join(ROOT, "advisor")):
        if cand and os.path.isfile(os.path.join(cand, "news_forecast.py")):
            if cand not in sys.path:
                sys.path.insert(0, cand)
            break
    try:
        import news_forecast
        return news_forecast
    except ImportError:
        return None


def _compute_forecast() -> dict:
    mod = _load_news_forecast()
    if mod is None:
        return {"engine": "news-forecast", "available": False,
                "reason": "news forecast unavailable"}
    try:
        d = mod.forecast()
        d["available"] = True
        d["text"] = mod.format_forecast(d)
        return d
    except Exception as exc:  # noqa: BLE001 - a feed failure must not 500
        return {"engine": "news-forecast", "available": False,
                "reason": "news forecast error", "detail": str(exc)}


def forecast_body(now: float | None = None) -> bytes:
    """Cached JSON body for /forecast.json."""
    now = time.time() if now is None else now
    with _forecast_lock:
        cached = _forecast_cache.fresh(now, _FORECAST_CACHE_TTL)
        if cached is not None:
            return cached
    body = json.dumps(_compute_forecast(), ensure_ascii=False).encode("utf-8")
    with _forecast_lock:
        _forecast_cache.at, _forecast_cache.body = now, body
    return body


def warm_akoan_cache() -> None:
    """Populate the akoan cache once, in the background, at startup.

    The study replays the committed corpus and takes ~100 s cold; without this
    the first phone to open the News tab waits that long on a blank panel. The
    cache TTL is 10 minutes, so warming at boot covers the window in which the
    service is first opened. Best-effort: a failure must not stop the server.
    """
    try:
        akoan_body()
    except Exception as exc:  # noqa: BLE001 - pragma: no cover - defensive
        print("[warn] akoan warm-up failed: %s" % exc, file=sys.stderr)



class Handler(BaseHTTPRequestHandler):
    server_version = "AstraMobile/1.0"

    # Injected by main().
    api_base = "http://127.0.0.1:8790/api/v1"
    bridge_base = "http://127.0.0.1:8791"
    advisor_base = "http://127.0.0.1:8792"
    tracker_base = "http://127.0.0.1:8793"
    engine_base = "http://127.0.0.1:8794"
    apk_path = ""

    def log_message(self, fmt, *args):  # quieter logs
        sys.stderr.write("[mobile] %s\n" % (fmt % args))

    # -- routing ------------------------------------------------------------

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/v1/health":
            # Liveness must never need a credential: Render, Fly and the
            # keepalive cron all poll it, and a 401 would read as "down".
            self._proxy(self.api_base + path[len("/api/v1"):], self.path)
            return
        if path == "/astra.apk":
            # The installable app is not a secret: this same APK is published
            # on the public rolling release. Gating it 401s the in-app download
            # button (and the phone browser behind it) exactly when the gate is
            # on, which reads as "the download is broken".
            self._apk()
            return
        denied = self._gate_check()
        if denied is not None:
            self._unauthorized(denied)
            return
        if path == "/unlock":
            self._unlock()
        elif path == "/upgrade.json":
            self._upgrade_json()
        elif path == "/version.json":
            self._version_json()
        elif path == "/bot.json":
            self._bot_json()
        elif path == "/push.json":
            self._push_json()
        elif path == "/vol.json":
            self._volatility()
        elif path == "/akoan.json":
            self._akoan()
        elif path == "/news.json":
            self._news()
        elif path == "/forecast.json":
            self._forecast()
        elif path.startswith("/api/v1/"):
            self._proxy(self.api_base + path[len("/api/v1"):], self.path)
        elif path.startswith("/bridge/"):
            self._proxy(self.bridge_base + path[len("/bridge"):], self.path)
        elif path.startswith("/advisor/"):
            self._proxy(self.advisor_base + path[len("/advisor"):], self.path)
        elif path.startswith("/tracker/"):
            self._proxy(self.tracker_base + path[len("/tracker"):], self.path)
        elif path.startswith("/engine/"):
            self._proxy(self.engine_base + path[len("/engine"):], self.path,
                        timeout=_ENGINE_PROXY_TIMEOUT)
        else:
            self._static(path)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        denied = self._gate_check()
        if denied is not None:
            self._unauthorized(denied)
            return
        if path == "/push/subscribe":
            self._push_subscribe()
        elif path == "/push/unsubscribe":
            self._push_unsubscribe()
        elif path == "/push/test":
            self._push_test()
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def _read_json_body(self) -> dict | None:
        """A small JSON body, or None when it is missing, huge or malformed.

        Bounded because the body arrives from the network: a length cap keeps a
        stranger from making the server buffer an unbounded payload.
        """
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            return None
        if length <= 0 or length > 8192:
            return None
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None

    # -- access control -----------------------------------------------------

    def _client_key(self) -> str:
        """The address a failed guess is attributed to.

        X-Forwarded-For is read **only** when the operator says a trusted proxy
        sets it (`ASTRA_TRUST_FORWARDED_FOR=1`), and then only the **rightmost**
        hop. XFF is client-supplied and trivially spoofed: trusting the leftmost
        value lets an attacker rotate one header per request and never trip the
        gate, which is exactly the brute-force oracle the limiter exists to
        close. With no trusted proxy the socket peer address is the truth, and
        a spoofed header is ignored.

        The rightmost hop, not the leftmost, because a well-behaved proxy
        *appends* the address it saw; everything to its left is whatever the
        client sent. The leftmost value is the most spoofable, not the least.
        """
        if _TRUST_FORWARDED_FOR:
            fwd = self.headers.get("X-Forwarded-For", "")
            if fwd:
                return fwd.split(",")[-1].strip()
        return self.client_address[0] if self.client_address else "unknown"

    def _presented_key(self) -> str:
        """A credential the caller actually supplied, or "" if none.

        Distinguishes a wrong *guess* from a bare unauthenticated visit: only
        the former is a brute-force signal worth counting. A page load with no
        key at all is not an attempt, so it must not consume the budget.
        """
        cookie = _cookie_value(self.headers.get("Cookie", ""))
        if cookie:
            return cookie
        return _parse_query(self.path).get("k", "")

    def _gate_check(self) -> int | None:
        """Decide access, counting wrong guesses against the caller's address.

        Returns the status to refuse with (401, or 429 once the limit is
        crossed), or None to allow. A correct key — cookie or ?k= — always
        passes and clears the record, so the operator is never locked out. Only
        a *presented* wrong key is counted; a request carrying no key is a plain
        unauthenticated visit and is answered 401 without spending the budget.
        """
        if not _auth_required():
            return None
        now = time.time()
        key = self._client_key()
        if _GATE_LIMITER.blocked(key, now):
            return 429
        if self._authorized():
            _GATE_LIMITER.succeed(key)
            return None
        if self._presented_key():
            _GATE_LIMITER.fail(key, now)
            return 429 if _GATE_LIMITER.blocked(key, now) else 401
        return 401

    def _authorized(self) -> bool:
        """A valid cookie, or the token on the URL (for the first visit)."""
        if not _auth_required():
            return True
        given = _cookie_value(self.headers.get("Cookie", ""))
        if not _token_matches(given):
            given = _parse_query(self.path).get("k", "")
        return _token_matches(given)

    def _unauthorized(self, status: int = 401) -> None:
        # Shown to a phone that opened the app without the key. Lead with the
        # /unlock form (it stores the cookie, so it is typed once); the bare
        # ?k= form works too but is forgotten on every visit. Styled for a
        # phone: 401s are reached from the app, not from a desktop. A rate
        # limited caller gets the same page as 429, with a note, so it still
        # reads as a lock rather than a raw error.
        retry_after = 0
        note = ""
        if status == 429:
            retry_after = _GATE_LIMITER.retry_after(self._client_key(), time.time())
            note = ("<p>محاولات كثيرة خاطئة. أعد المحاولة بعد قليل.</p>")
        body = (
            "<!doctype html><html dir=\"rtl\" lang=\"ar\"><head>"
            "<meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>ASTRA</title><style>"
            "body{margin:0;min-height:100vh;display:flex;align-items:center;"
            "justify-content:center;background:#0B0E13;color:#E8ECF2;"
            "font:16px/1.6 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}"
            "main{max-width:22rem;padding:2rem;text-align:center}"
            "h1{margin:0 0 1rem;color:#C9A24B;letter-spacing:.12em}"
            "p{margin:0 0 1.25rem;color:#A9B4C4}"
            "input{width:100%;box-sizing:border-box;padding:.8rem;margin:0 0 .75rem;"
            "border:1px solid #2A323F;border-radius:.6rem;background:#141922;"
            "color:#E8ECF2;font-size:1rem}"
            "button{width:100%;padding:.85rem;border:0;border-radius:.6rem;"
            "background:#C9A24B;color:#0B0E13;font-size:1rem;font-weight:700}"
            "</style></head><body><main>"
            "<h1>ASTRA</h1>"
            "<p>هذا التطبيق محمي. أدخل مفتاح الوصول مرة واحدة وسيُحفظ على جهازك.</p>"
            + note +
            "<form method=\"get\" action=\"/unlock\">"
            "<input name=\"k\" type=\"password\" dir=\"ltr\" autocomplete=\"off\" "
            "placeholder=\"مفتاح الوصول\" aria-label=\"مفتاح الوصول\">"
            "<button type=\"submit\">دخول</button></form>"
            "</main></body></html>"
        ).encode("utf-8")
        self._send(status, body, "text/html; charset=utf-8", retry_after=retry_after)

    def _unlock(self) -> None:
        """Exchange ?k= for a cookie, then redirect to a clean URL.

        This keeps the key out of the address bar, history and Referer after
        the first visit — the same reason a login page posts a form instead of
        keeping credentials in the URL.
        """
        if not _auth_required():
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return
        if not _token_matches(_parse_query(self.path).get("k", "")):
            self._unauthorized()
            return
        self.send_response(302)
        self.send_header("Location", "/")
        secure = "; Secure" if _forwarded_proto_https(self) else ""
        self.send_header("Set-Cookie",
                         f"{_COOKIE_NAME}={ACCESS_TOKEN}; Path=/; Max-Age=31536000; "
                         f"HttpOnly; SameSite=Lax{secure}")
        self.end_headers()

    # -- static shell -------------------------------------------------------

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        # A request path is attacker-controlled: it can carry "..", a NUL, or a
        # name like "serve_mobile.py". Two independent guards, because either
        # alone has a hole:
        #   1. realpath containment, not a string prefix — "mobile" is a prefix
        #      of "mobile-secret", so startswith() would serve a sibling dir;
        #   2. an extension allowlist, so even a file that *is* inside the
        #      shell directory (the server's own .py, its .pyc, a stray .md)
        #      is never handed out.
        target = os.path.realpath(os.path.join(HERE, rel))
        root = os.path.realpath(HERE)
        if not target.startswith(root + os.sep) or not os.path.isfile(target):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        ext = os.path.splitext(target)[1].lower()
        if ext not in _SERVE_EXTS:
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        with open(target, "rb") as fh:
            body = fh.read()
        if ext in (".html", ".htm"):
            body = _stamp_shell(body)
        ctype = _CONTENT_TYPES.get(ext, "application/octet-stream")
        self._send(200, body, ctype)

    # -- upgrade link -------------------------------------------------------

    def _upgrade_json(self) -> None:
        """Expose the configured upgrade URL to the pricing page.

        The URL is not a secret, so serving it as JSON keeps the page static
        while letting one env var (ASTRA_UPGRADE_URL) drive the buy button.
        """
        url = os.environ.get("ASTRA_UPGRADE_URL", "").strip()
        body = json.dumps({"url": url}).encode("utf-8")
        self._send(200, body, "application/json; charset=utf-8")

    # -- deployed version ---------------------------------------------------

    def _version_json(self) -> None:
        """What is actually running, so 'is my fix live?' is one curl.

        The shell stamp changes when the served assets change; the commit comes
        from the host's env (Render sets RENDER_GIT_COMMIT). Both are cheap, so
        this is served fresh every time — it is the one reading that must never
        be cached.
        """
        body = json.dumps({
            "product": _product_version(),
            "shell": _BUILD,
            "commit": _commit_stamp(),
            "started_at": int(_STARTED_AT),
            "uptime_s": int(time.time() - _STARTED_AT),
        }, sort_keys=True).encode("utf-8")
        self._send(200, body, "application/json; charset=utf-8")

    # -- bot liveness -------------------------------------------------------

    def _bot_json(self) -> None:
        """Whether the interactive Telegram poller is alive right now."""
        self._send(200, bot_heartbeat_body(), "application/json; charset=utf-8")

    # -- web push -----------------------------------------------------------

    def _push_json(self) -> None:
        """Whether Web Push is on, and the VAPID key the phone subscribes with.

        `available: false` is the normal state on a host with no keys: the
        phone then keeps using its in-app alerts instead of erroring.
        """
        self._send(200, webpush.body(), "application/json; charset=utf-8")

    def _push_subscribe(self) -> None:
        sub = self._read_json_body()
        if not sub or not webpush.valid_subscription(sub):
            self._send(400, b'{"error":"invalid subscription"}',
                       "application/json; charset=utf-8")
            return
        try:
            webpush.subscribe(sub)
        except OSError:
            self._send(503, b'{"error":"could not store subscription"}',
                       "application/json; charset=utf-8")
            return
        self._send(200, b'{"ok":true}', "application/json; charset=utf-8")

    def _push_unsubscribe(self) -> None:
        body = self._read_json_body()
        endpoint = (body or {}).get("endpoint", "")
        if not isinstance(endpoint, str) or not endpoint:
            self._send(400, b'{"error":"invalid endpoint"}',
                       "application/json; charset=utf-8")
            return
        try:
            webpush.unsubscribe(endpoint)
        except OSError:
            self._send(503, b'{"error":"could not store subscription"}',
                       "application/json; charset=utf-8")
            return
        self._send(200, b'{"ok":true}', "application/json; charset=utf-8")

    def _push_test(self) -> None:
        """Send one deliberate test notification to the registered devices.

        So the operator can prove the background path works without waiting for
        a real market signal. It reports how many devices the push service
        accepted, so "0 sent" is a diagnosable answer, not a silent no-op.
        """
        try:
            result = webpush.test_broadcast()
        except OSError:
            self._send(503, b'{"error":"could not read subscriptions"}',
                       "application/json; charset=utf-8")
            return
        payload = json.dumps({
            "ok": bool(result.get("sent")),
            "sent": result.get("sent", 0),
            "subscribers": len(webpush.load_subscriptions()),
            "available": result.get("configured", False),
        }, ensure_ascii=False).encode("utf-8")
        self._send(200, payload, "application/json; charset=utf-8")

    def _volatility(self) -> None:
        """Re-score the bot's calibrated model for the phone.

        Same artifact, same code as the bot, so the number agrees by
        construction. Null body when it cannot be scored.
        """
        body = volatility_body(self.bridge_base)
        self._send(200, body, "application/json; charset=utf-8")

    # -- akoan reading ------------------------------------------------------

    def _akoan(self) -> None:
        """The akoan result, computed by the same engine the CLI runs.

        Cached for minutes; the replay is heavy, so one run serves every phone.
        """
        body = akoan_body()
        self._send(200, body, "application/json; charset=utf-8")

    # -- news reading -------------------------------------------------------

    def _news(self) -> None:
        """The live news digest, computed by the same engine the CLI runs."""
        body = news_body()
        self._send(200, body, "application/json; charset=utf-8")

    def _forecast(self) -> None:
        """The pre-news prep plan, computed by the same engine the bot runs."""
        body = forecast_body()
        self._send(200, body, "application/json; charset=utf-8")

    # -- APK download -------------------------------------------------------

    def _apk(self) -> None:
        if not self.apk_path or not os.path.isfile(self.apk_path):
            # No local copy staged. Point at the rolling release rather than
            # 404, so a link to /astra.apk always downloads something.
            self.send_response(302)
            self.send_header("Location", APK_FALLBACK_URL)
            self.end_headers()
            return
        with open(self.apk_path, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type",
                         "application/vnd.android.package-archive")
        self.send_header("Content-Disposition",
                         'attachment; filename="astra.apk"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # -- reverse proxy ------------------------------------------------------

    def _proxy(self, url: str, original_path: str, timeout: float | None = None) -> None:
        query = original_path.split("?", 1)[1] if "?" in original_path else ""
        full = url + ("?" + query if query else "")
        req = urllib.request.Request(full, method="GET")
        try:
            with urllib.request.urlopen(
                    req, timeout=_PROXY_TIMEOUT if timeout is None else timeout) as resp:
                body = resp.read()
                ctype = resp.headers.get("Content-Type",
                                         "application/json; charset=utf-8")
                self._send(resp.status, body, ctype)
        except urllib.error.HTTPError as exc:
            body = exc.read() if exc.fp else b"upstream error"
            self._send(exc.code, body,
                       exc.headers.get("Content-Type", "application/json"))
        except (urllib.error.URLError, OSError) as exc:
            self._send(503, b'{"error":"backend unreachable"}',
                       "application/json; charset=utf-8")
            self.log_message("proxy %s -> %s", full, exc)

    # -- writer -------------------------------------------------------------

    def _send(self, status: int, body: bytes, ctype: str,
              retry_after: int = 0) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        # Defense in depth: the shell is a standalone app, never an iframe, and
        # the access key can appear in the URL on a first visit, so it must not
        # leak to any other origin through the Referer header.
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if retry_after:
            # RFC 6585: tells a rate-limited caller how long to wait, so a
            # well-behaved client backs off instead of hammering the gate.
            self.send_header("Retry-After", str(retry_after))
        if ctype.startswith("application/json"):
            self.send_header("Cache-Control", "no-store")
        else:
            # Without this the browser heuristically caches the shell, so a
            # redeploy is not seen until the cache expires. The shell is small,
            # so revalidating on every load is cheap.
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


def main(argv=None) -> int:
    global ACCESS_TOKEN
    parser = argparse.ArgumentParser(description="ASTRA mobile PWA server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=12000)
    parser.add_argument("--api-port", type=int, default=8790)
    parser.add_argument("--bridge-port", type=int, default=8791)
    parser.add_argument("--advisor-port", type=int, default=8792)
    parser.add_argument("--tracker-port", type=int, default=8793)
    parser.add_argument("--engine-port", type=int, default=8794)
    parser.add_argument("--apk", default=os.path.join(
        os.path.dirname(HERE), "android", "dist", "astra.apk"),
        help="APK to offer at /astra.apk (optional)")
    parser.add_argument("--access-token", default=ACCESS_TOKEN,
                        help="require this key on every route except "
                             "/api/v1/health and /unlock (env ASTRA_ACCESS_TOKEN; "
                             "empty = open, the historical behaviour)")
    args = parser.parse_args(argv)

    ACCESS_TOKEN = (args.access_token or "").strip()

    Handler.api_base = f"http://127.0.0.1:{args.api_port}/api/v1"
    Handler.bridge_base = f"http://127.0.0.1:{args.bridge_port}"
    Handler.advisor_base = f"http://127.0.0.1:{args.advisor_port}"
    Handler.tracker_base = f"http://127.0.0.1:{args.tracker_port}"
    Handler.engine_base = f"http://127.0.0.1:{args.engine_port}"
    Handler.apk_path = os.path.abspath(args.apk)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f"[OK] ASTRA mobile app on http://{args.host}:{args.port} "
          f"(api -> {Handler.api_base})", flush=True)
    print("[OK] access control: "
          + ("on (key required)" if ACCESS_TOKEN else "off (open)"), flush=True)
    # Warm the heavy akoan study in the background so the first phone that
    # opens the News tab reads a cached result instead of waiting ~100 s.
    threading.Thread(target=warm_akoan_cache, name="akoan-warmup",
                     daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
