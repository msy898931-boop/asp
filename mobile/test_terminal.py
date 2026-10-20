"""Tests for the web terminal (mobile/terminal.html, .css, .js).

The terminal is a static shell served by serve_mobile.py, so most of what can
regress is text-level: the assets must exist, be stamped, be gated with the rest
of the app, stay bilingual, and never grow a way to inject untrusted text or
reach an origin other than the app's own. These tests pin exactly that.
"""

import os
import re
import unittest
import urllib.error
import urllib.request

import serve_mobile as sm

HERE = os.path.dirname(os.path.abspath(__file__))
TERMINAL = os.path.join(HERE, "terminal.html")
TERMINAL_CSS = os.path.join(HERE, "terminal.css")
TERMINAL_JS = os.path.join(HERE, "terminal.js")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _object_keys(source, marker):
    """Return the keys of the object literal that starts at `marker`.

    A tiny brace matcher, so the test does not need a JS parser to compare the
    Arabic and English string tables.
    """
    start = source.index(marker) + len(marker)
    depth = 1
    i = start
    while depth and i < len(source):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
        i += 1
    body = source[start:i - 1]
    return set(re.findall(r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", body))


class AssetsTest(unittest.TestCase):
    def test_all_three_files_exist(self):
        for path in (TERMINAL, TERMINAL_CSS, TERMINAL_JS):
            with self.subTest(path=os.path.basename(path)):
                self.assertTrue(os.path.isfile(path))

    def test_html_wires_the_terminal_styles_and_script(self):
        html = _read(TERMINAL)
        self.assertIn('href="terminal.css"', html)
        self.assertIn('src="terminal.js"', html)
        # It must also inherit the shared instrument-panel palette.
        self.assertIn('href="styles.css"', html)

    def test_default_direction_is_rtl_and_arabic(self):
        html = _read(TERMINAL)
        self.assertIn('lang="ar"', html)
        self.assertIn('dir="rtl"', html)

    def test_html_is_stamped_by_the_shell(self):
        # serve_mobile stamps shell assets so a redeploy is not masked by cache.
        self.assertRegex(
            sm._stamp_shell(_read(TERMINAL).encode("utf-8")).decode("utf-8"),
            r'terminal\.(css|js)\?v=[0-9a-f]{6,}')

    def test_stamp_covers_the_terminal_assets(self):
        # Changing terminal.js must change the stamp, or the cache never busts.
        html = _read(TERMINAL).encode("utf-8")
        self.assertIn(b"terminal.css", sm._stamp_shell(html))
        self.assertIn(b"terminal.js", sm._stamp_shell(html))
        # The stamp is built from these names.
        import inspect
        src = inspect.getsource(sm._build_stamp)
        for name in ("terminal.html", "terminal.css", "terminal.js"):
            self.assertIn(name, src)

    def test_index_links_to_the_terminal(self):
        html = _read(os.path.join(HERE, "index.html"))
        self.assertIn('href="/terminal.html"', html)


class BilingualTest(unittest.TestCase):
    def test_arabic_and_english_tables_share_every_key(self):
        js = _read(TERMINAL_JS)
        ar = _object_keys(js, "  ar: {")
        en = _object_keys(js, "  en: {")
        self.assertTrue(ar, "the Arabic table is empty")
        self.assertEqual(ar, en,
                         "string tables drifted: %s" % (ar.symmetric_difference(en)))

    def test_language_toggle_is_present(self):
        self.assertIn("astra-lang", _read(TERMINAL_JS))

    def test_dir_flips_with_the_language(self):
        js = _read(TERMINAL_JS)
        self.assertIn('lang === "ar" ? "rtl" : "ltr"', js)


class SafetyTest(unittest.TestCase):
    def test_no_dynamic_code_execution(self):
        js = _read(TERMINAL_JS)
        for forbidden in ("eval(", "new Function", "document.write"):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, js)

    def test_untrusted_text_is_escaped_before_innerhtml(self):
        js = _read(TERMINAL_JS)
        self.assertIn("function esc(", js)
        # The one place that builds markup from a variable is the service list.
        self.assertRegex(js, r"\$\{esc\(ep\)\}")

    def test_no_absolute_origins_are_called(self):
        js = _read(TERMINAL_JS)
        self.assertNotRegex(js, r"https?://[^\s\"']+")
        self.assertIn('const API = "api/v1/"', js)
        self.assertIn('const BRIDGE = "bridge/"', js)

    def test_console_escapes_nothing_into_the_dom(self):
        # The console renders raw JSON via textContent, never innerHTML.
        js = _read(TERMINAL_JS)
        self.assertIn("out.textContent = JSON.stringify", js)


class UnknownIsNotSafeTest(unittest.TestCase):
    def test_unknown_maps_to_the_unknown_tone(self):
        js = _read(TERMINAL_JS)
        self.assertRegex(js, r'return "unknown"')

    def test_a_missing_value_reads_as_unavailable_not_zero(self):
        js = _read(TERMINAL_JS)
        # metric() shows v_na for null/undefined rather than coercing to 0.
        self.assertIn('value === null || value === undefined ? T("v_na")', js)


class MarketPanelTest(unittest.TestCase):
    """The market & trades view: direction, trade plan, shadow positions.

    The panel is where the project's honesty rule is easiest to break, so these
    pin the two things that matter: every number comes from a real surface, and
    an uncalibrated score is never shown as a probability.
    """

    def test_the_section_and_tab_exist(self):
        html = _read(TERMINAL)
        self.assertIn('id="view-market"', html)
        self.assertIn('data-view="market"', html)
        self.assertIn('id="mk-levels"', html)
        self.assertIn('id="mk-positions"', html)
        self.assertIn('id="mk-outcomes"', html)

    def test_the_bar_has_eight_tabs(self):
        html = _read(TERMINAL)
        self.assertEqual(html.count('class="tab"'), 8)

    def test_market_is_a_route(self):
        js = _read(TERMINAL_JS)
        self.assertRegex(js, r'const ROUTES = \[[^\]]*"market"')
        self.assertIn('if (route === "market") renderMarket();', js)

    def test_direction_reads_the_analysis_surface(self):
        js = _read(TERMINAL_JS)
        # The direction is the engine's own word, taken from signal.direction.
        self.assertIn("sig.direction", js)
        # And the market view is the analysis document, fetched once.
        self.assertIn("state.market = state.analysis;", js)

    def test_probability_is_shown_only_when_calibrated(self):
        js = _read(TERMINAL_JS)
        # RULE C: the calibrated flag must gate the probability, and the raw
        # score must be labelled as a score, never dressed as a probability.
        self.assertIn("sig.probability_calibrated === true", js)
        self.assertIn('T("mk_score_note")', js)

    def test_trade_plan_comes_from_the_risk_proposal(self):
        js = _read(TERMINAL_JS)
        for field in ("levels.entry", "levels.stop_loss", "levels.take_profit",
                      "levels.targets", "levels.reward_risk",
                      "levels.suggested_risk_pct"):
            with self.subTest(field=field):
                self.assertIn(field, js)

    def test_the_scaled_ladder_renders_as_its_own_row(self):
        js = _read(TERMINAL_JS)
        # A ladder is only drawn when there is more than one rung, so a
        # single-target card keeps the older five-cell grid.
        self.assertIn("levels.targets", js)
        self.assertIn("function ladderCell", js)
        self.assertIn("plan-cell-wide", js)
        self.assertIn("ladder.length > 1", js)

    def test_positions_and_outcomes_are_read_from_the_shadow_surfaces(self):
        js = _read(TERMINAL_JS)
        self.assertIn('["positions", "shadow/positions"]', js)
        self.assertIn('["outcomes", "shadow/outcomes"]', js)

    def test_the_last_recorded_decision_comes_from_the_ledger(self):
        js = _read(TERMINAL_JS)
        self.assertIn('["signals", "signals/latest"]', js)
        self.assertIn("state.signals", js)
        self.assertIn('s.available', js)

    def test_an_unknown_direction_is_not_guessed_into_a_side(self):
        js = _read(TERMINAL_JS)
        # dirClass falls back to "flat", never to up or down.
        fn = re.search(r"function dirClass[\s\S]*?\n\}", js).group(0)
        self.assertIn('return "flat"', fn)
        self.assertNotRegex(fn, r'return "up";\s*\n\}')

    def test_market_styles_are_scoped_to_the_terminal(self):
        # The market panel's own CSS lives in terminal.css, not the shared
        # stylesheet the phone app depends on.
        css = _read(TERMINAL_CSS)
        for cls in (".dir-hero", ".plan-grid", ".pos-card", ".chip"):
            with self.subTest(cls=cls):
                self.assertIn(cls, css)
        shared = _read(os.path.join(HERE, "styles.css"))
        self.assertNotIn(".dir-hero", shared)

    def test_regime_labels_match_the_backend_vocabulary(self):
        # The analysis surface emits TRENDING_UP / TRENDING_DOWN / RANGING; a
        # label keyed on TREND_UP would silently fall back to the raw English
        # word in the Arabic UI.
        js = _read(TERMINAL_JS)
        for key in ("rg_TRENDING_UP", "rg_TRENDING_DOWN", "rg_RANGING"):
            with self.subTest(key=key):
                self.assertIn(key, js)

    def test_no_plan_names_the_gate_instead_of_waiting(self):
        js = _read(TERMINAL_JS)
        self.assertIn("function whyNoTrade(", js)
        # Every branch must resolve to a real key, so the note is never blank.
        for key in ("why_no_direction", "why_not_eligible", "why_risk_denied",
                    "why_awaiting", "why_locked"):
            with self.subTest(key=key):
                self.assertIn(key + ":", js)
        # The reason is written into the note when no plan is drawn.
        self.assertIn('$("mk-plan-note").textContent = whyNoTrade(a);', js)

    def test_a_locked_backend_is_named_not_guessed(self):
        js = _read(TERMINAL_JS)
        # A 401 on analysis/latest is a key gate, not a missing decision.
        self.assertIn("analysisLocked", js)
        self.assertIn("why_locked", js)


class NewsPanelTest(unittest.TestCase):
    """The news & gold view: the blackout window, the schedule, the gold read.

    The panel is where a news claim is easiest to overstate, so these pin the
    honesty: the window word comes from the engine's state, the gold read is
    asserted only for the dollar, and the panel is wired to the /news.json route
    rather than a raw external origin.
    """

    def test_the_section_and_tab_exist(self):
        html = _read(TERMINAL)
        self.assertIn('id="view-news"', html)
        self.assertIn('data-view="news"', html)
        for el in ("nw-window", "nw-upcoming", "nw-released", "nw-headlines"):
            with self.subTest(el=el):
                self.assertIn('id="%s"' % el, html)

    def test_news_is_a_route(self):
        js = _read(TERMINAL_JS)
        self.assertRegex(js, r'const ROUTES = \[[^\]]*"news"')
        self.assertIn('if (route === "news") renderNews();', js)

    def test_the_panel_reads_the_news_route(self):
        js = _read(TERMINAL_JS)
        # The digest comes from the app's own origin, never an external host.
        self.assertIn('getJson("news.json"', js)
        self.assertNotRegex(js, r'https?://[^"\']*news\.json')

    def test_the_window_word_comes_from_the_engine_state(self):
        js = _read(TERMINAL_JS)
        self.assertIn("win.state", js)
        for key in ("nw_window_ACTIVE", "nw_window_UPCOMING", "nw_window_CLEAR"):
            with self.subTest(key=key):
                self.assertIn(key + ":", js)

    def test_the_gold_read_is_asserted_only_for_the_dollar(self):
        js = _read(TERMINAL_JS)
        # `certain` is the engine's own flag; without it the panel says the link
        # is indirect rather than inventing a direction.
        fn = re.search(r"function nwImpact[\s\S]*?\n\}", js).group(0)
        self.assertIn("r.certain", fn)
        self.assertIn('T("nw_impact_indirect")', fn)
        self.assertIn("r.gold_dir", fn)

    def test_every_list_has_an_empty_state(self):
        js = _read(TERMINAL_JS)
        for key in ("nw_empty_upcoming", "nw_empty_released", "nw_empty_headlines"):
            with self.subTest(key=key):
                self.assertIn(key + ":", js)

    def test_news_styles_are_scoped_to_the_terminal(self):
        css = _read(TERMINAL_CSS)
        for cls in (".nw-window", ".nw-card", ".nw-list"):
            with self.subTest(cls=cls):
                self.assertIn(cls, css)
        shared = _read(os.path.join(HERE, "styles.css"))
        self.assertNotIn(".nw-window", shared)


class PrepPlanPanelTest(unittest.TestCase):
    """The pre-news prep block: a bracket, never a direction.

    This is where a forecast is easiest to overstate, so these pin the honesty:
    the block reads the shared /forecast.json route, hides when there is no
    plan, and shows the measured band and both break levels without ever
    naming a side.
    """

    def test_the_prep_block_exists_and_is_hidden_by_default(self):
        html = _read(TERMINAL)
        self.assertIn('id="nw-prep"', html)
        self.assertRegex(html, r'id="nw-prep"[^>]*hidden')
        for el in ("nw-prep-title", "nw-prep-band", "nw-prep-steps", "nw-prep-src"):
            with self.subTest(el=el):
                self.assertIn('id="%s"' % el, html)

    def test_the_panel_reads_the_forecast_route(self):
        js = _read(TERMINAL_JS)
        # Same origin, never an external host.
        self.assertIn('getJson("forecast.json"', js)
        self.assertNotRegex(js, r'https?://[^"\']*forecast\.json')

    def test_the_block_hides_when_there_is_no_plan(self):
        js = _read(TERMINAL_JS)
        fn = re.search(r"function renderPrep[\s\S]*?\n\}", js).group(0)
        self.assertIn("box.hidden = true", fn)
        self.assertIn("!plan", fn)

    def test_the_block_never_names_a_direction(self):
        js = _read(TERMINAL_JS)
        fn = re.search(r"function renderPrep[\s\S]*?\n\}", js).group(0)
        for banned in ("LONG", "SHORT", "سيصعد", "سينزل", "gold_dir"):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, fn)

    def test_the_block_shows_the_band_and_both_breaks(self):
        js = _read(TERMINAL_JS)
        fn = re.search(r"function renderPrep[\s\S]*?\n\}", js).group(0)
        for key in ("expected_move", "median", "p90", "break_up", "break_down"):
            with self.subTest(key=key):
                self.assertIn(key, fn)

    def test_prep_styles_are_scoped_to_the_terminal(self):
        css = _read(TERMINAL_CSS)
        for cls in (".nw-prep", ".nw-prep-band", ".nw-prep-note"):
            with self.subTest(cls=cls):
                self.assertIn(cls, css)
        shared = _read(os.path.join(HERE, "styles.css"))
        self.assertNotIn(".nw-prep", shared)

    def test_the_prep_note_states_direction_is_unpredicted(self):
        js = _read(TERMINAL_JS)
        self.assertIn("nw_prep_note:", js)
        # The Arabic block (STRINGS.ar) must carry the honesty word.
        ar = js.split("ar: {", 1)[1].split("en: {", 1)[0]
        self.assertIn('nw_prep_note: "الاتجاه غير متوقّع', ar)


class GateTest(unittest.TestCase):
    """The terminal is part of the app, so it lives behind the same gate."""

    def setUp(self):
        self._saved = sm.ACCESS_TOKEN
        sm.ACCESS_TOKEN = ""

    def tearDown(self):
        sm.ACCESS_TOKEN = self._saved

    def _status(self, path, headers=None):
        from test_serve_mobile import _serve, _stop
        app = _serve(sm.Handler)
        try:
            url = "http://127.0.0.1:%d%s" % (app.server_address[1], path)
            req = urllib.request.Request(url, headers=headers or {})
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.status, resp.read()
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read()
        finally:
            _stop(app)

    def test_terminal_is_served_when_the_gate_is_off(self):
        status, body = self._status("/terminal.html")
        self.assertEqual(status, 200)
        self.assertIn(b"ASTRA", body)

    def test_terminal_needs_the_key_once_the_gate_is_on(self):
        sm.ACCESS_TOKEN = "s3cret"
        for path in ("/terminal.html", "/terminal.css", "/terminal.js"):
            with self.subTest(path=path):
                status, _ = self._status(path)
                self.assertEqual(status, 401, path)

    def test_the_key_on_the_url_opens_the_terminal(self):
        sm.ACCESS_TOKEN = "s3cret"
        status, _ = self._status("/terminal.html?k=s3cret")
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
