#!/usr/bin/env python3
"""Shared harness for the mobile server tests.

The phone server tests start a real `serve_mobile` handler on a loopback port
and talk to it over HTTP, with a stub bridge standing in for the backend. That
harness - the stub bridges, the server start/stop helpers, and the small
fixtures they need - lives here so more than one test module can use it
without duplicating it. Nothing here asserts anything; it is setup only.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface the 302 instead of following it, so the cookie can be read."""
    def redirect_request(self, *a, **k):
        return None


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

    def do_GET(self):
        body = json.dumps({"payload": {"bars": _bars()}}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _BareStubBridge(_StubBridge):
    """A bare body, to prove the route is not coupled to the envelope."""
    def do_GET(self):
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
