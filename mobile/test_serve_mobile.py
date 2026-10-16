#!/usr/bin/env python3
"""Tests for the mobile server's volatility route.

The phone re-scores the bot's real artifact with the bot's real code, so these
tests run that path for real against a stub bridge — no mocks of the model.
"""

from __future__ import annotations

import json
import os
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

    def do_GET(self):  # noqa: N802
        body = json.dumps({"payload": {"bars": _bars()}}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _BareStubBridge(_StubBridge):
    """A bare body, to prove the route is not coupled to the envelope."""
    def do_GET(self):  # noqa: N802
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
        sm._vol_cache["at"], sm._vol_cache["body"] = 0.0, None

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
        js = self._read("app.js")
        self.assertIn("renderJournal", js)
        self.assertIn("bindJournalFilter()", js)
        for key in ("jrn_today", "jrn_all", "jrn_trades", "jrn_record",
                    "jrn_net_r", "jrn_net_usd", "jrn_open"):
            self.assertIn(key, js, "missing journal string %s" % key)

    def test_open_r_uses_live_price(self):
        # Guard the sign convention: a LONG above entry is positive R.
        js = self._read("app.js")
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
        sm._vol_cache["at"], sm._vol_cache["body"] = 0.0, None

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
    """Nine tabs do not fit one row; the bar is a two-row grid that shows them all."""

    def _read(self, name):
        with open(os.path.join(sm.HERE, name), encoding="utf-8") as fh:
            return fh.read()

    def test_css_is_a_two_row_grid_with_arabic_spacing(self):
        css = self._read("styles.css")
        self.assertIn("grid-template-columns: repeat(5, 1fr)", css)
        self.assertIn("display: grid", css)
        # The overflow affordance stays for a wide screen that still overflows.
        self.assertIn(".tabs.more-left", css)
        self.assertIn(".tabs.more-right", css)
        self.assertIn("mask-image", css)
        # Arabic joins break under Latin letter-spacing.
        self.assertIn('html[dir="rtl"] .tab', css)

    def test_js_keeps_the_overflow_affordance(self):
        js = self._read("app.js")
        self.assertIn("function updateTabFades", js)
        self.assertIn("more-left", js)
        self.assertIn("more-right", js)
        # Fades must be recomputed when direction or viewport changes.
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
        js = self._read("app.js")
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
        js = self._read("app.js")
        self.assertIn("loadAkoan", js)
        self.assertIn("renderAkoan", js)
        self.assertIn("akoan.json", js)
        for key in ("tab_akoan", "akoan_verdict_NO_EDGE_MEASURED",
                    "akoan_base", "akoan_cov", "akoan_not_comparable",
                    "akoan_footer"):
            self.assertIn(key, js, "missing akoan string %s" % key)

    def test_panel_states_its_disclaimer(self):
        js = self._read("app.js")
        self.assertIn("not financial advice", js)

    def test_styles_exist(self):
        css = self._read("styles.css")
        for cls in (".akoan-verdict", ".akoan-row", ".akoan-base", ".akoan-dim"):
            self.assertIn(cls, css)


class AkoanRouteTest(unittest.TestCase):
    def setUp(self):
        sm._akoan_cache["at"], sm._akoan_cache["body"] = 0.0, None

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
        sm._akoan_cache["at"], sm._akoan_cache["body"] = 0.0, None

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
        with open(os.path.join(sm.HERE, "app.js"), encoding="utf-8") as fh:
            return fh.read()

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

    def tearDown(self):
        sm.ACCESS_TOKEN = self._saved

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
                     "/astra.apk", "/upgrade.json", "/bot.json"):
            with self.subTest(path=path):
                status, _ = self._status(path)
                self.assertEqual(status, 401, path)

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

    def test_unlock_rejects_a_wrong_key(self):
        sm.ACCESS_TOKEN = "s3cret"
        status, _ = self._status("/unlock?k=nope")
        self.assertEqual(status, 401)

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


if __name__ == "__main__":
    unittest.main()
