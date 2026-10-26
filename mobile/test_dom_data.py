"""Browser-level (DOM) tests for the phone app with a seeded backend.

`test_dom_browser.py` aborts every live endpoint on purpose: it proves the
shell survives a dead backend. That leaves the other half untested — that the
panels *render* a real reading. A renderer that reads the wrong key, or a
rename on the backend, passes every text test and every dead-backend test and
still shows an empty panel on the phone.

These tests serve the real `index.html` through the real `serve_mobile.Handler`
and answer its `/api/v1/*` calls from the canonical fixtures in
`scripts/mock_api.py` (the twin the contract tests read), with a synthetic
candle series and a volatility reading. They then assert on the rendered DOM.

The payloads come from `mock_api.build_payloads`, so the app is fed the same
frozen contract the backend is checked against — if the two ever drift, these
tests see it on the phone instead of in production.

The suite is skipped, not failed, when Playwright is absent.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for path in (HERE, os.path.join(REPO, "scripts")):
    if path not in sys.path:
        sys.path.insert(0, path)

import serve_mobile as sm  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
    HAVE_PLAYWRIGHT = True
except Exception:  # noqa: BLE001 - absent browser must skip, never fail
    HAVE_PLAYWRIGHT = False

try:
    import mock_api

    with open(mock_api.SCHEMA_PATH, encoding="utf-8") as _fh:
        _SCHEMA = json.load(_fh)
    PAYLOADS = mock_api.build_payloads(_SCHEMA, calibrated=False)
    PAYLOADS_CALIBRATED = mock_api.build_payloads(_SCHEMA, calibrated=True)
    HAVE_MOCK = True
except Exception:  # noqa: BLE001 - the fixtures must never break the suite
    PAYLOADS, PAYLOADS_CALIBRATED = {}, {}
    HAVE_MOCK = False


def _bars(n: int = 30) -> list[dict]:
    """A deterministic ascending candle series; bars[-1] is the newest close."""
    out = []
    for i in range(n):
        base = 2400.0 + i * 0.5
        out.append({
            "time": 1760000000 + i * 900,
            "open": base,
            "high": base + 1.2,
            "low": base - 1.1,
            "close": base + 0.4,
            "tick_volume": 100 + i,
            "spread": 12,
            "real_volume": 0,
        })
    return out


CANDLES = {"status": "OK", "payload": {
    "bars": _bars(), "timeframe": "M15", "symbol": "XAUUSD",
    "count": 30, "closed_only": True, "newest_closed_time": 1760026100,
    "freshness": "FRESH",
}}
TICK = {"status": "OK", "payload": {"last": 2415.75, "symbol": "XAUUSD"}}
VOL = {"available": True, "probability": 0.5, "threshold": 0.65,
       "tier": "elevated", "timeframes": ["D1", "H1", "H4", "M15", "M5"],
       "reason": None}
PUSH = {"available": False}

# The isolated analysis engine's envelope, shaped exactly like ai_engine.py's
# `Engine._build`: `analysis` is null unless a provider key is configured. The
# panel must read `analysis` (never the envelope) and label the win-rate as an
# estimate, so the fixture carries a populated reply.
ENGINE = {
    "engine": {
        "schema_version": "1.0", "symbol": "XAUUSD", "validated": False,
        "source": "ai-engine (isolated)", "provider": "groq",
        "configured": True, "model": "llama-3.3-70b",
        "analysis": {
            "market_read": "الفريمات الصغيرة صاعدة والبنية العامة تدعم الشراء.",
            "bias": "LONG",
            "news_used": True, "key_event": "بيانات التضخم",
            "rejected": [],
            "trades": [{
                "direction": "LONG", "entry": 2415.5, "stop": 2408.0,
                "targets": [2422.0, 2430.0], "target": 2430.0,
                "reward_risk": 1.93, "win_rate": 0.55,
                "timeframe": "M15",
                "rationale": "كسر مقاومة قصيرة مع زخم صاعد.",
                "anchor": {"anchored": True, "note": ""},
            }],
            "engine": {"timeframes_read": ["M15", "H1", "H4"],
                       "timeframes_missing": [], "rule_card": None,
                       "note": "isolated"},
        },
        "note": "AI analysis engine output.",
    }
}

# The isolated SMC/ICT engine's envelope, shaped like smc_engine.py's
# `Engine._build`: `analysis` is null unless a provider key is configured. The
# panel reads `analysis` and shows the model's own plans, with the confidence
# labelled an estimate and the swept liquidity / point of interest named.
SMC = {
    "smc": {
        "schema_version": "1.0", "symbol": "XAUUSD", "validated": False,
        "source": "smc-engine (isolated)", "provider": "groq",
        "framework": "SMC/ICT",
        "configured": True, "model": "llama-3.3-70b",
        "analysis": {
            "market_read": "بنية صاعدة على H1 مع مسح سيولة أسفل القاع.",
            "bias": "LONG",
            "news_caution": True, "key_event": "قرار الفائدة",
            "rejected": [],
            "plans": [{
                "direction": "LONG", "entry": 2415.5, "stop": 2408.0,
                "targets": [2426.0, 2434.0],
                "reward_risk": 2.53, "confidence": 0.55,
                "confidence_validated": False,
                "rationale": "CHoCH صاعد بعد مسح سيولة.",
                "sweep": "قاع متساوٍ", "poi": "بلوك أوامر",
            }],
            "engine": {"timeframes_read": ["M15", "H1", "H4"],
                       "timeframes_missing": [], "note": "isolated"},
        },
        "note": "SMC/ICT analysis engine output.",
    }
}


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


def _serve():
    server = _QuietServer(("127.0.0.1", 0), sm.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@unittest.skipUnless(HAVE_PLAYWRIGHT and HAVE_MOCK,
                     "playwright or the api fixtures are unavailable")
class SeededDomTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_log = sm.Handler.log_message
        sm.Handler.log_message = lambda *a, **k: None
        cls.server = _serve()
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        sm.Handler.log_message = cls._orig_log

    def _route(self, route, payloads, offline=False):
        url = route.request.url
        path = urllib.parse.urlparse(url).path

        def fulfil(body, status=200):
            route.fulfill(status=status, content_type="application/json",
                          body=json.dumps(body))

        if offline:
            # Everything the app talks to is unreachable; only the shell loads.
            shell = (url.startswith(self.base) and path in ("/", "/index.html")) \
                or path.endswith((".css", ".js", ".webmanifest", ".png"))
            if shell:
                route.continue_()
            else:
                route.abort()
            return

        if path.startswith("/api/v1/"):
            body = payloads.get("GET " + path)
            if body is None:
                fulfil({"error": "unknown route"}, 404)
            else:
                fulfil(body)
        elif path.startswith("/bridge/v1/candles"):
            fulfil(CANDLES)
        elif path.startswith("/bridge/v1/tick"):
            fulfil(TICK)
        elif path == "/vol.json":
            fulfil(VOL)
        elif path == "/push.json":
            fulfil(PUSH)
        elif path.startswith("/engine/v1/engine"):
            fulfil(payloads.get("engine", ENGINE))
        elif path.startswith("/smc/v1/smc"):
            fulfil(payloads.get("smc", SMC))
        elif url.startswith(self.base):
            route.continue_()
        else:
            route.abort()  # fonts and anything else: offline is fine here

    def _open(self, payloads=None, offline=False, seed=None):
        # Service workers are blocked: once one controls the page its own
        # fetches bypass Playwright's page routes, so the seeded responses would
        # never be seen and the app would talk to the real (unreachable)
        # backend instead. The worker's cache policy has its own tests.
        context = self.browser.new_context(
            viewport={"width": 390, "height": 844}, service_workers="block")
        self.addCleanup(context.close)
        page = context.new_page()
        if seed is not None:
            page.add_init_script(
                "try{localStorage.setItem('astra-last-read', %s);"
                "localStorage.setItem('astra-theme','dark');}catch(e){}"
                % json.dumps(json.dumps(seed)))
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/*",
                   lambda r: self._route(r, payloads or PAYLOADS, offline))
        page.goto(self.base + "/", wait_until="load")
        return page, errors

    def _wait(self, page, expr, timeout=5000):
        # `page.wait_for_function` wraps the expression in `new Function()`,
        # which a strict `script-src` (no `'unsafe-eval'`) blocks — so the
        # shipped CSP makes it unusable. Poll with `evaluate` instead: it runs
        # the expression directly, which the policy permits.
        deadline = time.monotonic() + timeout / 1000.0
        while True:
            try:
                if page.evaluate("() => !!(%s)" % expr):
                    return
            except Exception:  # noqa: BLE001 - a mid-navigation page error must not abort the wait
                pass
            if time.monotonic() >= deadline:
                raise AssertionError("timed out waiting for: %s" % expr)
            time.sleep(0.05)

    # -- the status panel --------------------------------------------------

    def test_status_panel_renders_the_seeded_health(self):
        page, errors = self._open()
        try:
            self._wait(page, "document.querySelector('#health-value')"
                             ".textContent === 'ONLINE'")
            self.assertEqual(page.text_content("#r-mode"), "SHADOW")
            self.assertEqual(page.text_content("#r-startup"), "READY")
            self.assertEqual(page.text_content("#r-symbol"), "XAUUSD")
            self.assertEqual(page.text_content("#r-decision"), "yes")
            self.assertEqual(page.text_content("#health-sub"), "Decision data")
            self.assertEqual(page.text_content("#b-process"), "ONLINE")
            self.assertEqual(page.text_content("#b-ready"), "yes")
            self.assertEqual(page.text_content("#b-transport"), "http_loopback")
            self.assertEqual(errors, [])
        finally:
            page.close()

    def test_banner_reports_the_live_aggregate_and_source(self):
        page, _ = self._open()
        try:
            self._wait(page, "!document.querySelector('#banner')"
                             ".classList.contains('hidden')")
            self.assertIn("online", page.text_content("#banner-text"))
        finally:
            page.close()

    # -- the signal panel --------------------------------------------------

    def test_signal_panel_renders_an_uncalibrated_reading_honestly(self):
        page, _ = self._open()
        try:
            self._wait(page, "document.querySelector('#health-value')"
                             ".textContent === 'ONLINE'")
            page.click('.tab[data-view="signal"]')
            self.assertEqual(page.text_content("#sig-direction"), "FLAT")
            self.assertEqual(page.text_content("#sig-regime"), "UNKNOWN")
            # The frozen contract carries a score but no calibrated
            # probability; the panel must show the score and refuse a number
            # for the probability rather than invent one.
            self.assertEqual(page.text_content("#v-score"), "0.5")
            self.assertEqual(page.text_content("#v-conf"), "n/a")
            self.assertEqual(page.text_content("#l-entry"), "--")
            self.assertIn("H4 bias", page.text_content("#sig-context"))
        finally:
            page.close()

    def test_signal_panel_shows_a_calibrated_probability_when_present(self):
        page, _ = self._open(payloads=PAYLOADS_CALIBRATED)
        try:
            self._wait(page, "document.querySelector('#health-value')"
                             ".textContent === 'ONLINE'")
            page.click('.tab[data-view="signal"]')
            self.assertEqual(page.text_content("#sig-direction"), "UP")
            self.assertEqual(page.text_content("#v-conf"), "61%")
        finally:
            page.close()

    def test_signal_panel_renders_the_scaled_ladder_when_present(self):
        payloads = json.loads(json.dumps(PAYLOADS))
        levels = payloads["GET /api/v1/analysis/latest"]["data"]["levels"]
        levels.update({"entry": 2000.0, "stop_loss": 1995.0,
                       "take_profit": 2012.0, "reward_risk": 2.4,
                       "targets": [2002.4, 2004.8, 2007.2, 2009.6, 2012.0]})
        page, _ = self._open(payloads=payloads)
        try:
            self._wait(page, "document.querySelector('#health-value')"
                             ".textContent === 'ONLINE'")
            page.click('.tab[data-view="signal"]')
            self.assertFalse(page.is_hidden("#l-ladder-row"))
            text = page.text_content("#l-ladder")
            self.assertIn("2002.4", text)
            self.assertIn("2012.0", text)
        finally:
            page.close()

    def test_signal_panel_hides_the_ladder_on_a_single_target(self):
        page, _ = self._open()  # the frozen-null payload has no ladder
        try:
            self._wait(page, "document.querySelector('#health-value')"
                             ".textContent === 'ONLINE'")
            page.click('.tab[data-view="signal"]')
            self.assertTrue(page.is_hidden("#l-ladder-row"))
        finally:
            page.close()

    # -- the ticker and the chart -----------------------------------------

    def test_ticker_shows_the_live_price(self):
        page, _ = self._open()
        try:
            self._wait(page, "!document.querySelector('#ticker').hidden")
            self.assertEqual(page.text_content("#price"), "2415.75")
            self.assertIn("live", page.text_content("#price-basis"))
        finally:
            page.close()

    def test_chart_draws_the_candle_series(self):
        page, _ = self._open()
        try:
            self._wait(page, "document.querySelector('#health-value')"
                             ".textContent === 'ONLINE'")
            page.click('.tab[data-view="chart"]')
            self._wait(page, "document.querySelectorAll('#chart rect').length > 0")
            bodies = page.eval_on_selector_all("#chart rect", "e => e.length")
            self.assertEqual(bodies, 30, "one candle body per bar")
            self.assertEqual(page.text_content("#dq-bars"), "30")
            self.assertIn("30 closed bars", page.text_content("#chart-meta"))
        finally:
            page.close()

    # -- the volatility panel ---------------------------------------------

    def test_volatility_panel_renders_the_reading(self):
        page, _ = self._open()
        try:
            self._wait(page, "document.querySelector('#health-value')"
                             ".textContent === 'ONLINE'")
            page.click('.tab[data-view="vol"]')
            self._wait(page, "document.querySelector('#vol-pct')"
                             ".textContent === '50%'")
            self.assertEqual(page.text_content("#vol-tier"), "elevated")
            self.assertIn("livelier", page.text_content("#vol-verdict"))
        finally:
            page.close()

    # -- the offline-first promise ----------------------------------------

    def test_a_cached_reading_paints_when_the_backend_is_gone(self):
        seed = {"v": 1, "at": 1760000000000, "snap": {
            "health": {"aggregate": "ONLINE", "decision_grade_data": True},
            "system": {"mode": "SHADOW", "startup_stage": "READY"},
            "bridge": {"bridge_process_state": "ONLINE", "mt5_ready": True,
                       "resolved_symbol": "XAUUSD", "transport": "http_loopback"},
            "analysis": {"signal": {"direction": "UP", "score": 0.58},
                         "context": {"regime": "TREND_UP", "h4_bias": "BULLISH",
                                     "m15_trigger": "UP",
                                     "volatility_state": "NORMAL"}},
            "ticker": {"price": 2401.0, "change": 1.0, "pct": 0.04,
                       "live": False, "tf": "M15"},
        }}
        page, _ = self._open(offline=True, seed=seed)
        try:
            self._wait(page, "document.querySelector('#health-value')"
                             ".textContent === 'ONLINE'")
            # The live poll fails; the app must fall back and say so, not
            # present the cached reading as fresh.
            self._wait(page, "document.querySelector('#banner-text')"
                             ".textContent.includes('offline')")
            self.assertIn("offline", page.text_content("#banner-text"))
            self.assertIn("last reading", page.text_content("#banner-text"))
            self.assertEqual(page.text_content("#price"), "2401.00")
        finally:
            page.close()


    # -- the isolated analysis engine panel --------------------------------

    def test_engine_panel_renders_the_model_trades(self):
        page, _ = self._open()
        try:
            self._wait(page, "document.querySelector('#health-value')"
                             ".textContent === 'ONLINE'")
            page.click(".tab-more")
            page.click('#tabs-more .tab[data-view="engine"]')
            self._wait(page, "document.querySelectorAll('#eng-trades .ai-card')"
                             ".length === 1")
            card = page.text_content("#eng-trades .ai-card")
            self.assertIn("2415.50", card)
            self.assertIn("2408.00", card)
            # The win-rate is the model's claim; it must be labelled as one.
            self.assertIn("55%", card)
            self.assertIn("estimate", card)
            self.assertIn("1.93", card)
            # Two targets render as a ladder, not a single number.
            self.assertEqual(
                page.eval_on_selector_all(
                    "#eng-trades .ai-card-targets-list li", "e => e.length"), 2)
            self.assertIn("LONG" if "LONG" in card else "buy", card.lower())
            self.assertIn("M15", card)
        finally:
            page.close()

    def test_engine_panel_reads_analysis_not_the_envelope(self):
        # `configured: true` with a null `analysis` is the paused/unavailable
        # shape. Rendering the envelope would print "[object Object]"; the panel
        # must instead show the honest status and no trade cards.
        stalled = {"engine": {"configured": True, "analysis": None,
                              "rate_limited": True,
                              "note": "AI engine paused: quota spent."}}
        page, _ = self._open(payloads={**PAYLOADS, "engine": stalled})
        try:
            self._wait(page, "document.querySelector('#health-value')"
                             ".textContent === 'ONLINE'")
            page.click(".tab-more")
            page.click('#tabs-more .tab[data-view="engine"]')
            self._wait(page, "document.querySelector('#eng-status')"
                             ".textContent.length > 0")
            self.assertIn("quota", page.text_content("#eng-status"))
            self.assertEqual(
                page.eval_on_selector_all("#eng-trades .ai-card", "e => e.length"), 0)
        finally:
            page.close()


    # -- the isolated SMC/ICT engine panel ---------------------------------

    def test_smc_panel_renders_the_model_plans(self):
        page, _ = self._open()
        try:
            self._wait(page, "document.querySelector('#health-value')"
                             ".textContent === 'ONLINE'")
            page.click(".tab-more")
            page.click('#tabs-more .tab[data-view="smc"]')
            self._wait(page, "document.querySelectorAll('#smc-plans .ai-card')"
                             ".length === 1")
            card = page.text_content("#smc-plans .ai-card")
            self.assertIn("2415.50", card)
            self.assertIn("2408.00", card)
            self.assertIn("2.53", card)
            # The confidence is the model's claim; it must be labelled as one.
            self.assertIn("55%", card)
            self.assertIn("estimate", card)
            # Two targets render as a ladder, not a single number.
            self.assertEqual(
                page.eval_on_selector_all(
                    "#smc-plans .ai-card-targets-list li", "e => e.length"), 2)
            # The SMC vocabulary the persona speaks in is surfaced.
            self.assertIn("قاع متساوٍ", card)
            self.assertIn("بلوك أوامر", card)
        finally:
            page.close()

    def test_smc_panel_reads_analysis_not_the_envelope(self):
        # `configured: true` with a null `analysis` is the paused/unavailable
        # shape. Rendering the envelope would print "[object Object]"; the panel
        # must instead show the honest status and no plan cards.
        stalled = {"smc": {"configured": True, "analysis": None,
                           "rate_limited": True,
                           "note": "SMC engine paused: quota spent."}}
        page, _ = self._open(payloads={**PAYLOADS, "smc": stalled})
        try:
            self._wait(page, "document.querySelector('#health-value')"
                             ".textContent === 'ONLINE'")
            page.click(".tab-more")
            page.click('#tabs-more .tab[data-view="smc"]')
            self._wait(page, "document.querySelector('#smc-status')"
                             ".textContent.length > 0")
            self.assertIn("quota", page.text_content("#smc-status"))
            self.assertEqual(
                page.eval_on_selector_all("#smc-plans .ai-card", "e => e.length"), 0)
        finally:
            page.close()


if __name__ == "__main__":
    unittest.main()
