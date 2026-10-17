"""TEL-0104 - the bot over its real transports.

The unit suite (test_astra_bot.py) replaces both the backend client and the
Telegram client with in-process fakes, so it proves the routing and the
formatting but never that `urllib` speaks to a server. A rename in the frozen
contract, a bridge that answers on a different shape, or a Telegram call whose
JSON Telegram rejects would all pass every existing test.

These tests start real loopback servers and let the production clients talk to
them:

  * the backend is `scripts/mock_api.py`'s handler, serving the frozen v1
    contract;
  * the bridge is a two-line handler answering the candle envelope;
  * Telegram is a fake Bot API that records what the bot sent.

Nothing reaches the network. A bot is *not* started against the real Telegram
API: the deployment owns the token, and a second poller would 409 it.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
for _path in (_HERE, os.path.join(_REPO, "scripts")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astra_bot  # noqa: E402
import mock_api  # noqa: E402
from astra_api import AstraApi  # noqa: E402
from astra_bot import CommandRouter, Subscriptions, poll_updates  # noqa: E402


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _quiet(cls):
    """Silence the request log; the tests assert on behaviour, not output."""
    cls.log_message = lambda *a, **k: None
    return cls


@_quiet
class _BackendHandler(mock_api.Handler):
    """The canonical mock backend, wrapped so it runs in a thread."""

    def log_message(self, *args):  # pragma: no cover - logging only
        pass


class _Server:
    """A loopback HTTP server on a free port, stopped on close()."""

    def __init__(self, handler_cls) -> None:
        self._srv = ThreadingHTTPServer(("127.0.0.1", _free_port()), handler_cls)
        self._srv.daemon_threads = True
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()

    def url(self, path: str = "") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


@_quiet
class _BridgeHandler(BaseHTTPRequestHandler):
    """The MT5 bridge's candle envelope, which is not the API envelope."""

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.endswith("/candles"):
            body = {"status": "OK", "payload": {
                "symbol": "XAUUSD",
                "bars": [{"time": 1, "open": 2340.0, "high": 2348.0,
                          "low": 2339.0, "close": 2345.67}]}}
        else:
            body = {"status": "OK", "payload": {}}
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@_quiet
class _FakeTelegramHandler(BaseHTTPRequestHandler):
    """A recording stand-in for api.telegram.org.

    getUpdates hands out the queued messages once; every sendMessage is kept so
    the test can read what the bot actually said. The production client cannot
    tell this apart from Telegram.
    """

    updates: ClassVar[list] = []
    sent: ClassVar[list] = []
    commands: ClassVar[list] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        method = self.path.rsplit("/", 1)[-1]
        if method == "getUpdates":
            result: object = list(type(self).updates)
            type(self).updates.clear()
        elif method == "sendMessage":
            type(self).sent.append(payload)
            result = {"message_id": len(type(self).sent)}
        elif method == "setMyCommands":
            type(self).commands = payload.get("commands", [])
            result = True
        else:
            result = True
        raw = json.dumps({"ok": True, "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class ConnectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with open(mock_api.SCHEMA_PATH, encoding="utf-8") as fh:
            mock_api.Handler.schema = json.load(fh)
        mock_api.Handler.calibrated = False

    def setUp(self) -> None:
        self.backend = _Server(_BackendHandler)
        self.addCleanup(self.backend.close)
        self.bridge = _Server(_BridgeHandler)
        self.addCleanup(self.bridge.close)
        self.api = AstraApi(
            base_url=self.backend.url("/api/v1"),
            bridge_base=self.bridge.url("/v1"),
        )
        self.state = tempfile.mkdtemp(prefix="astra-conn-")
        self.addCleanup(shutil.rmtree, self.state, True)

    def _router(self) -> CommandRouter:
        subs = Subscriptions(Path(self.state) / "subs.json")
        return CommandRouter(self.api, subs, None)

    # -- the real HTTP client over the real frozen contract ----------------

    def test_health_reads_the_live_backend(self):
        health = self.api.health()
        self.assertIn(health.get("aggregate"), ("ONLINE", "DEGRADED", "OFFLINE"))

    def test_analysis_reads_the_frozen_signal_shape(self):
        analysis = self.api.latest_analysis()
        self.assertIn("signal", analysis)
        self.assertIsInstance(analysis["signal"], dict)

    def test_snapshot_tolerates_a_dead_backend(self):
        dead = AstraApi(base_url=f"http://127.0.0.1:{_free_port()}/api/v1",
                        bridge_base=f"http://127.0.0.1:{_free_port()}/v1")
        snap = dead.snapshot()
        for name in ("health", "system", "bridge"):
            self.assertIn("_error", snap[name], f"{name} should report the outage")

    def test_last_price_reads_the_bridge_envelope(self):
        # The bridge's shape (payload.bars) is not the API's shape (data); a
        # client that confused them would silently show no price.
        self.assertEqual(self.api.last_price(), 2345.67)

    def test_a_missing_bridge_is_no_price_not_an_error(self):
        api = AstraApi(base_url=self.backend.url("/api/v1"),
                       bridge_base=f"http://127.0.0.1:{_free_port()}/v1")
        self.assertIsNone(api.last_price())

    # -- the commands, end to end ------------------------------------------

    def test_status_renders_the_live_backend(self):
        reply = self._router().handle("123", "/status")
        self.assertIn("الحالة", reply)
        self.assertIn("ONLINE", reply)
        self.assertIn("وضع الظل", reply)  # the shadow-mode disclaimer

    def test_price_renders_the_live_bridge(self):
        reply = self._router().handle("123", "/price")
        self.assertIn("2,345.67", reply)

    def test_status_degrades_when_the_backend_is_gone(self):
        dead = AstraApi(base_url=f"http://127.0.0.1:{_free_port()}/api/v1",
                        bridge_base=f"http://127.0.0.1:{_free_port()}/v1")
        router = CommandRouter(
            dead, Subscriptions(Path(self.state) / "s.json"), None)
        reply = router.handle("123", "/status")
        self.assertIn("غير متاح", reply)  # reported, not crashed

    # -- the polling loop, over a fake Telegram ----------------------------

    def test_polling_answers_a_message_over_the_real_transport(self):
        telegram = _Server(_FakeTelegramHandler)
        self.addCleanup(telegram.close)
        _FakeTelegramHandler.sent = []
        _FakeTelegramHandler.updates = [
            {"update_id": 1, "message": {"chat": {"id": 123}, "text": "/status"}},
        ]
        client = astra_bot.TelegramClient("test-token")
        client.base = telegram.url("/bottest-token")  # the production client

        astra_bot._stop.clear()
        worker = threading.Thread(
            target=poll_updates, args=(client, self._router()), daemon=True)
        worker.start()
        deadline = time.time() + 5
        while time.time() < deadline and not _FakeTelegramHandler.sent:
            time.sleep(0.02)
        astra_bot._stop.set()
        worker.join(timeout=3)

        self.assertTrue(_FakeTelegramHandler.sent, "the bot sent no reply")
        reply = _FakeTelegramHandler.sent[0]
        self.assertEqual(str(reply["chat_id"]), "123")
        self.assertIn("الحالة", reply["text"])

    def test_command_menu_is_registered(self):
        telegram = _Server(_FakeTelegramHandler)
        self.addCleanup(telegram.close)
        _FakeTelegramHandler.commands = []
        client = astra_bot.TelegramClient("test-token")
        client.base = telegram.url("/bottest-token")
        client.set_my_commands()
        registered = [c["command"] for c in _FakeTelegramHandler.commands]
        # Every command the bot answers must appear in the menu, and none may
        # carry the leading slash: Telegram rejects "/price" (BOT_COMMAND_INVALID)
        # and drops the whole list, leaving the menu empty in the app.
        expected = [c.lstrip("/") for c, _ in astra_bot.BOT_COMMANDS]
        self.assertEqual(registered, expected)
        for name in registered:
            self.assertNotIn("/", name, f"{name!r} would be rejected by Telegram")
            self.assertRegex(name, r"^[a-z0-9_]{1,32}$")


if __name__ == "__main__":
    unittest.main()
