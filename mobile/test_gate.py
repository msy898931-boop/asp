#!/usr/bin/env python3
"""Tests for the phone app's access gate: token, rate limit, accounts.

The gate is opt-in (unset `ASTRA_ACCESS_TOKEN` means open) and these tests run
it for real over HTTP against a loopback server, plus the per-address rate
limiter and the multi-user account keys as units. Split out of
test_serve_mobile.py to keep files under the size the project holds to; the
assertions are unchanged. The shared harness is in mobile_test_kit.py.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import serve_mobile as sm  # noqa: E402
from mobile_test_kit import (  # noqa: E402
    _NoRedirect,
    _serve,
    _stop,
    _StubBridge,
    client_js,
)


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

    def test_forwarded_for_keeps_clients_apart_when_trusted(self):
        # Behind Render/Fly every request arrives from the proxy, so — when the
        # operator declares the proxy trusted — the client address comes from
        # X-Forwarded-For, otherwise one attacker would lock out every user.
        saved = sm._TRUST_FORWARDED_FOR
        sm._TRUST_FORWARDED_FOR = True
        sm._GATE_LIMITER.reset()
        try:
            attacker = {"X-Forwarded-For": "203.0.113.9"}
            for i in range(sm._GATE_MAX_FAILURES):
                self._status("/version.json?k=wrong%d" % i, headers=attacker)
            status, _ = self._status("/version.json?k=x", headers=attacker)
            self.assertEqual(status, 429)
            status, _ = self._status("/version.json?k=x",
                                     headers={"X-Forwarded-For": "198.51.100.4"})
            self.assertEqual(status, 401)
        finally:
            sm._TRUST_FORWARDED_FOR = saved

    def test_forwarded_for_is_ignored_by_default(self):
        # XFF is client-supplied. With no trusted proxy declared, a rotated
        # header must NOT buy fresh guesses: the peer address is used and the
        # limiter still trips.
        saved = sm._TRUST_FORWARDED_FOR
        sm._TRUST_FORWARDED_FOR = False
        sm._GATE_LIMITER.reset()
        try:
            codes = []
            for i in range(sm._GATE_MAX_FAILURES + 2):
                codes.append(self._status(
                    "/version.json?k=wrong%d" % i,
                    headers={"X-Forwarded-For": "10.0.0.%d" % i})[0])
            self.assertIn(429, codes)
            self.assertEqual(codes[-1], 429)
        finally:
            sm._TRUST_FORWARDED_FOR = saved

    def test_only_the_rightmost_forwarded_hop_is_used(self):
        # A well-behaved proxy appends the address it saw; the leftmost value is
        # whatever the client sent. Attributing on the rightmost hop means a
        # spoofed left side cannot rotate the identity.
        saved = sm._TRUST_FORWARDED_FOR
        sm._TRUST_FORWARDED_FOR = True
        sm._GATE_LIMITER.reset()
        try:
            real = "203.0.113.77"
            for i in range(sm._GATE_MAX_FAILURES):
                self._status("/version.json?k=wrong%d" % i,
                             headers={"X-Forwarded-For": "1.2.3.%d, %s" % (i, real)})
            # Same real hop, new spoofed prefix: still locked out.
            status, _ = self._status(
                "/version.json?k=x",
                headers={"X-Forwarded-For": "9.9.9.9, %s" % real})
            self.assertEqual(status, 429)
        finally:
            sm._TRUST_FORWARDED_FOR = saved


class AccountsGateTest(unittest.TestCase):
    """Per-user account keys work alongside the static master token.

    These drive the real server with accounts enabled against a real temp
    store, so the whole path — key resolution, the gate, and /me.json — is
    exercised end to end, not mocked.
    """

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("ASTRA_ACCOUNTS", "ASTRA_ACCOUNTS_FILE",
                        "ASTRA_ACCESS_TOKEN")}
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["ASTRA_ACCOUNTS_FILE"] = os.path.join(self._tmp.name,
                                                         "accounts.json")
        os.environ["ASTRA_ACCOUNTS"] = "1"
        sm.ACCESS_TOKEN = ""
        sm._GATE_LIMITER.reset()
        import accounts
        accounts.reset_store()
        self._accounts = accounts

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        sm.ACCESS_TOKEN = ""
        sm._GATE_LIMITER.reset()
        self._accounts.reset_store()
        self._tmp.cleanup()

    def _status(self, path, headers=None):
        app = _serve(sm.Handler)
        try:
            url = "http://127.0.0.1:%d%s" % (app.server_address[1], path)
            req = urllib.request.Request(url, headers=headers or {})
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.status, resp.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read().decode("utf-8")
        finally:
            _stop(app)

    def test_accounts_on_require_a_key(self):
        status, _ = self._status("/version.json")
        self.assertEqual(status, 401)

    def test_a_valid_account_key_opens_the_gate(self):
        created = self._accounts.store().create(email="a@b.c", ttl_days=30)
        status, _ = self._status("/version.json?k=" + created.key)
        self.assertEqual(status, 200)

    def test_a_revoked_account_key_is_refused(self):
        created = self._accounts.store().create(email="a@b.c")
        self._accounts.store().revoke(created.account.id)
        status, _ = self._status("/version.json?k=" + created.key)
        self.assertEqual(status, 401)

    def test_an_expired_account_key_is_refused(self):
        created = self._accounts.store().create(email="a@b.c", ttl_days=1)
        self._accounts.store().extend(created.account.id, -100)
        status, _ = self._status("/version.json?k=" + created.key)
        self.assertEqual(status, 401)

    def test_me_json_reports_the_account_and_its_expiry(self):
        created = self._accounts.store().create(email="a@b.c", ttl_days=30)
        status, body = self._status("/me.json?k=" + created.key)
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["mode"], "account")
        self.assertEqual(payload["id"], created.account.id)
        self.assertEqual(payload["tier"], "pro")
        self.assertIsNotNone(payload["expires_at"])
        self.assertEqual(payload["days_left"], 30)
        # The email is never echoed back.
        self.assertNotIn("a@b.c", body)

    def test_me_json_does_not_leak_the_key(self):
        created = self._accounts.store().create(email="a@b.c")
        _, body = self._status("/me.json?k=" + created.key)
        self.assertNotIn(created.key, body)

    def test_static_token_still_works_as_a_master_key(self):
        # The operator must never be locked out of their own deployment.
        sm.ACCESS_TOKEN = "master-key"
        status, _ = self._status("/version.json?k=master-key")
        self.assertEqual(status, 200)
        status, body = self._status("/me.json?k=master-key")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["mode"], "master")

    def test_unlock_stores_the_presented_account_key_not_the_master(self):
        # A regression guard: baking ACCESS_TOKEN into the cookie would
        # silently upgrade every per-user visitor to the master key.
        sm.ACCESS_TOKEN = "master-key"
        created = self._accounts.store().create(email="a@b.c")
        opener = urllib.request.build_opener(_NoRedirect)
        app = _serve(sm.Handler)
        try:
            url = ("http://127.0.0.1:%d/unlock?k=%s"
                   % (app.server_address[1], created.key))
            try:
                opener.open(url, timeout=10)
                self.fail("expected a 302 redirect")
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, 302)
                cookie = exc.headers.get("Set-Cookie", "")
                self.assertIn(created.key, cookie)
                self.assertNotIn("master-key", cookie)
        finally:
            _stop(app)

    def test_accounts_off_is_the_old_behaviour(self):
        os.environ["ASTRA_ACCOUNTS"] = ""
        self._accounts.reset_store()
        # With no static token either, the server is open as before.
        status, _ = self._status("/version.json")
        self.assertEqual(status, 200)


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
if __name__ == "__main__":
    unittest.main()
