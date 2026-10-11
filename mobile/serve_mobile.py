#!/usr/bin/env python3
"""ASTRA mobile launcher: serve the PWA and reverse-proxy the backend API.

The phone can only reach the machine's public port, while the backend binds to
loopback. This tiny standard-library server bridges the two:

    GET /                -> mobile/index.html (the app shell)
    GET /api/v1/<...>    -> http://127.0.0.1:<api-port>/api/v1/<...>
    GET /bridge/<...>    -> http://127.0.0.1:<bridge-port>/<...>

It is read-only by construction: only GET/HEAD are proxied, and the upstream is
fixed to loopback, so the app cannot be used to reach anything else.

Run: python3 mobile/serve_mobile.py --port 12000
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))

# Where /astra.apk points when no local build is staged: the rolling release
# the Android workflow republishes on every mobile/android change.
APK_FALLBACK_URL = (
    "https://github.com/msy898931-boop/asp/releases/download/android/astra.apk"
)

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}

_PROXY_TIMEOUT = 6.0


class Handler(BaseHTTPRequestHandler):
    server_version = "AstraMobile/1.0"

    # Injected by main().
    api_base = "http://127.0.0.1:8790/api/v1"
    bridge_base = "http://127.0.0.1:8791"
    apk_path = ""

    def log_message(self, fmt, *args):  # quieter logs
        sys.stderr.write("[mobile] %s\n" % (fmt % args))

    # -- routing ------------------------------------------------------------

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/astra.apk":
            self._apk()
        elif path == "/upgrade.json":
            self._upgrade_json()
        elif path.startswith("/api/v1/"):
            self._proxy(self.api_base + path[len("/api/v1"):], self.path)
        elif path.startswith("/bridge/"):
            self._proxy(self.bridge_base + path[len("/bridge"):], self.path)
        else:
            self._static(path)

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    # -- static shell -------------------------------------------------------

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = os.path.normpath(os.path.join(HERE, rel))
        if not target.startswith(HERE) or not os.path.isfile(target):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        ext = os.path.splitext(target)[1].lower()
        with open(target, "rb") as fh:
            body = fh.read()
        ctype = _CONTENT_TYPES.get(ext, "application/octet-stream")
        self._send(200, body, ctype)

    # -- upgrade link -------------------------------------------------------

    def _upgrade_json(self) -> None:
        """Expose the configured upgrade URL to the pricing page.

        The URL is not a secret, so serving it as JSON keeps the page static
        while letting one env var (ASTRA_UPGRADE_URL) drive the buy button.
        """
        url = os.environ.get("ASTRA_UPGRADE_URL", "").strip()
        body = json.dumps({"url": url}).encode("utf-8")
        self._send(200, body, "application/json; charset=utf-8")

    # -- APK download -------------------------------------------------------

    def _apk(self) -> None:
        if not self.apk_path or not os.path.isfile(self.apk_path):
            # No local copy staged. Point at the rolling release rather than
            # 404, so a link to /astra.apk always downloads something.
            self.send_response(302)
            self.send_header("Location", APK_FALLBACK_URL)
            self.end_headers()
            return
        with open(self.apk_path, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type",
                         "application/vnd.android.package-archive")
        self.send_header("Content-Disposition",
                         'attachment; filename="astra.apk"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # -- reverse proxy ------------------------------------------------------

    def _proxy(self, url: str, original_path: str) -> None:
        query = original_path.split("?", 1)[1] if "?" in original_path else ""
        full = url + ("?" + query if query else "")
        req = urllib.request.Request(full, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=_PROXY_TIMEOUT) as resp:
                body = resp.read()
                ctype = resp.headers.get("Content-Type",
                                         "application/json; charset=utf-8")
                self._send(resp.status, body, ctype)
        except urllib.error.HTTPError as exc:
            body = exc.read() if exc.fp else b"upstream error"
            self._send(exc.code, body,
                       exc.headers.get("Content-Type", "application/json"))
        except (urllib.error.URLError, OSError) as exc:
            self._send(503, b'{"error":"backend unreachable"}',
                       "application/json; charset=utf-8")
            self.log_message("proxy %s -> %s", full, exc)

    # -- writer -------------------------------------------------------------

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if ctype.startswith("application/json"):
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="ASTRA mobile PWA server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=12000)
    parser.add_argument("--api-port", type=int, default=8790)
    parser.add_argument("--bridge-port", type=int, default=8791)
    parser.add_argument("--apk", default=os.path.join(
        os.path.dirname(HERE), "android", "dist", "astra.apk"),
        help="APK to offer at /astra.apk (optional)")
    args = parser.parse_args(argv)

    Handler.api_base = f"http://127.0.0.1:{args.api_port}/api/v1"
    Handler.bridge_base = f"http://127.0.0.1:{args.bridge_port}"
    Handler.apk_path = os.path.abspath(args.apk)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f"[OK] ASTRA mobile app on http://{args.host}:{args.port} "
          f"(api -> {Handler.api_base})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
