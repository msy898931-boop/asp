#!/usr/bin/env python3
"""T19 - Mock API server: contract validation + live serving (no MT5 required).

Checks:
  1. every synthetic payload validates against the frozen schema, in both the
     uncalibrated and calibrated modes;
  2. the live server actually serves the frozen contract over loopback;
  3. RULE C honesty: the uncalibrated mode never presents a value as a
     probability (probability null, probability_calibrated false);
  4. the server refuses a non-loopback bind.

Exit code 0 = all checks passed.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import mock_api  # noqa: E402

_results = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok, detail))
    print(("[PASS] " if ok else "[FAIL] ") + name + (f" - {detail}" if detail and not ok else ""))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fetch(url: str, timeout: float = 3.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def main() -> int:
    with open(mock_api.SCHEMA_PATH, "r", encoding="utf-8") as handle:
        schema = json.load(handle)

    # 1. Every payload validates in both modes.
    for calibrated in (False, True):
        payloads = mock_api.build_payloads(schema, calibrated)
        for route, payload in payloads.items():
            spec = schema["endpoints"][route]
            try:
                mock_api.validate_envelope(route, spec, payload)
            except mock_api.SchemaError as exc:
                check(f"schema {route} calibrated={calibrated}", False, str(exc))
            else:
                check(f"schema {route} calibrated={calibrated}", True)

    # 2/3. Live server, uncalibrated mode: RULE C honesty.
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, os.path.join(REPO, "scripts", "mock_api.py"),
         "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        base = f"http://127.0.0.1:{port}/api/v1"
        status, body = _wait_for(base + "/analysis/latest")
        check("live status 200", status == 200, str(status))
        check("live envelope api=v1", body.get("api") == "v1")
        signal = body["data"]["signal"]
        check("uncalibrated probability is null", signal["probability"] is None)
        check("uncalibrated flag false", signal["probability_calibrated"] is False)
        check("uncalibrated score_is_probability false",
              body["data"]["meta"]["score_is_probability"] is False)
        try:
            mock_api.validate_envelope(
                "GET /api/v1/analysis/latest",
                schema["endpoints"]["GET /api/v1/analysis/latest"], body)
            check("live payload validates", True)
        except mock_api.SchemaError as exc:
            check("live payload validates", False, str(exc))

        # history respects the limit
        _, hist = fetch(base + "/analysis/history?limit=2")
        check("history limit honoured", len(hist["data"]) == 2,
              str(len(hist["data"])))

        # 404 for an unknown route
        try:
            fetch(base + "/nope")
            check("unknown route is 404", False, "no error raised")
        except urllib.error.HTTPError as exc:
            check("unknown route is 404", exc.code == 404, str(exc.code))
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    # 4. Non-loopback bind is refused.
    refused = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "mock_api.py"),
         "--host", "0.0.0.0", "--check"],
        capture_output=True, text=True,
    )
    check("non-loopback host refused", refused.returncode == 2,
          str(refused.returncode))

    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


def _wait_for(url: str, attempts: int = 30):
    last = None
    for _ in range(attempts):
        try:
            return fetch(url)
        except Exception as exc:  # noqa: BLE001 - server not up yet
            last = exc
            time.sleep(0.1)
    raise RuntimeError(f"mock server did not start: {last}")


if __name__ == "__main__":
    sys.exit(main())
