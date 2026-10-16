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
