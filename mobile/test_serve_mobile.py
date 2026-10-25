#!/usr/bin/env python3
"""Tests for the mobile server's volatility route.

The phone re-scores the bot's real artifact with the bot's real code, so these
tests run that path for real against a stub bridge — no mocks of the model.
The gate, rate-limiter and account tests that used to live here now sit in
test_gate.py; the shared harness is in mobile_test_kit.py.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import serve_mobile as sm  # noqa: E402
from mobile_test_kit import (  # noqa: E402
    _BareStubBridge,
    _serve,
    _stop,
    _StubBridge,
    client_js,
)


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


class StaticFileSecurityTest(unittest.TestCase):
    """The shell is a fixed asset set; a request path must not escape it.

    Two real holes were closed: the server's own Python source was served over
    HTTP, and the containment check used a string prefix, so a sibling
    directory whose name starts with "mobile" was reachable with "..".
    """

    def setUp(self):
        sm.ACCESS_TOKEN = ""  # the shell is reachable without a key
        sm._GATE_LIMITER.reset()
        self.app = _serve(sm.Handler)
        self.port = self.app.server_address[1]

    def tearDown(self):
        _stop(self.app)

    def _get(self, raw_path):
        url = "http://127.0.0.1:%d%s" % (self.port, raw_path)
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_index_is_served(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"ASTRA", body)

    def test_a_real_asset_is_served(self):
        status, _ = self._get("/styles.css")
        self.assertEqual(status, 200)

    def test_security_headers_are_present(self):
        url = "http://127.0.0.1:%d/styles.css" % self.port
        with urllib.request.urlopen(url, timeout=10) as resp:
            headers = dict(resp.headers)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")

    def test_server_source_is_not_served(self):
        # The server's own source, and its compiled cache, must be a 404.
        for path in ("/serve_mobile.py", "/webpush.py",
                     "/__pycache__/serve_mobile.cpython-313.pyc"):
            status, _ = self._get(path)
            self.assertEqual(status, 404, "%s must not be served" % path)

    def test_non_asset_extensions_are_not_served(self):
        status, _ = self._get("/README.md")
        self.assertEqual(status, 404)

    def test_dot_dot_does_not_escape_the_shell(self):
        for path in ("/../VERSION", "/../README.md", "/../mobile/VERSION"):
            status, _ = self._get(path)
            self.assertEqual(status, 404, "%s must not be served" % path)

    def test_sibling_directory_with_a_shared_prefix_is_not_reachable(self):
        # "mobile" is a prefix of "mobile-secret": a naive startswith() check
        # would let this through. It must be a 404.
        import tempfile
        outside = tempfile.mkdtemp(prefix="mobile-secret-")
        try:
            with open(os.path.join(outside, "leak.txt"), "w") as fh:
                fh.write("TOPSECRET")
            rel = os.path.relpath(os.path.join(outside, "leak.txt"), sm.HERE)
            status, body = self._get("/" + rel)
            self.assertEqual(status, 404)
            self.assertNotIn(b"TOPSECRET", body)
        finally:
            import shutil
            shutil.rmtree(outside, ignore_errors=True)


class DosHardeningTest(unittest.TestCase):
    """A phone-app server that cannot be exhausted by idle connections.

    The service is the one process on the public internet, so an unauthenticated
    caller must not be able to pin it with sockets. Two guards are checked here:
    the per-connection read timeout (slowloris) and the concurrency ceiling.
    """

    def test_socket_timeout_is_bounded(self):
        self.assertGreater(sm._SOCKET_TIMEOUT_S, 0)
        self.assertLessEqual(sm._SOCKET_TIMEOUT_S, 120)

    def test_max_concurrency_is_sane(self):
        self.assertGreaterEqual(sm._MAX_CONCURRENCY, 1)

    def test_bounded_server_applies_timeout_and_cap(self):
        server = sm._BoundedServer(("127.0.0.1", 0), sm.Handler,
                                   timeout=2.5, max_concurrency=3)
        try:
            self.assertEqual(server.timeout, 2.5)
            # The semaphore starts full: exactly `max_concurrency` slots.
            for _ in range(3):
                self.assertTrue(server._slots.acquire(blocking=False))
            self.assertFalse(server._slots.acquire(blocking=False))
        finally:
            server.server_close()

    def test_half_open_connection_is_closed_not_parked(self):
        """A slowloris request is dropped after the timeout, not held forever."""
        server = sm._BoundedServer(("127.0.0.1", 0), sm.Handler,
                                   timeout=1.0, max_concurrency=4)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_address[1]
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            sock.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n")  # never finish
            sock.settimeout(6)
            started = time.time()
            data = sock.recv(64)
            elapsed = time.time() - started
            self.assertEqual(data, b"")            # server closed it
            self.assertLess(elapsed, 5.0)          # and did so promptly
        finally:
            sock.close()
            server.shutdown()
            server.server_close()

    def test_saturation_sheds_and_stays_serving(self):
        """Past the cap, extra connections are shed and the server still answers."""
        server = sm._BoundedServer(("127.0.0.1", 0), sm.Handler,
                                   timeout=1.0, max_concurrency=4)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_address[1]
        held = []
        try:
            for _ in range(20):
                try:
                    s = socket.create_connection(("127.0.0.1", port), timeout=2)
                    s.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n")
                    held.append(s)
                except OSError:
                    break
            # A normal request still completes while the cap is under pressure.
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d/version.json" % port, timeout=8) as resp:
                self.assertEqual(resp.status, 200)
        finally:
            for s in held:
                s.close()
            server.shutdown()
            server.server_close()


class CspTest(unittest.TestCase):
    """The shell ships a restrictive CSP, and it is emitted on every response."""

    def test_csp_is_restrictive(self):
        csp = sm._CSP
        self.assertIn("default-src 'self'", csp)
        self.assertIn("object-src 'none'", csp)
        self.assertIn("base-uri 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        # Scripts must never be allowed by 'unsafe-inline'.
        script_src = csp.split("script-src", 1)[1].split(";", 1)[0]
        self.assertNotIn("unsafe-inline", script_src)
        self.assertNotIn("unsafe-eval", csp)

    def test_inline_boot_hashes_match_the_shipped_html(self):
        """The hash must match the file, or the boot script would be blocked."""
        import base64 as _b64
        import hashlib as _hashlib
        for name in sm._INLINE_BOOT_PAGES:
            with open(os.path.join(sm.HERE, name), encoding="utf-8") as fh:
                html = fh.read()
            blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
            for block in blocks:
                digest = _b64.b64encode(
                    _hashlib.sha256(block.encode("utf-8")).digest()).decode()
                self.assertIn("'sha256-" + digest + "'", sm._CSP,
                              "%s inline block hash is missing from the CSP" % name)

    def test_every_response_carries_the_csp(self):
        app = _serve(sm.Handler)
        try:
            port = app.server_address[1]
            for path in ("/", "/version.json", "/pricing.html"):
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d%s" % (port, path), timeout=10) as resp:
                    self.assertEqual(resp.headers.get("Content-Security-Policy"),
                                     sm._CSP, path)
        finally:
            _stop(app)


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

    def test_version_route_reports_the_product_version(self):
        """One tag for the whole product, so 'what version is live?' has one answer."""
        status, _headers, body = self._get("/version.json")
        self.assertEqual(status, 200)
        self.assertIn("product", body)
        self.assertEqual(body["product"], sm._product_version())
        self.assertNotEqual(body["product"], "")

    def test_product_version_reads_the_version_file(self):
        self.assertEqual(sm._product_version(), "1.0.0")

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
    """Ten views: five primary tabs in one row plus a More sheet for the rest."""

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
        # The secondary row holds the other five views and starts hidden.
        sheet = html[html.index('id="tabs-more"'):html.index("</div>", html.index('id="tabs-more"'))]
        self.assertEqual(sheet.count('class="tab"'), 5)
        for view in ("tracker", "ai", "engine", "conn", "bridge"):
            self.assertIn('data-view="%s"' % view, sheet)
        self.assertIn("hidden", html[html.index('class="tabs-more" id="tabs-more"'):html.index(">", html.index('id="tabs-more"'))])

    def test_css_is_one_row_plus_a_sheet(self):
        css = self._read("styles.css")
        self.assertIn("grid-template-columns: repeat(6, 1fr)", css)
        self.assertIn("display: grid", css)
        # The More sheet anchors above the fixed-height bar, five across.
        self.assertIn(".tabs-more:not([hidden])", css)
        self.assertIn("grid-template-columns: repeat(5, 1fr)", css)
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
        for view in ("akoan", "chart", "vol", "ai", "engine", "tracker"):
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


class ForecastRouteTest(unittest.TestCase):
    """/forecast.json serves the same prep plan the bot prints, or a null body.

    The panel and the bot must agree on the same event, so this route must read
    the shared advisor module rather than recompute anything, and it must never
    500 when the module is missing or the feed throws.
    """

    def setUp(self):
        sm._forecast_cache.at, sm._forecast_cache.body = 0.0, None

    def test_unavailable_engine_yields_a_null_body(self):
        original = sm._load_news_forecast
        sm._load_news_forecast = lambda: None
        try:
            body = sm._compute_forecast()
        finally:
            sm._load_news_forecast = original
        self.assertEqual(body["engine"], "news-forecast")
        self.assertFalse(body["available"])
        self.assertIn("reason", body)

    def test_a_failing_engine_does_not_raise(self):
        class Boom:
            @staticmethod
            def forecast():
                raise RuntimeError("feed down")

        original = sm._load_news_forecast
        sm._load_news_forecast = lambda: Boom
        try:
            body = sm._compute_forecast()
        finally:
            sm._load_news_forecast = original
        self.assertFalse(body["available"])
        self.assertIn("detail", body)

    def test_the_body_carries_the_rendered_plan(self):
        class Stub:
            @staticmethod
            def forecast():
                return {"engine": "news-forecast", "state": "clear",
                        "event": None, "plan": None, "notes": []}

            @staticmethod
            def format_forecast(d):
                return "خطة تحضير"

        original = sm._load_news_forecast
        sm._load_news_forecast = lambda: Stub
        try:
            body = sm._compute_forecast()
        finally:
            sm._load_news_forecast = original
        self.assertTrue(body["available"])
        self.assertEqual(body["text"], "خطة تحضير")

    def test_body_is_cached_within_the_ttl(self):
        calls = {"n": 0}

        def counting():
            calls["n"] += 1
            return {"available": False, "reason": "stub"}

        original = sm._compute_forecast
        sm._compute_forecast = counting
        try:
            first = sm.forecast_body(now=1000.0)
            second = sm.forecast_body(now=1000.0 + sm._FORECAST_CACHE_TTL - 1)
        finally:
            sm._compute_forecast = original
        self.assertEqual(calls["n"], 1)
        self.assertEqual(first, second)

    def test_cache_expires_after_the_ttl(self):
        calls = {"n": 0}

        def counting():
            calls["n"] += 1
            return {"available": False, "reason": "stub"}

        original = sm._compute_forecast
        sm._compute_forecast = counting
        try:
            sm.forecast_body(now=2000.0)
            sm.forecast_body(now=2000.0 + sm._FORECAST_CACHE_TTL + 1)
        finally:
            sm._compute_forecast = original
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


class EngineProxyTimeoutTest(unittest.TestCase):
    """The engine does a synchronous model round-trip on a cold read.

    The shared 6s proxy budget would turn a healthy-but-cold engine into a 503
    for a caller who did nothing wrong, so the /engine/ route carries its own
    wider budget. These run the real proxy against a stub that answers slower
    than the shared budget but inside the engine's.
    """

    class _SlowEngine(BaseHTTPRequestHandler):
        delay = 0.0

        def log_message(self, *a):  # silence
            pass

        def do_GET(self):
            time.sleep(self.delay)
            body = json.dumps({"engine": {"configured": True, "analysis": None,
                                          "note": "cold read"}}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def test_a_cold_engine_inside_its_budget_is_not_a_503(self):
        saved_base = sm.Handler.engine_base
        slow = _serve(self._SlowEngine)
        self._SlowEngine.delay = sm._PROXY_TIMEOUT + 0.4  # longer than shared
        sm.Handler.engine_base = "http://127.0.0.1:%d" % slow.server_address[1]
        app = _serve(sm.Handler)
        try:
            url = "http://127.0.0.1:%d/engine/v1/engine" % app.server_address[1]
            with urllib.request.urlopen(url, timeout=15) as resp:
                self.assertEqual(resp.status, 200)
                body = json.loads(resp.read().decode("utf-8"))
        finally:
            self._SlowEngine.delay = 0.0
            _stop(app)
            _stop(slow)
            sm.Handler.engine_base = saved_base
        self.assertIn("engine", body)

    def test_the_engine_route_carries_its_own_budget(self):
        src = inspect.getsource(sm.Handler._proxy)
        self.assertIn("timeout", src)
        # The route passes the wider engine budget explicitly.
        routing = inspect.getsource(sm.Handler.do_GET)
        self.assertIn("_ENGINE_PROXY_TIMEOUT", routing)
if __name__ == "__main__":
    unittest.main()
