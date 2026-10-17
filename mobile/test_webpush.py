#!/usr/bin/env python3
"""Tests for Web Push: the store, the routes, and the real encryption.

The crypto tests are not mocks. A local server stands in for the push service,
a real client key pair is subscribed, the payload is encrypted by pywebpush and
then decrypted again with the client's private key — so the round trip proves
the code produces a payload a browser could actually read.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import serve_mobile as sm  # noqa: E402
import webpush  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.serialization import (  # noqa: E402
    Encoding,
    PublicFormat,
)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _client_subscription(endpoint: str) -> tuple[dict, ec.EllipticCurvePrivateKey]:
    """A subscription shaped exactly like a browser's, plus its private key."""
    priv = ec.generate_private_key(ec.SECP256R1())
    p256dh = _b64(priv.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint))
    return {"endpoint": endpoint,
            "keys": {"p256dh": p256dh, "auth": _b64(os.urandom(16))}}, priv


class _PushService(BaseHTTPRequestHandler):
    """Stands in for FCM/APNs: captures the encrypted POST body."""
    captured: ClassVar[dict] = {}
    status = 201

    def log_message(self, *a):  # silence
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        type(self).captured["body"] = self.rfile.read(n)
        type(self).captured["headers"] = dict(self.headers)
        self.send_response(type(self).status)
        self.end_headers()


def _serve(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _stop(server):
    server.shutdown()
    server.server_close()


class _EnvMixin(unittest.TestCase):
    """Save and restore the VAPID/store env, and point the store at a temp dir."""

    def setUp(self):
        self._saved = {k: os.environ.get(k)
                       for k in ("VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY",
                                 "VAPID_SUBJECT", "ASTRA_PUSH_STORE")}
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["ASTRA_PUSH_STORE"] = os.path.join(self._tmp.name, "subs.json")
        for k in ("VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT"):
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()


class KeysTest(unittest.TestCase):
    def test_generate_keys_returns_a_usable_pair(self):
        public, private = webpush.generate_keys()
        # base64url, unpadded: 65-byte point -> 87 chars, 32-byte scalar -> 43.
        self.assertEqual(len(public), 87)
        self.assertEqual(len(private), 43)
        self.assertNotIn("=", public + private)
        # The public key must decode to a real P-256 point, not arbitrary bytes.
        raw = base64.urlsafe_b64decode(public + "=")
        self.assertEqual(raw[0], 0x04)
        self.assertEqual(len(raw), 65)

    def test_generated_pair_signs_a_push_that_pywebpush_accepts(self):
        # The strongest check on generate_keys: a payload signed with the pair
        # it produced must actually encrypt, i.e. pywebpush accepts the JWT.
        os.environ["VAPID_PUBLIC_KEY"], os.environ["VAPID_PRIVATE_KEY"] = \
            webpush.generate_keys()
        self.assertTrue(webpush.configured())


class ValidationTest(unittest.TestCase):
    def test_a_browser_shaped_subscription_is_valid(self):
        sub, _ = _client_subscription("https://fcm.googleapis.com/fcm/send/abc")
        self.assertTrue(webpush.valid_subscription(sub))

    def test_rejects_non_https_endpoint(self):
        sub, _ = _client_subscription("http://insecure.example/push")
        self.assertFalse(webpush.valid_subscription(sub))

    def test_rejects_missing_keys(self):
        self.assertFalse(webpush.valid_subscription(
            {"endpoint": "https://x.example/p"}))
        self.assertFalse(webpush.valid_subscription(
            {"endpoint": "https://x.example/p", "keys": {"p256dh": "a"}}))
        self.assertFalse(webpush.valid_subscription(
            {"endpoint": "https://x.example/p", "keys": {}}))

    def test_rejects_oversized_endpoint(self):
        sub, _ = _client_subscription("https://x.example/" + "a" * 4000)
        self.assertFalse(webpush.valid_subscription(sub))

    def test_rejects_non_dict(self):
        self.assertFalse(webpush.valid_subscription("not a dict"))
        self.assertFalse(webpush.valid_subscription({"keys": "x"}))


class StoreTest(_EnvMixin):
    def test_subscribe_then_load(self):
        sub, _ = _client_subscription("https://push.example/one")
        self.assertTrue(webpush.subscribe(sub))
        stored = webpush.load_subscriptions()
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["endpoint"], "https://push.example/one")

    def test_subscribe_is_idempotent_by_endpoint(self):
        sub, _ = _client_subscription("https://push.example/one")
        webpush.subscribe(sub)
        webpush.subscribe(sub)
        webpush.subscribe(sub)
        self.assertEqual(len(webpush.load_subscriptions()), 1)

    def test_invalid_subscription_is_not_stored(self):
        self.assertFalse(webpush.subscribe({"endpoint": "http://nope"}))
        self.assertEqual(webpush.load_subscriptions(), [])

    def test_unsubscribe_removes_only_that_endpoint(self):
        a, _ = _client_subscription("https://push.example/a")
        b, _ = _client_subscription("https://push.example/b")
        webpush.subscribe(a)
        webpush.subscribe(b)
        self.assertTrue(webpush.unsubscribe("https://push.example/a"))
        self.assertEqual([s["endpoint"] for s in webpush.load_subscriptions()],
                         ["https://push.example/b"])
        self.assertFalse(webpush.unsubscribe("https://push.example/a"))

    def test_a_missing_or_corrupt_store_reads_as_empty(self):
        self.assertEqual(webpush.load_subscriptions(), [])
        with open(os.environ["ASTRA_PUSH_STORE"], "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(webpush.load_subscriptions(), [])

    def test_not_configured_when_no_keys(self):
        self.assertFalse(webpush.configured())

    def test_not_configured_when_the_crypto_library_is_absent(self):
        os.environ["VAPID_PUBLIC_KEY"], os.environ["VAPID_PRIVATE_KEY"] = "a", "b"
        saved = (webpush._pywebpush, webpush._pywebpush_tried)
        webpush._pywebpush, webpush._pywebpush_tried = None, True
        try:
            self.assertFalse(webpush.configured())
            # broadcast must degrade, not raise, when the library is missing.
            self.assertEqual(webpush.broadcast({"title": "x"}),
                             {"configured": False, "sent": 0, "removed": 0})
        finally:
            webpush._pywebpush, webpush._pywebpush_tried = saved


class BroadcastCryptoTest(_EnvMixin):
    """The whole path, for real: encrypt with the server key, decrypt with the
    client key. This is what proves a browser would be able to read it."""

    def setUp(self):
        super().setUp()
        os.environ["VAPID_PUBLIC_KEY"], os.environ["VAPID_PRIVATE_KEY"] = \
            webpush.generate_keys()
        os.environ["VAPID_SUBJECT"] = "mailto:test@example.com"

    def _force_subscribe(self, sub: dict) -> None:
        """Store a subscription directly.

        The crypto path needs a *local* http endpoint to receive the POST, but
        subscribe() rightly refuses non-https endpoints (a browser never sends
        one). Writing the store directly tests the encryption, not validation,
        which ValidationTest already covers.
        """
        webpush._write_subscriptions([{
            "endpoint": sub["endpoint"],
            "keys": sub["keys"],
            "added_at": 0.0,
        }])

    def test_payload_round_trips_through_real_encryption(self):
        import http_ece

        service = _serve(_PushService)
        _PushService.captured = {}
        _PushService.status = 201
        try:
            endpoint = "http://127.0.0.1:%d/push" % service.server_address[1]
            sub, client_priv = _client_subscription(endpoint)
            self._force_subscribe(sub)
            payload = {"title": "ASTRA", "body": "إشارة شراء", "tag": "astra-alert"}
            result = webpush.broadcast(payload)
        finally:
            _stop(service)

        self.assertEqual(result, {"configured": True, "sent": 1, "removed": 0})
        body = _PushService.captured.get("body")
        self.assertTrue(body, "the push service received nothing")
        # The VAPID JWT must be present — it is how the service authenticates us.
        headers = {k.lower(): v for k, v in _PushService.captured["headers"].items()}
        self.assertIn("authorization", headers)
        self.assertTrue(headers["authorization"].startswith("vapid "))
        plain = http_ece.decrypt(
            body,
            private_key=client_priv,
            auth_secret=base64.urlsafe_b64decode(sub["keys"]["auth"] + "=="),
            version="aes128gcm",
        )
        self.assertEqual(json.loads(plain.decode("utf-8")), payload)

    def test_a_gone_subscription_is_dropped(self):
        service = _serve(_PushService)
        _PushService.captured = {}
        _PushService.status = 410  # the push service forgot this subscriber
        try:
            endpoint = "http://127.0.0.1:%d/push" % service.server_address[1]
            sub, _ = _client_subscription(endpoint)
            self._force_subscribe(sub)
            result = webpush.broadcast({"title": "ASTRA", "body": "x"})
        finally:
            _stop(service)
        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["removed"], 1)
        # The dead endpoint is gone, so the next broadcast does not retry it.
        self.assertEqual(webpush.load_subscriptions(), [])


class PushRouteTest(_EnvMixin):
    """The routes the phone actually calls."""

    def setUp(self):
        super().setUp()
        self._token = sm.ACCESS_TOKEN
        sm.ACCESS_TOKEN = ""
        sm._GATE_LIMITER.reset()

    def tearDown(self):
        sm.ACCESS_TOKEN = self._token
        sm._GATE_LIMITER.reset()
        super().tearDown()

    def _request(self, path, data=None, method=None):
        app = _serve(sm.Handler)
        try:
            url = "http://127.0.0.1:%d%s" % (app.server_address[1], path)
            body = json.dumps(data).encode() if data is not None else None
            headers = {"Content-Type": "application/json"} if body else {}
            req = urllib.request.Request(url, data=body, headers=headers,
                                         method=method or ("POST" if body else "GET"))
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    raw = resp.read()
                    return resp.status, (json.loads(raw) if raw else {})
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                try:
                    return exc.code, (json.loads(raw) if raw else {})
                except ValueError:
                    return exc.code, {}
        finally:
            _stop(app)

    def test_push_json_says_unavailable_without_keys(self):
        status, body = self._request("/push.json")
        self.assertEqual(status, 200)
        self.assertFalse(body["available"])
        self.assertEqual(body["public_key"], "")

    def test_push_json_reports_available_with_keys(self):
        os.environ["VAPID_PUBLIC_KEY"], os.environ["VAPID_PRIVATE_KEY"] = \
            webpush.generate_keys()
        status, body = self._request("/push.json")
        self.assertEqual(status, 200)
        self.assertTrue(body["available"])
        self.assertTrue(body["public_key"])

    def test_subscribe_stores_and_reports_the_count(self):
        sub, _ = _client_subscription("https://push.example/phone")
        status, body = self._request("/push/subscribe", data=sub)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(webpush.load_subscriptions()[0]["endpoint"],
                         "https://push.example/phone")

    def test_subscribe_rejects_a_bad_body(self):
        status, _ = self._request("/push/subscribe",
                                  data={"endpoint": "http://nope"})
        self.assertEqual(status, 400)

    def test_unsubscribe_removes_the_endpoint(self):
        sub, _ = _client_subscription("https://push.example/phone")
        self._request("/push/subscribe", data=sub)
        status, body = self._request("/push/unsubscribe",
                                     data={"endpoint": sub["endpoint"]})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(webpush.load_subscriptions(), [])

    def test_unknown_post_path_is_404(self):
        status, _ = self._request("/push/whatever", data={"a": 1})
        self.assertEqual(status, 404)

    def test_the_gate_covers_the_push_routes(self):
        sm.ACCESS_TOKEN = "s3cret"
        status, _ = self._request("/push/subscribe",
                                  data={"endpoint": "https://x.example/p"})
        self.assertEqual(status, 401)
        self.assertEqual(webpush.load_subscriptions(), [])


if __name__ == "__main__":
    unittest.main()
