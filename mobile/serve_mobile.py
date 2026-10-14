#!/usr/bin/env python3
"""ASTRA mobile launcher: serve the PWA and reverse-proxy the backend API.

The phone can only reach the machine's public port, while the backend binds to
loopback. This tiny standard-library server bridges the two:

    GET /                -> mobile/index.html (the app shell)
    GET /api/v1/<...>    -> http://127.0.0.1:<api-port>/api/v1/<...>
    GET /bridge/<...>    -> http://127.0.0.1:<bridge-port>/<...>

It is read-only by construction: only GET/HEAD are proxied, and the upstream is
fixed to loopback, so the app cannot be used to reach anything else.

Run: python3 mobile/serve_mobile.py --port 12000
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Where /astra.apk points when no local build is staged: the rolling release
# the Android workflow republishes on every mobile/android change.
APK_FALLBACK_URL = (
    "https://github.com/msy898931-boop/asp/releases/download/android/astra.apk"
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

_PROXY_TIMEOUT = 6.0


def _build_stamp() -> str:
    """A short token that changes whenever the shell changes.

    Caching layers (the service worker, the browser's own cache) key off the
    URL, so appending this to styles.css/app.js makes a redeploy fetch the new
    files immediately instead of serving the previously cached shell.
    """
    h = hashlib.sha1()
    for name in ("index.html", "styles.css", "app.js", "sw.js"):
        try:
            with open(os.path.join(HERE, name), "rb") as fh:
                h.update(fh.read())
        except OSError:
            pass
    return h.hexdigest()[:10]


_BUILD = _build_stamp()
_SHELL_ASSET_RE = re.compile(rb'(href|src)="(styles\.css|app\.js)"')
_STARTED_AT = time.time()


def _commit_stamp() -> str:
    """The deployed commit, from whichever env var the host sets, or 'unknown'."""
    for key in ("ASTRA_COMMIT", "RENDER_GIT_COMMIT", "GITHUB_SHA"):
        value = os.environ.get(key, "").strip()
        if value:
            return value[:12]
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
_vol_cache = {"at": 0.0, "body": None}
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
        if _vol_cache["body"] is not None and now - _vol_cache["at"] < _VOL_CACHE_TTL:
            return _vol_cache["body"]
    body = json.dumps(_compute_volatility(bridge_base),
                      ensure_ascii=False).encode("utf-8")
    with _vol_lock:
        _vol_cache["at"], _vol_cache["body"] = now, body
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
_akoan_cache = {"at": 0.0, "body": None}
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
    except Exception as exc:  # a bad corpus must not take the app down
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
        if _akoan_cache["body"] is not None and now - _akoan_cache["at"] < _AKOAN_CACHE_TTL:
            return _akoan_cache["body"]
    # One compute at a time: the replay is CPU-heavy and every phone polls.
    with _akoan_compute_lock:
        with _akoan_lock:
            if _akoan_cache["body"] is not None and now - _akoan_cache["at"] < _AKOAN_CACHE_TTL:
                return _akoan_cache["body"]
        body = json.dumps(_compute_akoan(), ensure_ascii=False).encode("utf-8")
        with _akoan_lock:
            _akoan_cache["at"], _akoan_cache["body"] = now, body
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
    except Exception as exc:  # pragma: no cover - defensive
        print("[warn] akoan warm-up failed: %s" % exc, file=sys.stderr)



class Handler(BaseHTTPRequestHandler):
    server_version = "AstraMobile/1.0"

    # Injected by main().
    api_base = "http://127.0.0.1:8790/api/v1"
    bridge_base = "http://127.0.0.1:8791"
    advisor_base = "http://127.0.0.1:8792"
    tracker_base = "http://127.0.0.1:8793"
    apk_path = ""

    def log_message(self, fmt, *args):  # quieter logs
        sys.stderr.write("[mobile] %s\n" % (fmt % args))

    # -- routing ------------------------------------------------------------

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/astra.apk":
            self._apk()
        elif path == "/upgrade.json":
            self._upgrade_json()
        elif path == "/version.json":
            self._version_json()
        elif path == "/bot.json":
            self._bot_json()
        elif path == "/vol.json":
            self._volatility()
        elif path == "/akoan.json":
            self._akoan()
        elif path.startswith("/api/v1/"):
            self._proxy(self.api_base + path[len("/api/v1"):], self.path)
        elif path.startswith("/bridge/"):
            self._proxy(self.bridge_base + path[len("/bridge"):], self.path)
        elif path.startswith("/advisor/"):
            self._proxy(self.advisor_base + path[len("/advisor"):], self.path)
        elif path.startswith("/tracker/"):
            self._proxy(self.tracker_base + path[len("/tracker"):], self.path)
        else:
            self._static(path)

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    # -- static shell -------------------------------------------------------

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = os.path.normpath(os.path.join(HERE, rel))
        if not target.startswith(HERE) or not os.path.isfile(target):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        ext = os.path.splitext(target)[1].lower()
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

    # -- volatility reading -------------------------------------------------

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

    def _proxy(self, url: str, original_path: str) -> None:
        query = original_path.split("?", 1)[1] if "?" in original_path else ""
        full = url + ("?" + query if query else "")
        req = urllib.request.Request(full, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=_PROXY_TIMEOUT) as resp:
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

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
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
    parser = argparse.ArgumentParser(description="ASTRA mobile PWA server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=12000)
    parser.add_argument("--api-port", type=int, default=8790)
    parser.add_argument("--bridge-port", type=int, default=8791)
    parser.add_argument("--advisor-port", type=int, default=8792)
    parser.add_argument("--tracker-port", type=int, default=8793)
    parser.add_argument("--apk", default=os.path.join(
        os.path.dirname(HERE), "android", "dist", "astra.apk"),
        help="APK to offer at /astra.apk (optional)")
    args = parser.parse_args(argv)

    Handler.api_base = f"http://127.0.0.1:{args.api_port}/api/v1"
    Handler.bridge_base = f"http://127.0.0.1:{args.bridge_port}"
    Handler.advisor_base = f"http://127.0.0.1:{args.advisor_port}"
    Handler.tracker_base = f"http://127.0.0.1:{args.tracker_port}"
    Handler.apk_path = os.path.abspath(args.apk)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f"[OK] ASTRA mobile app on http://{args.host}:{args.port} "
          f"(api -> {Handler.api_base})", flush=True)
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
