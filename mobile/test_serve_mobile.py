#!/usr/bin/env python3
"""Tests for the mobile server's volatility route.

The phone re-scores the bot's real artifact with the bot's real code, so these
tests run that path for real against a stub bridge — no mocks of the model.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import serve_mobile as sm  # noqa: E402

# The client is split across ordered classic scripts that share one global
# scope. Tests that assert on client source read them all, in load order, as
# one string, so a symbol's file may change without breaking the assertion.
CLIENT_SCRIPTS = ("app.js", "i18n.js", "panels.js", "advisor.js",
                  "tracker.js", "alerts.js", "vol.js", "akoan.js", "ticker.js")


def client_js() -> str:
    parts = []
    for name in CLIENT_SCRIPTS:
        with open(os.path.join(sm.HERE, name), encoding="utf-8") as fh:
            parts.append(fh.read())
    return "\n".join(parts)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface the 302 instead of following it, so the cookie can be read."""
    def redirect_request(self, *a, **k):
        return None


def _bars(n=220, base=1800.0):
    bars = []
    for i in range(n):
        close = base + i * 0.1
        bars.append({"time": 1_800_000_000 + i * 300, "open": close - 0.05,
                     "high": close + 0.4, "low": close - 0.4, "close": close})
    return bars


class _StubBridge(BaseHTTPRequestHandler):
    """Mirrors the real bridge: candles live under payload.bars."""
    def log_message(self, *a):  # silence
        pass

    def do_GET(self):
        body = json.dumps({"payload": {"bars": _bars()}}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _BareStubBridge(_StubBridge):
    """A bare body, to prove the route is not coupled to the envelope."""
    def do_GET(self):
        body = json.dumps({"bars": _bars()}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _serve(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _stop(server):
    server.shutdown()
    server.server_close()


class TierTest(unittest.TestCase):
    def test_tier_boundaries(self):
        self.assertEqual(sm._tier(0.70, 0.65), "high")
        self.assertEqual(sm._tier(0.65, 0.65), "high")
        self.assertEqual(sm._tier(0.50, 0.65), "elevated")
        self.assertEqual(sm._tier(0.40, 0.65), "elevated")
        self.assertEqual(sm._tier(0.30, 0.65), "calm")


class LoadTest(unittest.TestCase):
    def test_model_module_is_found(self):
        self.assertIsNotNone(sm._load_volatility())

    def test_found_in_the_container_layout_too(self):
        import shutil
        import tempfile
        root = tempfile.mkdtemp()
        try:
            dest = os.path.join(root, "resources", "bridge", "telegram_bot")
            os.makedirs(dest)
            src = os.path.join(os.path.dirname(sm.HERE), "bridge", "telegram_bot")
            shutil.copy(os.path.join(src, "volatility.py"), dest)
            original = sm.ROOT
            sm.ROOT = root
            try:
                self.assertIsNotNone(sm._load_volatility())
            finally:
                sm.ROOT = original
        finally:
            shutil.rmtree(root, ignore_errors=True)


class ComputeTest(unittest.TestCase):
    def setUp(self):
        sm._vol_cache.at, sm._vol_cache.body = 0.0, None

    def test_unreachable_bridge_yields_a_null_body(self):
        body = sm._compute_volatility("http://127.0.0.1:9")  # nothing listens
        self.assertFalse(body["available"])
        self.assertIn("reason", body)

    def test_real_model_scores_a_live_shape(self):
        bridge = _serve(_StubBridge)
        try:
            base = "http://127.0.0.1:%d" % bridge.server_address[1]
            body = sm._compute_volatility(base)
        finally:
            _stop(bridge)
        self.assertTrue(body["available"])
        self.assertGreaterEqual(body["probability"], 0.0)
        self.assertLessEqual(body["probability"], 1.0)
        self.assertEqual(body["timeframes"], ["D1", "H1", "H4", "M15", "M5"])
        self.assertIn(body["tier"], ("calm", "elevated", "high"))

    def test_bare_body_also_scores(self):
        bridge = _serve(_BareStubBridge)
        try:
            base = "http://127.0.0.1:%d" % bridge.server_address[1]
            body = sm._compute_volatility(base)
        finally:
            _stop(bridge)
        self.assertTrue(body["available"])

    def test_body_is_cached_within_the_ttl(self):
        calls = {"n": 0}
        original = sm._compute_volatility

        def counting(base, timeout=5.0):
            calls["n"] += 1
            return {"available": False, "reason": "stub"}

        sm._compute_volatility = counting
        try:
            first = sm.volatility_body("http://x", now=1000.0)
            second = sm.volatility_body("http://x", now=1000.0 + sm._VOL_CACHE_TTL - 1)
        finally:
            sm._compute_volatility = original
        self.assertEqual(calls["n"], 1)
        self.assertEqual(first, second)

    def test_cache_expires_after_the_ttl(self):
        calls = {"n": 0}

        def counting(base, timeout=5.0):
            calls["n"] += 1
            return {"available": False, "reason": "stub"}

        original = sm._compute_volatility
        sm._compute_volatility = counting
        try:
            sm.volatility_body("http://x", now=2000.0)
            sm.volatility_body("http://x", now=2000.0 + sm._VOL_CACHE_TTL + 1)
        finally:
            sm._compute_volatility = original
        self.assertEqual(calls["n"], 2)


class JournalTest(unittest.TestCase):
    """The journal tab must be wired end to end, or it ships blank."""

    def _read(self, name):
        with open(os.path.join(sm.HERE, name), encoding="utf-8") as fh:
            return fh.read()

    def test_panel_has_strip_and_filter(self):
        html = self._read("index.html")
        for anchor in ('id="jrn-today"', 'id="jrn-filter"', 'data-scope="today"',
                       'data-scope="all"'):
            self.assertIn(anchor, html)

    def test_app_wires_the_journal(self):
        js = client_js()
        self.assertIn("renderJournal", js)
        self.assertIn("bindJournalFilter()", js)
        for key in ("jrn_today", "jrn_all", "jrn_trades", "jrn_record",
                    "jrn_net_r", "jrn_net_usd", "jrn_open"):
            self.assertIn(key, js, "missing journal string %s" % key)

    def test_open_r_uses_live_price(self):
        # Guard the sign convention: a LONG above entry is positive R.
        js = client_js()
        self.assertIn("(lastPrice - r.entry) * dir", js)


class PreviewTest(unittest.TestCase):
    """A shared link must render a card, not a bare URL."""

    def test_index_carries_open_graph_tags(self):
        with open(os.path.join(sm.HERE, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        for prop in ("og:title", "og:description", "og:image", "og:url"):
            self.assertIn('property="%s"' % prop, html)
        # og:image must be absolute — relative paths are ignored by chat apps.
        self.assertRegex(html, r'og:image"\s+content="https://')

    def test_og_image_is_a_real_asset(self):
        self.assertTrue(
            os.path.isfile(os.path.join(sm.HERE, "icons", "icon-512.png")))


class RouteTest(unittest.TestCase):
    def setUp(self):
        sm._vol_cache.at, sm._vol_cache.body = 0.0, None

    def test_vol_json_route_returns_the_reading(self):
        bridge = _serve(_StubBridge)
        sm.Handler.bridge_base = "http://127.0.0.1:%d" % bridge.server_address[1]
        app = _serve(sm.Handler)
        try:
            url = "http://127.0.0.1:%d/vol.json" % app.server_address[1]
            with urllib.request.urlopen(url, timeout=10) as resp:
                self.assertEqual(resp.status, 200)
                body = json.loads(resp.read().decode("utf-8"))
        finally:
            _stop(app)
            _stop(bridge)
        self.assertTrue(body["available"])
        self.assertIn("probability", body)


class VersionRouteTest(unittest.TestCase):
    """The version route is how 'is my fix live?' is answered with one curl."""

    def _get(self, path):
        app = _serve(sm.Handler)
        try:
            url = "http://127.0.0.1:%d%s" % (app.server_address[1], path)
            with urllib.request.urlopen(url, timeout=10) as resp:
                return resp.status, dict(resp.headers), json.loads(resp.read())
        finally:
            _stop(app)

    def test_version_route_reports_the_running_build(self):
        status, headers, body = self._get("/version.json")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertIn("shell", body)
        self.assertIn("commit", body)
        self.assertGreaterEqual(body["uptime_s"], 0)

    def test_commit_comes_from_the_host_env_when_present(self):
        old = os.environ.get("RENDER_GIT_COMMIT")
        os.environ["RENDER_GIT_COMMIT"] = "abcdef1234567890"
        try:
            self.assertEqual(sm._commit_stamp(), "abcdef123456")
        finally:
            if old is None:
                os.environ.pop("RENDER_GIT_COMMIT", None)
            else:
                os.environ["RENDER_GIT_COMMIT"] = old

    def test_commit_is_unknown_when_no_env_is_set(self):
        saved = {k: os.environ.pop(k, None)
                 for k in ("ASTRA_COMMIT", "RENDER_GIT_COMMIT", "GITHUB_SHA")}
        try:
            self.assertEqual(sm._commit_stamp(), "unknown")
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


class BotHeartbeatTest(unittest.TestCase):
    """A dead interactive poller must be visible, not silent."""

    def _write(self, payload, tmp):
        path = os.path.join(tmp, "bot_heartbeat.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        return path

    def test_fresh_heartbeat_is_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write({"pid": 42, "at": 1000.0}, tmp)
            old = os.environ.get("ASTRA_BOT_HEARTBEAT")
            os.environ["ASTRA_BOT_HEARTBEAT"] = path
            try:
                body = json.loads(sm.bot_heartbeat_body(now=1010.0))
            finally:
                if old is None:
                    os.environ.pop("ASTRA_BOT_HEARTBEAT", None)
                else:
                    os.environ["ASTRA_BOT_HEARTBEAT"] = old
            self.assertTrue(body["available"])
            self.assertEqual(body["pid"], 42)

    def test_stale_heartbeat_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write({"pid": 42, "at": 1000.0}, tmp)
            old = os.environ.get("ASTRA_BOT_HEARTBEAT")
            os.environ["ASTRA_BOT_HEARTBEAT"] = path
            try:
                body = json.loads(sm.bot_heartbeat_body(now=1000.0 + sm._BOT_STALE_AFTER + 1))
            finally:
                if old is None:
                    os.environ.pop("ASTRA_BOT_HEARTBEAT", None)
                else:
                    os.environ["ASTRA_BOT_HEARTBEAT"] = old
            self.assertFalse(body["available"])
            self.assertEqual(body["reason"], "stale")

    def test_missing_heartbeat_is_unavailable_not_an_error(self):
        old = os.environ.get("ASTRA_BOT_HEARTBEAT")
        os.environ["ASTRA_BOT_HEARTBEAT"] = "/nonexistent/bot_heartbeat.json"
        try:
            body = json.loads(sm.bot_heartbeat_body(now=1000.0))
        finally:
            if old is None:
                os.environ.pop("ASTRA_BOT_HEARTBEAT", None)
            else:
                os.environ["ASTRA_BOT_HEARTBEAT"] = old
        self.assertFalse(body["available"])
        self.assertEqual(body["reason"], "no heartbeat")

    def test_route_serves_the_heartbeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write({"pid": 7, "at": time.time()}, tmp)
            old = os.environ.get("ASTRA_BOT_HEARTBEAT")
            os.environ["ASTRA_BOT_HEARTBEAT"] = path
            app = _serve(sm.Handler)
            try:
                url = "http://127.0.0.1:%d/bot.json" % app.server_address[1]
                with urllib.request.urlopen(url, timeout=10) as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
                    body = json.loads(resp.read())
            finally:
                _stop(app)
                if old is None:
                    os.environ.pop("ASTRA_BOT_HEARTBEAT", None)
                else:
                    os.environ["ASTRA_BOT_HEARTBEAT"] = old
            self.assertTrue(body["available"])


class TabBarTest(unittest.TestCase):
    """Nine views: five primary tabs in one row plus a More sheet for the rest."""

    def _read(self, name):
        with open(os.path.join(sm.HERE, name), encoding="utf-8") as fh:
            return fh.read()

    def test_html_splits_primary_from_secondary(self):
        html = self._read("index.html")
        # Five primary views plus the More button share the fixed bar.
        nav = html[html.index('class="tabs"'):html.index("</nav>")]
        self.assertEqual(nav.count('class="tab"'), 5)
        self.assertIn('class="tab tab-more"', nav)
        self.assertIn('aria-controls="tabs-more"', nav)
        # The secondary row holds the other four views and starts hidden.
        sheet = html[html.index('id="tabs-more"'):html.index("</div>", html.index('id="tabs-more"'))]
        self.assertEqual(sheet.count('class="tab"'), 4)
        for view in ("tracker", "ai", "conn", "bridge"):
            self.assertIn('data-view="%s"' % view, sheet)
        self.assertIn("hidden", html[html.index('class="tabs-more" id="tabs-more"'):html.index(">", html.index('id="tabs-more"'))])

    def test_css_is_one_row_plus_a_sheet(self):
        css = self._read("styles.css")
        self.assertIn("grid-template-columns: repeat(6, 1fr)", css)
        self.assertIn("display: grid", css)
        # The More sheet anchors above the fixed-height bar, four across.
        self.assertIn(".tabs-more:not([hidden])", css)
        self.assertIn("grid-template-columns: repeat(4, 1fr)", css)
        # The overflow affordance stays for a wide screen that still overflows.
        self.assertIn(".tabs.more-left", css)
        self.assertIn(".tabs.more-right", css)
        self.assertIn("mask-image", css)
        # Arabic joins break under Latin letter-spacing.
        self.assertIn('html[dir="rtl"] .tab', css)

    def test_js_toggles_more_and_keeps_the_overflow_affordance(self):
        js = client_js()
        self.assertIn("function setMoreOpen", js)
        self.assertIn("function selectView", js)
        self.assertIn("SECONDARY_VIEWS", js)
        self.assertIn('aria-expanded', js)
        # Tapping outside the sheet closes it.
        self.assertIn('e.target.closest("#tabs-more")', js)
        # The fades must be recomputed when direction or viewport changes.
        self.assertIn("function updateTabFades", js)
        self.assertIn("more-left", js)
        self.assertIn("more-right", js)
        self.assertIn('addEventListener("resize", updateTabFades)', js)
        self.assertIn("orientationchange", js)


class PanelLoadingTest(unittest.TestCase):
    """A slow read must look like work, not a frozen panel."""

    def _read(self, name):
        with open(os.path.join(sm.HERE, name), encoding="utf-8") as fh:
            return fh.read()

    def test_css_defines_the_loading_shimmer(self):
        css = self._read("styles.css")
        self.assertIn(".panel.loading", css)
        self.assertIn("shimmer", css)
        # Reduced motion must not leave a frozen bright band.
        self.assertIn("prefers-reduced-motion", css)

    def test_js_toggles_loading_on_the_slow_panels(self):
        js = client_js()
        self.assertIn("function setPanelLoading", js)
        for view in ("akoan", "chart", "vol", "ai", "tracker"):
            self.assertIn('setPanelLoading("%s"' % view, js,
                          "no loading indicator wired for %s" % view)


class AkoanPanelTest(unittest.TestCase):
    """The akoan tab must be wired end to end, or it ships blank."""

    def _read(self, name):
        with open(os.path.join(sm.HERE, name), encoding="utf-8") as fh:
            return fh.read()

    def test_panel_exists_and_is_named(self):
        html = self._read("index.html")
        self.assertIn('id="view-akoan"', html)
        self.assertIn('data-view="akoan"', html)
        for anchor in ('id="akoan-verdict"', 'id="akoan-dims"', 'id="akoan-base"',
                       'id="akoan-cov"'):
            self.assertIn(anchor, html)

    def test_app_wires_the_engine_route(self):
        js = client_js()
        self.assertIn("loadAkoan", js)
        self.assertIn("renderAkoan", js)
        self.assertIn("akoan.json", js)
        for key in ("tab_akoan", "akoan_verdict_NO_EDGE_MEASURED",
                    "akoan_base", "akoan_cov", "akoan_not_comparable",
                    "akoan_footer"):
            self.assertIn(key, js, "missing akoan string %s" % key)

    def test_panel_states_its_disclaimer(self):
        js = client_js()
        self.assertIn("not financial advice", js)

    def test_styles_exist(self):
        css = self._read("styles.css")
        for cls in (".akoan-verdict", ".akoan-row", ".akoan-base", ".akoan-dim"):
            self.assertIn(cls, css)


class AkoanRouteTest(unittest.TestCase):
    def setUp(self):
        sm._akoan_cache.at, sm._akoan_cache.body = 0.0, None

    def test_compact_shape_is_phone_sized(self):
        result = {
            "verdict": "NO_EDGE_MEASURED", "scored": 353, "wins": 123,
            "losses": 230, "net_r": -33.44, "base_rate": 0.348, "survivors": 0,
            "min_n": 8, "fdr_q": 0.10,
            "dimensions": [{"name": "blackout", "comparable": False,
                            "buckets": [{"value": "quiet", "n": 353,
                                         "win_rate": 0.348, "edge_vs_base": 0.0,
                                         "net_r": -33.4, "significant": False}]}],
            "coverage": {"events": 1659, "released": 1332, "span_days": 119},
        }
        body = sm._akoan_compact(result)
        self.assertEqual(body["engine"], "akoan")
        self.assertEqual(body["verdict"], "NO_EDGE_MEASURED")
        self.assertEqual(body["coverage"]["span_days"], 119)
        bucket = body["dimensions"][0]["buckets"][0]
        self.assertFalse(bucket["insufficient"])
        self.assertEqual(bucket["win_rate"], 0.348)

    def test_insufficient_bucket_is_flagged(self):
        result = {"dimensions": [{"name": "blackout", "comparable": True,
                                  "buckets": [{"value": "quiet", "n": 2,
                                               "win_rate": None}]}]}
        body = sm._akoan_compact(result)
        self.assertTrue(body["dimensions"][0]["buckets"][0]["insufficient"])

    def test_unavailable_engine_yields_a_null_body(self):
        original = sm._load_akoan
        sm._load_akoan = lambda: None
        try:
            body = sm._compute_akoan()
        finally:
            sm._load_akoan = original
        self.assertFalse(body["available"])
        self.assertIn("reason", body)

    def test_body_is_cached_within_the_ttl(self):
        calls = {"n": 0}
        original = sm._compute_akoan

        def counting():
            calls["n"] += 1
            return {"available": False, "reason": "stub"}

        sm._compute_akoan = counting
        try:
            first = sm.akoan_body(now=1000.0)
            second = sm.akoan_body(now=1000.0 + sm._AKOAN_CACHE_TTL - 1)
        finally:
            sm._compute_akoan = original
        self.assertEqual(calls["n"], 1)
        self.assertEqual(first, second)

    def test_cache_expires_after_the_ttl(self):
        calls = {"n": 0}

        def counting():
            calls["n"] += 1
            return {"available": False, "reason": "stub"}

        original = sm._compute_akoan
        sm._compute_akoan = counting
        try:
            sm.akoan_body(now=2000.0)
            sm.akoan_body(now=2000.0 + sm._AKOAN_CACHE_TTL + 1)
        finally:
            sm._compute_akoan = original
        self.assertEqual(calls["n"], 2)


class AkoanWarmupTest(unittest.TestCase):
    """The first phone to open the News tab must not wait ~100 s on a blank.

    The study is warmed once at startup so /akoan.json is already cached when
    the tab is first opened.
    """

    def setUp(self):
        sm._akoan_cache.at, sm._akoan_cache.body = 0.0, None

    def test_warmup_populates_the_cache(self):
        calls = {"n": 0}

        def counting():
            calls["n"] += 1
            return {"available": True, "verdict": "NO_EDGE_MEASURED"}

        original = sm._compute_akoan
        sm._compute_akoan = counting
        try:
            sm.warm_akoan_cache()
            self.assertEqual(calls["n"], 1)
            # A subsequent read is served from the cache, not recomputed.
            body = sm.akoan_body()
            self.assertEqual(calls["n"], 1)
            self.assertIn(b"NO_EDGE_MEASURED", body)
        finally:
            sm._compute_akoan = original

    def test_warmup_failure_does_not_raise(self):
        original = sm._compute_akoan

        def boom():
            raise RuntimeError("corpus missing")

        sm._compute_akoan = boom
        try:
            sm.warm_akoan_cache()  # must not raise
        finally:
            sm._compute_akoan = original

    def test_server_starts_a_warmup_thread(self):
        # The wiring, not just the helper: main() must launch the warm-up.
        with open(os.path.join(sm.HERE, "serve_mobile.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("warm_akoan_cache", src)
        self.assertIn("akoan-warmup", src)


class FetchTimeoutTest(unittest.TestCase):
    """No panel may hang forever; the app must bound every request."""

    def _js(self):
        return client_js()

    def test_requests_are_bounded(self):
        js = self._js()
        self.assertIn("FETCH_TIMEOUT_MS", js)
        self.assertIn("AbortController", js)
        # The heavy News read gets its own, larger budget.
        self.assertIn("AKOAN_TIMEOUT_MS", js)

    def test_akoan_shows_progress_when_slow(self):
        js = self._js()
        self.assertIn("akoan_slow", js)
        self.assertIn("setInterval", js)


class AccessControlTest(unittest.TestCase):
    """The gate is opt-in: unset means open, set means every route but health.

    Each test sets sm.ACCESS_TOKEN directly (the value the env/flag would
    produce) and restores it, so the suite never leaks state into another test.
    """

    def setUp(self):
        self._saved = sm.ACCESS_TOKEN
        sm.ACCESS_TOKEN = ""
        sm._GATE_LIMITER.reset()

    def tearDown(self):
        sm.ACCESS_TOKEN = self._saved
        sm._GATE_LIMITER.reset()

    def _serve_app(self):
        return _serve(sm.Handler)

    def _status(self, path, headers=None):
        app = self._serve_app()
        try:
            url = "http://127.0.0.1:%d%s" % (app.server_address[1], path)
            req = urllib.request.Request(url, headers=headers or {})
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.status, dict(resp.headers)
            except urllib.error.HTTPError as exc:
                return exc.code, dict(exc.headers)
        finally:
            _stop(app)

    def test_open_when_no_token_is_configured(self):
        status, _ = self._status("/version.json")
        self.assertEqual(status, 200)

    def test_every_route_needs_the_key_once_set(self):
        sm.ACCESS_TOKEN = "s3cret"
        for path in ("/", "/version.json", "/vol.json", "/akoan.json",
                     "/upgrade.json", "/bot.json"):
            with self.subTest(path=path):
                status, _ = self._status(path)
                self.assertEqual(status, 401, path)

    def test_the_apk_download_is_never_gated(self):
        # The in-app download button points at /astra.apk. Gating it 401s the
        # download exactly when the gate is on, which reads as "broken link".
        # The same APK is public on the rolling release, so it is not a secret.
        sm.ACCESS_TOKEN = "s3cret"
        status, _ = self._status("/astra.apk")
        self.assertNotEqual(status, 401)

    def test_wrong_key_is_rejected(self):
        sm.ACCESS_TOKEN = "s3cret"
        status, _ = self._status("/version.json?k=wrong")
        self.assertEqual(status, 401)

    def test_key_on_the_url_authorizes(self):
        sm.ACCESS_TOKEN = "s3cret"
        status, _ = self._status("/version.json?k=s3cret")
        self.assertEqual(status, 200)

    def test_cookie_authorizes_a_later_visit(self):
        sm.ACCESS_TOKEN = "s3cret"
        status, _ = self._status("/version.json",
                                 headers={"Cookie": "astra_key=s3cret"})
        self.assertEqual(status, 200)

    def test_unlock_sets_a_cookie_and_redirects_off_the_url(self):
        sm.ACCESS_TOKEN = "s3cret"
        opener = urllib.request.build_opener(_NoRedirect)
        app = self._serve_app()
        try:
            url = "http://127.0.0.1:%d/unlock?k=s3cret" % app.server_address[1]
            try:
                opener.open(url, timeout=10)
                self.fail("expected a 302 redirect")
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, 302)
                self.assertEqual(exc.headers.get("Location"), "/")
                self.assertIn("astra_key=s3cret", exc.headers.get("Set-Cookie", ""))
                self.assertIn("HttpOnly", exc.headers.get("Set-Cookie", ""))
        finally:
            _stop(app)

    def test_unlock_cookie_is_secure_behind_https_proxy(self):
        # Render/Fly terminate TLS and forward X-Forwarded-Proto: https. On such
        # a request the cookie must carry Secure so it is never sent in clear.
        sm.ACCESS_TOKEN = "s3cret"
        opener = urllib.request.build_opener(_NoRedirect)
        app = self._serve_app()
        try:
            url = "http://127.0.0.1:%d/unlock?k=s3cret" % app.server_address[1]
            req = urllib.request.Request(url,
                                         headers={"X-Forwarded-Proto": "https"})
            try:
                opener.open(req, timeout=10)
                self.fail("expected a 302 redirect")
            except urllib.error.HTTPError as exc:
                cookie = exc.headers.get("Set-Cookie", "")
                self.assertIn("astra_key=s3cret", cookie)
                self.assertIn("Secure", cookie)
        finally:
            _stop(app)

    def test_unlock_cookie_omits_secure_on_plain_http(self):
        # A local run has no proxy and no TLS: a Secure cookie would be dropped
        # by the browser and lock the operator out, so it must be absent.
        sm.ACCESS_TOKEN = "s3cret"
        opener = urllib.request.build_opener(_NoRedirect)
        app = self._serve_app()
        try:
            url = "http://127.0.0.1:%d/unlock?k=s3cret" % app.server_address[1]
            try:
                opener.open(url, timeout=10)
                self.fail("expected a 302 redirect")
            except urllib.error.HTTPError as exc:
                cookie = exc.headers.get("Set-Cookie", "")
                self.assertIn("astra_key=s3cret", cookie)
                self.assertNotIn("Secure", cookie)
        finally:
            _stop(app)

    def test_unlock_rejects_a_wrong_key(self):
        sm.ACCESS_TOKEN = "s3cret"
        status, _ = self._status("/unlock?k=nope")
        self.assertEqual(status, 401)

    def test_gate_page_offers_the_unlock_form(self):
        # The gate page is reached from the phone app, so it must lead with the
        # /unlock form that stores the cookie, styled for a phone.
        sm.ACCESS_TOKEN = "s3cret"
        app = self._serve_app()
        try:
            url = "http://127.0.0.1:%d/" % app.server_address[1]
            try:
                urllib.request.urlopen(url, timeout=10)
                body = b""
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, 401)
                body = exc.read()
            html = body.decode("utf-8")
            self.assertIn('action="/unlock"', html)
            self.assertIn('name="k"', html)
            self.assertIn('dir="rtl"', html)
            self.assertIn('width=device-width', html)
        finally:
            _stop(app)

    def test_health_is_never_gated(self):
        """Render, Fly and the keepalive cron poll health with no credential."""
        sm.ACCESS_TOKEN = "s3cret"
        saved = sm.Handler.api_base
        api = _serve(_StubBridge)
        sm.Handler.api_base = "http://127.0.0.1:%d/api/v1" % api.server_address[1]
        try:
            status, _ = self._status("/api/v1/health")
        finally:
            _stop(api)
            sm.Handler.api_base = saved
        self.assertEqual(status, 200)

    def test_unset_token_is_not_a_requirement(self):
        sm.ACCESS_TOKEN = ""
        self.assertFalse(sm._auth_required())
        sm.ACCESS_TOKEN = "x"
        self.assertTrue(sm._auth_required())


class RateLimiterUnitTest(unittest.TestCase):
    """The limiter on its own, with an injected clock, so the timing rules are
    checked without sleeping."""

    def test_blocks_after_max_failures_and_expires(self):
        rl = sm._RateLimiter(max_failures=3, window_s=100.0, lockout_s=50.0)
        self.assertFalse(rl.blocked("a", 0.0))
        for t in (0.0, 1.0, 2.0):
            rl.fail("a", t)
        self.assertTrue(rl.blocked("a", 2.0))
        self.assertEqual(rl.retry_after("a", 2.0), 50)
        # The cool-down ends, and the address is forgotten, not just unblocked.
        self.assertFalse(rl.blocked("a", 52.0))
        self.assertEqual(rl.retry_after("a", 52.0), 0)

    def test_success_clears_the_record(self):
        rl = sm._RateLimiter(max_failures=3, window_s=100.0, lockout_s=50.0)
        rl.fail("a", 0.0)
        rl.fail("a", 1.0)
        rl.succeed("a")
        rl.fail("a", 2.0)  # a fresh run of failures, not a continuation
        self.assertFalse(rl.blocked("a", 2.0))

    def test_old_failures_fall_out_of_the_window(self):
        rl = sm._RateLimiter(max_failures=3, window_s=100.0, lockout_s=50.0)
        rl.fail("a", 0.0)
        rl.fail("a", 1.0)
        rl.fail("a", 200.0)  # the first two are outside the window
        self.assertFalse(rl.blocked("a", 200.0))

    def test_addresses_are_independent(self):
        rl = sm._RateLimiter(max_failures=2, window_s=100.0, lockout_s=50.0)
        rl.fail("a", 0.0)
        rl.fail("a", 1.0)
        self.assertTrue(rl.blocked("a", 1.0))
        self.assertFalse(rl.blocked("b", 1.0))


class GateRateLimitTest(unittest.TestCase):
    """The gate must not be an unlimited guessing oracle. Repeated wrong keys
    from one address are rate limited, but a legitimate visitor — a correct
    key, or a plain page load with no key — is never locked out."""

    def setUp(self):
        self._saved = sm.ACCESS_TOKEN
        sm.ACCESS_TOKEN = "s3cret"
        sm._GATE_LIMITER.reset()
        self.app = _serve(sm.Handler)

    def tearDown(self):
        _stop(self.app)
        sm.ACCESS_TOKEN = self._saved
        sm._GATE_LIMITER.reset()

    def _status(self, path, headers=None):
        url = "http://127.0.0.1:%d%s" % (self.app.server_address[1], path)
        req = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers)

    def test_repeated_wrong_keys_are_rate_limited(self):
        n = sm._GATE_MAX_FAILURES
        codes = [self._status("/version.json?k=wrong%d" % i)[0]
                 for i in range(n + 2)]
        # The first n-1 wrong guesses are plain 401s; the guess that reaches
        # the limit is refused with 429, and every guess after stays refused.
        self.assertEqual(codes[:n - 1], [401] * (n - 1))
        self.assertEqual(codes[n - 1:], [429] * 3)

    def test_lockout_sends_retry_after(self):
        for i in range(sm._GATE_MAX_FAILURES):
            self._status("/version.json?k=wrong%d" % i)
        status, headers = self._status("/version.json?k=wrong-again")
        self.assertEqual(status, 429)
        self.assertGreater(int(headers.get("Retry-After", "0")), 0)

    def test_correct_key_clears_the_budget(self):
        # A handful of typos must not accumulate toward a lockout once the
        # right key arrives.
        for i in range(sm._GATE_MAX_FAILURES - 1):
            self._status("/version.json?k=wrong%d" % i)
        status, _ = self._status("/version.json?k=s3cret")
        self.assertEqual(status, 200)
        # The record is gone, so the next wrong key is a 401, not a 429.
        status, _ = self._status("/version.json?k=wrong")
        self.assertEqual(status, 401)

    def test_a_bare_visit_is_not_a_guess(self):
        # A phone loading the gate page carries no key. That must never spend
        # the budget, or a few refreshes would lock the real user out.
        for _ in range(sm._GATE_MAX_FAILURES * 2):
            status, _ = self._status("/")
            self.assertEqual(status, 401)
        # The budget is intact: a wrong key is still just a 401.
        status, _ = self._status("/version.json?k=wrong")
        self.assertEqual(status, 401)

    def test_forwarded_for_keeps_clients_apart(self):
        # Behind Render/Fly every request arrives from the proxy, so the client
        # address must come from X-Forwarded-For — otherwise one attacker would
        # lock out every user.
        attacker = {"X-Forwarded-For": "203.0.113.9"}
        for i in range(sm._GATE_MAX_FAILURES):
            self._status("/version.json?k=wrong%d" % i, headers=attacker)
        status, _ = self._status("/version.json?k=x", headers=attacker)
        self.assertEqual(status, 429)
        status, _ = self._status("/version.json?k=x",
                                 headers={"X-Forwarded-For": "198.51.100.4"})
        self.assertEqual(status, 401)


class RefreshGuardTest(unittest.TestCase):
    """The 15s poll must not stack on a slow backend, and must only fetch the
    heavy panels that are actually on screen — this is a phone on cell data."""

    def _read(self, name):
        with open(os.path.join(sm.HERE, name), encoding="utf-8") as fh:
            return fh.read()

    def test_poll_is_guarded_against_re_entry(self):
        js = client_js()
        self.assertIn("let refreshing = false", js)
        # The guard must wrap the live fetch and be cleared no matter what.
        self.assertIn("if (refreshing) return", js)
        self.assertIn("refreshing = true", js)
        self.assertIn("refreshing = false", js)

    def test_heavy_panels_follow_the_visible_tab(self):
        js = client_js()
        self.assertIn("let currentView", js)
        # Candle history only refreshes while the chart tab is open.
        self.assertIn('if (currentView === "chart") loadChart()', js)
        self.assertIn('if (currentView === "vol") loadVolatility()', js)
        # The ticker stays live everywhere; it is the price the user watches.
        self.assertIn("loadTicker()", js)

    def test_tab_switch_records_the_view(self):
        js = client_js()
        self.assertIn("currentView = view", js)


class BootPaintTest(unittest.TestCase):
    """Theme and reading direction must be set before first paint, so a
    light-mode phone or an Arabic reader sees no dark/LTR flash."""

    def _read(self, name):
        with open(os.path.join(sm.HERE, name), encoding="utf-8") as fh:
            return fh.read()

    def test_boot_script_precedes_the_stylesheet(self):
        html = self._read("index.html")
        script = html.index("astra-theme")
        css = html.index('href="styles.css"')
        self.assertLess(script, css, "the boot script must run before styles.css")

    def test_boot_sets_theme_and_direction(self):
        html = self._read("index.html")
        self.assertIn('setAttribute("data-theme"', html)
        self.assertIn(".dir = l ===", html)

    def test_numeric_readouts_are_isolated_in_rtl(self):
        """A signed number like −12.34R must not read as 12.34R− in Arabic."""
        css = self._read("styles.css")
        anchor = 'html[dir="rtl"] .readout dd'
        block = css[css.index(anchor):]
        block = block[:block.index("}")]
        for sel in (".readout dd", ".levels dd", ".bar b",
                    ".trk-stat-value", ".trk-r", ".conn td"):
            self.assertIn(sel, block, f"{sel} must stay LTR in RTL")
        self.assertIn("unicode-bidi: embed", block)


class ThemeContrastTest(unittest.TestCase):
    """Both themes must clear WCAG AA on the phone. The light palette is where
    the gold-on-beige pairs used to slip under 4.5:1."""

    def _lum(self, hexcolor):
        h = hexcolor.lstrip("#")
        rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        f = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
        return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]

    def _ratio(self, a, b):
        la, lb = self._lum(a), self._lum(b)
        hi, lo = max(la, lb), min(la, lb)
        return (hi + 0.05) / (lo + 0.05)

    def _read(self):
        with open(os.path.join(sm.HERE, "styles.css"), encoding="utf-8") as fh:
            return fh.read()

    def _var(self, css, name, scope=None):
        block = css
        if scope:
            block = css[css.index(scope):]
            block = block[:block.index("}")]
        for line in block.splitlines():
            line = line.strip()
            if line.startswith(name + ":"):
                return line.split(":", 1)[1].split(";")[0].strip()
        raise AssertionError(f"{name} not found")

    def test_light_gold_and_flat_clear_aa(self):
        css = self._read()
        scope = 'html[data-theme="light"] {'
        gold = self._var(css, "--gold", scope)
        flat = self._var(css, "--flat", scope)
        ink = self._var(css, "--ink", scope)
        ink2 = self._var(css, "--ink-2", scope)
        ink3 = self._var(css, "--ink-3", scope)
        # The tab bar, toggles and the APK link all draw --gold on these fields.
        for bg, label in ((ink, "--ink"), (ink2, "--ink-2"), (ink3, "--ink-3")):
            self.assertGreaterEqual(self._ratio(gold, bg), 4.5,
                                    f"light --gold on {label} must clear AA")
        self.assertGreaterEqual(self._ratio(flat, ink2), 4.5,
                                "light --flat (the change readout) must clear AA")

    def test_dark_flat_clears_aa(self):
        css = self._read()
        flat = self._var(css, "--flat")
        ink2 = self._var(css, "--ink-2")
        self.assertGreaterEqual(self._ratio(flat, ink2), 4.5,
                                "dark --flat must clear AA")


class PwaManifestTest(unittest.TestCase):
    """The install splash and status bar must match the chosen theme, so a
    light-mode phone does not flash a dark field on launch."""

    def _read(self, name):
        with open(os.path.join(sm.HERE, name), encoding="utf-8") as fh:
            return fh.read()

    def test_index_swaps_to_the_light_manifest(self):
        html = self._read("index.html")
        self.assertIn('href="manifest.webmanifest"', html)
        self.assertIn("manifest-light.webmanifest", html)
        # The swap must run before the stylesheet so the splash colour is right.
        self.assertLess(html.index("manifest-light.webmanifest"),
                        html.index('href="styles.css"'))

    def test_light_manifest_is_light(self):
        m = json.loads(self._read("manifest-light.webmanifest"))
        self.assertEqual(m["background_color"].upper(), "#F4F0E6")
        self.assertEqual(m["theme_color"].upper(), "#F4F0E6")

    def test_light_manifest_is_precached(self):
        sw = self._read("sw.js")
        self.assertIn("./manifest-light.webmanifest", sw)


class ServiceWorkerGateTest(unittest.TestCase):
    """The service worker must not defeat or leak the access gate."""

    def _read(self, name):
        with open(os.path.join(sm.HERE, name), encoding="utf-8") as fh:
            return fh.read()

    def test_unlock_is_never_cached(self):
        # /unlock?k=KEY puts the access key in the URL; caching the request
        # would persist the key in the device's cache storage.
        sw = self._read("sw.js")
        self.assertIn('url.pathname.endsWith("/unlock")', sw)

    def test_only_successful_responses_are_cached(self):
        # Caching a 401 would serve the gate page as the offline shell.
        sw = self._read("sw.js")
        self.assertIn("res && res.ok", sw)

    def test_the_apk_download_is_not_intercepted(self):
        # A cached APK would mask a fresh build and pollute the shell cache.
        sw = self._read("sw.js")
        self.assertIn('url.pathname.endsWith(".apk")', sw)

    def test_panel_readings_are_never_cached(self):
        # /vol.json and /akoan.json carry a current number, exactly like the
        # live endpoints above them. Caching one would serve a stale reading —
        # and because the volatility panel stamps the *render* time, not the
        # data time, the stale number would look freshly computed.
        sw = self._read("sw.js")
        self.assertIn('url.pathname.endsWith("/vol.json")', sw)
        self.assertIn('url.pathname.endsWith("/akoan.json")', sw)
        # The guard has to sit with the live guard, inside the fetch handler and
        # before its cache write (the install-time caches.open is elsewhere).
        fetch_handler = sw[sw.index('addEventListener("fetch"'):]
        guard = fetch_handler.index("const reading =")
        cache_write = fetch_handler.index("caches.open(SHELL)")
        self.assertLess(guard, cache_write,
                        "the reading guard must precede the cache write")


class AndroidShellTest(unittest.TestCase):
    """The APK is a WebView shell that opens the hosted app. Its baked-in target
    must be the live server, never loopback: 127.0.0.1 on a phone is the phone
    itself, so the app opened an empty error screen and looked broken."""

    def _read(self, *parts):
        root = os.path.dirname(sm.HERE)
        with open(os.path.join(root, *parts), encoding="utf-8") as fh:
            return fh.read()

    def _url(self, text):
        m = re.search(r'DEFAULT_URL\s*=\s*"([^"]+)"', text)
        return m.group(1) if m else None

    def test_source_and_build_default_to_the_live_server(self):
        java = self._read("android", "app", "src", "main", "java",
                          "ai", "astra", "mobile", "MainActivity.java")
        self.assertEqual(self._url(java), "https://astra-xlp9.onrender.com/")

    def test_build_script_does_not_bake_loopback(self):
        sh = self._read("android", "build.sh")
        m = re.search(r'^\s*SERVER_URL="([^"]*)"', sh, re.M)
        self.assertIsNotNone(m, "build.sh must set a default SERVER_URL")
        self.assertEqual(m.group(1), "https://astra-xlp9.onrender.com/")

    def test_shell_allows_network(self):
        man = self._read("android", "app", "src", "main", "AndroidManifest.xml")
        self.assertIn('android:name="android.permission.INTERNET"', man)

    def test_shell_accepts_the_access_cookie(self):
        # The server's gate sets an HttpOnly astra_key cookie on /unlock?k=.
        # Without accepting cookies the WebView answers 401 on every route and
        # the app looks broken whenever the gate is on.
        java = self._read("android", "app", "src", "main", "java",
                          "ai", "astra", "mobile", "MainActivity.java")
        self.assertIn("setAcceptCookie(true)", java)
        self.assertIn("CookieManager", java)

    def test_shell_can_enter_the_access_key(self):
        java = self._read("android", "app", "src", "main", "java",
                          "ai", "astra", "mobile", "MainActivity.java")
        self.assertIn("/unlock", java)
        self.assertIn("KEY_ACCESS", java)
        self.assertIn("promptForKey", java)

    def test_shell_treats_401_as_a_key_prompt_not_a_network_error(self):
        java = self._read("android", "app", "src", "main", "java",
                          "ai", "astra", "mobile", "MainActivity.java")
        self.assertIn("onReceivedHttpError", java)
        self.assertIn("getStatusCode() == 401", java)


class OfflineCacheTest(unittest.TestCase):
    """A phone with no network must open to the last reading, not a blank
    screen, and must say plainly that the reading is cached."""

    def _read(self, name):
        with open(os.path.join(sm.HERE, name), encoding="utf-8") as fh:
            return fh.read()

    def test_app_caches_and_restores_the_last_reading(self):
        js = client_js()
        self.assertIn("saveLastRead", js)
        self.assertIn("loadLastRead", js)
        self.assertIn("renderSnapshot", js)
        # The cache is versioned so a shape change cannot be misread as current.
        self.assertIn("CACHE_VERSION", js)

    def test_refresh_saves_on_success_and_restores_on_failure(self):
        js = client_js()
        # The success path must persist; the failure path must restore.
        self.assertIn("saveLastRead({ health, system, bridge: br, analysis", js)
        self.assertIn("renderSnapshot(cached.snap, true)", js)

    def test_boot_paints_the_cached_reading_before_the_live_poll(self):
        js = client_js()
        # In init(): restore first, then refresh. A live poll on a cold start
        # must not be the only thing standing between the user and a blank app.
        restore = js.index("if (cached) {\n    renderSnapshot(cached.snap, false)")
        live = js.index("refresh();\n  setInterval")
        self.assertLess(restore, live)

    def test_offline_is_labelled_not_faked(self):
        js = client_js()
        # The offline banner must exist in both languages, and the cached
        # reading must carry its age rather than looking live.
        self.assertIn("cached_offline", js)
        self.assertIn("agoText", js)
        self.assertIn("offlineMode", js)

    def test_online_and_offline_events_are_wired(self):
        js = client_js()
        self.assertIn('addEventListener("online"', js)
        self.assertIn('addEventListener("offline"', js)

    def test_service_worker_falls_back_to_the_shell_for_navigations(self):
        sw = self._read("sw.js")
        self.assertIn('mode === "navigate"', sw)
        self.assertIn('caches.match("./index.html")', sw)


class SkeletonTest(unittest.TestCase):
    """A cold start must show placeholders, not a wall of '--' that reads as a
    broken app. The placeholders must vanish once a reading paints, and must not
    animate for a reader who asked for reduced motion."""

    def _read(self, name):
        with open(os.path.join(sm.HERE, name), encoding="utf-8") as fh:
            return fh.read()

    def test_css_defines_skeleton_placeholders(self):
        css = self._read("styles.css")
        self.assertIn('html[data-skeleton="true"]', css)
        # It must cover the readout values, not just the panel background.
        anchor = css[css.index('html[data-skeleton="true"] .readout dd'):]
        self.assertIn("color: transparent", anchor)
        self.assertIn("shimmer", anchor)

    def test_reduced_motion_neutralises_the_skeleton(self):
        css = self._read("styles.css")
        block = css[css.index("@media (prefers-reduced-motion: reduce)"):]
        block = block[:block.index("\n}\n")]
        self.assertIn('html[data-skeleton="true"]', block)

    def test_app_sets_and_clears_the_skeleton(self):
        js = client_js()
        self.assertIn("setSkeleton", js)
        self.assertIn('d.setAttribute("data-skeleton"', js)
        self.assertIn('d.removeAttribute("data-skeleton"', js)
        # It must be cleared when a live reading lands and when a cached one
        # paints, or the values would stay hidden forever.
        self.assertIn("setSkeleton(false)", js)

    def test_skeleton_is_only_shown_when_there_is_no_cached_reading(self):
        js = client_js()
        # In init(): if a cached reading exists, paint it; only otherwise show
        # the skeleton. A cached start must not flash placeholders.
        idx = js.index("const cached = loadLastRead();\n  if (cached) {")
        window = js[idx:idx + 220]
        self.assertIn("renderSnapshot(cached.snap, false)", window)
        self.assertIn("setSkeleton(true)", window)


class NarrowHeaderTest(unittest.TestCase):
    """The header must fit a 320px phone without a sideways scroll.

    It packed a brand plus four controls into one row, which needs 422px: every
    common phone (320-390px) scrolled sideways and the refresh button sat half
    off-screen. The fix is a measured one, so the numbers in the stylesheet are
    part of the contract and are checked here rather than eyeballed.
    """

    def _read(self, name):
        with open(os.path.join(sm.HERE, name), encoding="utf-8") as fh:
            return fh.read()

    def test_css_shrinks_the_header_on_narrow_screens(self):
        css = self._read("styles.css")
        self.assertIn("@media (max-width: 419px)", css)
        self.assertIn("@media (max-width: 359px)", css)
        # The controls keep their 44px height (tap target); only the width and
        # the gaps step down.
        narrow = css[css.index("@media (max-width: 419px)"):]
        self.assertIn(".icon-btn { width: 40px; }", narrow)
        self.assertIn(".controls { gap: 6px; }", narrow)
        # The wordmark steps down at the narrowest step.
        self.assertIn("h1 { font-size: 19px; letter-spacing: .06em; }", narrow)

    def test_the_brand_yields_before_the_controls(self):
        css = self._read("styles.css")
        # A flex item refuses to shrink below its content without min-width: 0,
        # which is what pushed the row past the viewport in the first place.
        self.assertIn(".brand { min-width: 0; }", css)
        self.assertIn(".brand > div { min-width: 0; overflow: hidden; }", css)
        self.assertIn(".brand .sub { white-space: nowrap; overflow: hidden;"
                      " text-overflow: ellipsis; }", css)
        # The controls are the fixed side of the trade.
        self.assertIn(".controls { flex: none; }", css)

    def test_the_page_never_scrolls_sideways(self):
        css = self._read("styles.css")
        self.assertIn("html, body { overflow-x: hidden; }", css)

    def test_micro_labels_step_up_on_a_phone(self):
        css = self._read("styles.css")
        floor = css[css.index("@media (max-width: 480px)"):]
        # The 10px labels (and the bottom tab bar) move to 11px on a narrow
        # screen; the bottom bar is the one a thumb actually reads.
        self.assertIn(".tab", floor)
        self.assertIn("font-size: 11px", floor)
        for sel in (".readout dt", ".levels dt", ".bar span", ".foot"):
            self.assertIn(sel, floor)

    def test_footer_links_are_thumb_sized(self):
        css = self._read("styles.css")
        # The APK and the three page links were 33px tall text pills, below the
        # 44px a thumb needs. They are now flex boxes with a 44px floor.
        for sel in (".apk-link", ".plans-link"):
            block = css[css.index(sel + " {"):]
            block = block[:block.index("}")]
            self.assertIn("display: inline-flex", block)
            self.assertIn("min-height: 44px", block)


class SubPageOverflowTest(unittest.TestCase):
    """The standalone pages (pricing, performance) must also fit a 320px phone.

    Each has its own stylesheet, so the fix in styles.css never reached them.
    performance.html cited repository paths inside <code> — long unbroken tokens
    like research/PHASE0_VOLATILITY_DECISION.md — which no line-breaking rule
    could split, so the page rendered 8px wider than a 320px screen and
    scrolled sideways. The token is checked alongside the rule that tames it.
    """

    def _read(self, name):
        with open(os.path.join(sm.HERE, name), encoding="utf-8") as fh:
            return fh.read()

    def test_code_tokens_break_instead_of_widening_the_page(self):
        css = self._read("performance.css")
        block = css[css.index("code {"):]
        block = block[:block.index("}")]
        self.assertIn("overflow-wrap: anywhere", block)
        self.assertIn("word-break: break-word", block)

    def test_the_paths_that_need_breaking_are_still_cited(self):
        html = self._read("performance.html")
        # If these ever shrink to short tokens the rule above is still correct,
        # but this is the reason it exists — assert the trigger is present.
        self.assertIn("research/PHASE0_VOLATILITY_DECISION.md", html)

    def test_every_page_reserves_the_notch_and_gesture_insets(self):
        for name in ("index.html", "pricing.html", "performance.html",
                     "terminal.html"):
            self.assertIn("viewport-fit=cover", self._read(name), name)


class ClientScriptContractTest(unittest.TestCase):
    """The client is split across ordered classic scripts. Each one must be
    loaded by the page, precached by the worker and stamped by the server, or
    a deploy ships a shell that is half-old and half-new — a failure that only
    shows up on a phone, after a cache has formed."""

    def setUp(self):
        self.html = self._read("index.html")

    def _read(self, name):
        with open(os.path.join(sm.HERE, name), encoding="utf-8") as fh:
            return fh.read()

    def _loaded(self) -> list[str]:
        return re.findall(r'<script src="([^"]+\.js)"></script>', self.html)

    def test_page_loads_every_client_script_in_order(self):
        self.assertEqual(self._loaded(), list(CLIENT_SCRIPTS),
                         "index.html must load the client scripts in order")

    def test_app_js_still_leads(self):
        # app.js declares the shared state and the network helpers; every other
        # script reads them, so a reorder would break the shell at load.
        self.assertEqual(self._loaded()[0], "app.js")

    def test_worker_precaches_every_client_script(self):
        sw = self._read("sw.js")
        for name in CLIENT_SCRIPTS:
            self.assertIn('"./%s"' % name, sw,
                          "%s is not precached by sw.js" % name)

    def test_server_stamps_every_client_script(self):
        stamp_src = inspect.getsource(sm._build_stamp)
        for name in CLIENT_SCRIPTS:
            # The stamp regex must actually match the tag the page emits, or a
            # redeploy serves the old file from cache.
            self.assertIsNotNone(
                sm._SHELL_ASSET_RE.search(b'src="%s"' % name.encode()),
                "%s is not stamped into the shell URL" % name)
            self.assertIn('"%s"' % name, stamp_src,
                          "%s is not part of the build stamp" % name)

    def test_worker_cache_name_was_bumped(self):
        # A new asset list under the old cache name never installs.
        sw = self._read("sw.js")
        self.assertRegex(sw, r'const SHELL = "astra-shell-v\d+"')


if __name__ == "__main__":
    unittest.main()
