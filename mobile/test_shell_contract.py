#!/usr/bin/env python3
"""Shell-contract tests: the shipped phone assets, read as source.

These assert on the files the browser actually receives - index.html, the CSS,
the manifest, the service worker, the client scripts - rather than on server
behaviour. They were split out of test_serve_mobile.py, which had grown past
the size the project keeps files under; the assertions are unchanged. Nothing
here starts a server or touches the network, so it runs in milliseconds.

Run directly:  python3 test_shell_contract.py
"""

from __future__ import annotations

import inspect
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import serve_mobile as sm  # noqa: E402

# The client is split across ordered classic scripts that share one global
# scope. Tests that assert on client source read them all, in load order, as
# one string, so a symbol's file may change without breaking the assertion.
CLIENT_SCRIPTS = ("app.js", "i18n.js", "panels.js", "advisor.js",
                  "tracker.js", "alerts.js", "vol.js", "akoan.js", "engine.js",
                  "ticker.js")


def client_js() -> str:
    parts = []
    for name in CLIENT_SCRIPTS:
        with open(os.path.join(sm.HERE, name), encoding="utf-8") as fh:
            parts.append(fh.read())
    return "\n".join(parts)


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
        # /vol.json, /akoan.json and /forecast.json carry a current number,
        # exactly like the live endpoints above them. Caching one would serve a
        # stale reading — and because the volatility panel stamps the *render*
        # time, not the data time, the stale number would look freshly computed.
        sw = self._read("sw.js")
        self.assertIn('url.pathname.endsWith("/vol.json")', sw)
        self.assertIn('url.pathname.endsWith("/akoan.json")', sw)
        self.assertIn('url.pathname.endsWith("/forecast.json")', sw)
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


class AdvisorCardRenderTest(unittest.TestCase):
    """The trade-card panel reads the *inner* card, not the envelope.

    The advisor's reply is an envelope whose `trade_card` wraps the levels in a
    `card` object. Rendering `agreement` off the envelope stringified the whole
    object as "[object Object]" on the phone — a bug no Python test could see,
    because the renderer is JavaScript. This pins the two keys the renderer
    depends on so a refactor cannot silently reintroduce it.
    """

    def setUp(self):
        with open(os.path.join(sm.HERE, "advisor.js"), encoding="utf-8") as fh:
            self.src = fh.read()

    def test_agreement_is_read_from_the_inner_card(self):
        self.assertIn("c.agreement", self.src)
        self.assertNotIn("card.agreement", self.src)

    def test_the_renderer_still_builds_the_level_rows(self):
        for key in ("c.entry", "c.stop", "c.target", "c.rr"):
            self.assertIn(key, self.src)

    def test_a_suppressed_read_is_shown_not_hidden(self):
        # A read with no card still carries a reason; when the structural frame
        # vetoed a real setup, hiding the panel made it look like the system
        # only ever saw buys. The renderer must surface that reason.
        self.assertIn("card.available", self.src)
        self.assertIn("card.reason", self.src)
        self.assertIn("ai_card_hold", self.src)
        self.assertIn("ai_card_none", self.src)
        self.assertIn("ai_dir_short", self.src)

    def test_the_hold_strings_exist_in_both_languages(self):
        with open(os.path.join(sm.HERE, "i18n.js"), encoding="utf-8") as fh:
            i18n = fh.read()
        for key in ("ai_card_hold", "ai_card_none", "ai_dir_long",
                    "ai_dir_short", "ai_dir_up", "ai_dir_down"):
            self.assertGreaterEqual(i18n.count(key + ":"), 2,
                                    "%s must be in en and ar" % key)


class EnginePanelRenderTest(unittest.TestCase):
    """The engine panel reads the *inner* analysis, and calls the rate a guess.

    The engine's reply is an envelope whose `analysis` wraps the model's own
    trades. Rendering `bias` or `trades` off the envelope would stringify the
    whole object as "[object Object]" on the phone — the same bug the advisor
    card once had, in a new renderer. The win-rate is the model's claim, never a
    measured edge, so every surface of it must say "estimate".
    """

    def setUp(self):
        with open(os.path.join(sm.HERE, "engine.js"), encoding="utf-8") as fh:
            self.src = fh.read()

    def test_the_analysis_object_is_read_from_the_envelope(self):
        self.assertIn("env.engine", self.src)
        self.assertIn("e.analysis", self.src)

    def test_the_win_rate_is_labelled_an_estimate(self):
        self.assertIn("t.win_rate", self.src)
        self.assertIn("eng_winrate_est", self.src)

    def test_an_unconfigured_engine_is_shown_not_hidden(self):
        self.assertIn("e.configured", self.src)
        self.assertIn("eng_unconfigured", self.src)
        self.assertIn("eng_unreachable", self.src)

    def test_a_rejected_trade_is_surfaced(self):
        # The engine drops incoherent model trades with a reason. Hiding the
        # count would make a partial reply look complete.
        self.assertIn("rejected", self.src)
        self.assertIn("eng_rejected", self.src)

    def test_the_engine_strings_exist_in_both_languages(self):
        with open(os.path.join(sm.HERE, "i18n.js"), encoding="utf-8") as fh:
            i18n = fh.read()
        for key in ("tab_engine", "eng_title", "eng_warn", "eng_winrate",
                    "eng_winrate_est", "eng_no_trades", "eng_ok",
                    "eng_unconfigured", "eng_unreachable", "eng_rejected"):
            self.assertGreaterEqual(i18n.count(key + ":"), 2,
                                    "%s must be in en and ar" % key)

    def test_a_spent_quota_is_shown_as_its_own_state(self):
        # A 429 from the meter is not "engine down": the renderer must tell them
        # apart, or a user whose daily allowance is spent is told the engine is
        # broken. The distinct status and the reset time must both be present.
        self.assertIn("eng_quota", self.src)
        self.assertIn("err.status === 429", self.src)
        self.assertIn("eng_quota_sub", self.src)

    def test_the_quota_strings_exist_in_both_languages(self):
        with open(os.path.join(sm.HERE, "i18n.js"), encoding="utf-8") as fh:
            i18n = fh.read()
        for key in ("eng_quota", "eng_quota_sub"):
            self.assertGreaterEqual(i18n.count(key + ":"), 2,
                                    "%s must be in en and ar" % key)


if __name__ == "__main__":
    unittest.main()
