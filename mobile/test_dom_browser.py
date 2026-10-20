"""Browser-level (DOM) tests for the phone app.

Every other test of the phone app reads the source as text and asserts on
substrings (`assertIn('href="styles.css"', html)`). That catches a missing
asset but not a broken control: a tab whose click handler was never wired, a
`selectView` that hides the panel it should show, a language toggle that flips
the wrong way — all of those pass a string test and fail a user.

These tests drive the real `index.html` in a real Chromium through Playwright,
served by the real `serve_mobile.Handler`, so the shell, the stylesheet and
`app.js` run exactly as they do on a phone. Network calls to the backend are
aborted on purpose: the app is designed to survive a dead backend (it falls
back to the last reading), so the UI must be fully navigable with no data.

The suite is skipped, not failed, when Playwright is absent, so a plain
`unittest discover` on a machine without the browser still runs green. CI
installs it in the frontend job.
"""

from __future__ import annotations

import os
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import serve_mobile as sm  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
    HAVE_PLAYWRIGHT = True
except Exception:  # noqa: BLE001 - absent browser must skip, never fail
    HAVE_PLAYWRIGHT = False

# Paths whose responses the backend would own. Aborting them keeps the tests
# off the network and pins the app's no-data behaviour.
LIVE_PREFIXES = ("/api/", "/bridge/", "/advisor/", "/tracker/", "/engine/", "/push")

PRIMARY_VIEWS = ["status", "signal", "chart", "vol", "akoan"]
SECONDARY_VIEWS = ["tracker", "ai", "engine", "conn", "bridge"]


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # An aborted request (the client walked away mid-response) is expected
        # here: the tests abort the live endpoints on purpose. Keep it off the
        # test output.
        pass


def _serve():
    server = _QuietServer(("127.0.0.1", 0), sm.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@unittest.skipUnless(HAVE_PLAYWRIGHT, "playwright is not installed")
class DomBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_log = sm.Handler.log_message
        sm.Handler.log_message = lambda *a, **k: None  # quiet the request log
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

    def _open(self):
        """A fresh page on the real shell, with the backend unreachable."""
        page = self.browser.new_page(viewport={"width": 390, "height": 844})
        page.route(
            "**/*",
            lambda route: route.abort()
            if any(p in route.request.url for p in LIVE_PREFIXES)
            else route.continue_(),
        )
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(self.base + "/", wait_until="load")
        page.wait_for_timeout(300)  # let init() run
        return page, errors

    # -- the shell ---------------------------------------------------------

    def test_shell_renders_and_one_panel_is_visible(self):
        page, _ = self._open()
        try:
            self.assertIn("ASTRA", page.title())
            visible = page.eval_on_selector_all(
                ".panel", "els => els.filter(e => !e.hidden).map(e => e.id)")
            self.assertEqual(visible, ["view-status"],
                             "exactly the status panel should start visible")
        finally:
            page.close()

    def test_no_uncaught_error_on_a_dead_backend(self):
        # The app is built to survive an unreachable backend; a page error here
        # means init() crashed on the phone when the host was asleep.
        page, errors = self._open()
        try:
            self.assertEqual(errors, [], "the shell threw while loading offline")
        finally:
            page.close()

    # -- tabs --------------------------------------------------------------

    def test_each_primary_tab_switches_the_panel(self):
        page, _ = self._open()
        try:
            for view in PRIMARY_VIEWS:
                page.click(f'.tab[data-view="{view}"]')
                page.wait_for_timeout(60)
                shown = page.eval_on_selector_all(
                    ".panel", "els => els.filter(e => !e.hidden).map(e => e.id)")
                self.assertEqual(shown, [f"view-{view}"],
                                 f"clicking the {view} tab showed {shown}")
        finally:
            page.close()

    def test_the_selected_tab_is_marked_for_screen_readers(self):
        page, _ = self._open()
        try:
            page.click('.tab[data-view="vol"]')
            page.wait_for_timeout(60)
            selected = page.eval_on_selector_all(
                '.tab[aria-selected="true"]',
                "els => els.map(e => e.dataset.view)")
            self.assertIn("vol", selected)
        finally:
            page.close()

    def test_secondary_views_open_from_the_more_sheet(self):
        page, _ = self._open()
        try:
            page.click(".tab-more")
            page.wait_for_timeout(60)
            self.assertFalse(page.eval_on_selector("#tabs-more", "e => e.hidden"),
                             "More did not open its sheet")
            for view in SECONDARY_VIEWS:
                page.click(f'#tabs-more .tab[data-view="{view}"]')
                page.wait_for_timeout(60)
                shown = page.eval_on_selector_all(
                    ".panel", "els => els.filter(e => !e.hidden).map(e => e.id)")
                self.assertEqual(shown, [f"view-{view}"])
                if view != SECONDARY_VIEWS[-1]:
                    page.click(".tab-more")
                    page.wait_for_timeout(60)
        finally:
            page.close()

    def test_tapping_outside_closes_the_more_sheet(self):
        page, _ = self._open()
        try:
            page.click(".tab-more")
            page.wait_for_timeout(60)
            self.assertFalse(page.eval_on_selector("#tabs-more", "e => e.hidden"))
            page.click("#view-status", position={"x": 5, "y": 5})
            page.wait_for_timeout(60)
            self.assertTrue(page.eval_on_selector("#tabs-more", "e => e.hidden"),
                            "the sheet stayed open after a tap outside it")
        finally:
            page.close()

    # -- controls ----------------------------------------------------------

    def test_language_toggle_flips_the_direction(self):
        page, _ = self._open()
        try:
            before = page.eval_on_selector("html", "e => e.getAttribute('dir')")
            page.click("#lang")
            page.wait_for_timeout(80)
            after = page.eval_on_selector("html", "e => e.getAttribute('dir')")
            self.assertNotEqual(before, after, "the direction did not change")
            self.assertEqual({before, after}, {"rtl", "ltr"})
        finally:
            page.close()

    def test_theme_toggle_flips_the_attribute(self):
        page, _ = self._open()
        try:
            before = page.eval_on_selector(
                "html", "e => e.getAttribute('data-theme')")
            page.click("#theme")
            page.wait_for_timeout(80)
            after = page.eval_on_selector(
                "html", "e => e.getAttribute('data-theme')")
            self.assertNotEqual(before, after, "the theme did not change")
        finally:
            page.close()

    def test_refresh_button_is_wired_and_does_not_throw(self):
        page, errors = self._open()
        try:
            page.click("#refresh")
            page.wait_for_timeout(200)
            self.assertEqual(errors, [], "refresh threw on a dead backend")
            # It must also not leave the button stuck in its busy state.
            self.assertFalse(page.eval_on_selector("#refresh", "e => e.disabled"))
        finally:
            page.close()


if __name__ == "__main__":
    unittest.main()
