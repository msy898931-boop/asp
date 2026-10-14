#!/usr/bin/env python3
"""Tests for the mobile server's volatility route.

The phone re-scores the bot's real artifact with the bot's real code, so these
tests run that path for real against a stub bridge — no mocks of the model.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import serve_mobile as sm  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
