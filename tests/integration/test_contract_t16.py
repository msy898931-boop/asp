#!/usr/bin/env python3
"""T16/T17 - Frozen contract vs the real implementation.

Starts the actual backend host (build/aura_backend_host), requests every route
in the frozen contract docs/architecture/API_V1_SCHEMA.json, and validates each
live response against the schema. Also asserts the RULE C posture on this build
(the synthetic T11 audit does not authorise publication, so /analysis/latest
must report probability=null).

This is the "schema matches the implementation exactly" check the T17 freeze
requires. It reads the real binary; it never mutates anything.

Exit 0 = contract and implementation agree. Skips (exit 0 with a notice) when the
host has not been built in this checkout.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "scripts"))
sys.path.insert(0, os.path.join(REPO, "src", "models"))
import contract_checker  # noqa: E402  (T23, Agent-B: shared frozen-set reader)
import mock_api  # noqa: E402

HOST_BIN = os.path.join(REPO, "build", "aura_backend_host")
SCHEMA_PATH = mock_api.SCHEMA_PATH
SEMANTIC_DIR = os.path.join(REPO, "tests", "fixtures", "api_v1", "semantic")

# Divergences between the frozen contract and the current binary that are already
# reported to the Lead/auditor. They are counted separately (not as PASS, not as
# a new FAIL) so the suite stays honest and green while the defect is tracked.
KNOWN_DEFECTS: dict = {}

_results = []
_known = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok and name in KNOWN_DEFECTS:
        _known.append(name)
        print(f"[KNOWN] {name} - {KNOWN_DEFECTS[name]}")
        return
    _results.append(ok)
    print(("[PASS] " if ok else "[FAIL] ") + name + (f" - {detail}" if detail and not ok else ""))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fetch(url: str, timeout: float = 5.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def wait_for(url: str, attempts: int = 50):
    last = None
    for _ in range(attempts):
        try:
            return fetch(url)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.1)
    raise RuntimeError(f"backend host did not serve: {last}")


def main() -> int:
    if not os.path.exists(HOST_BIN):
        print(f"[SKIP] {HOST_BIN} not built; run `cmake --build build` first")
        return 0

    with open(SCHEMA_PATH, "r", encoding="utf-8") as handle:
        schema = json.load(handle)

    port = free_port()
    proc = subprocess.Popen(
        [HOST_BIN, "--api-port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        wait_for(base + "/api/v1/system/state")
        for route, spec in schema["endpoints"].items():
            method, _, path = route.partition(" ")
            assert method == "GET", route
            live_path = path.replace("{tf}", "M15")
            try:
                status, body = fetch(base + live_path)
            except urllib.error.HTTPError as exc:
                check(f"{route} reachable", False, f"HTTP {exc.code}")
                continue
            check(f"{route} status 200", status == 200, str(status))
            try:
                mock_api.validate_envelope(route, spec, body)
                check(f"{route} matches schema", True)
            except mock_api.SchemaError as exc:
                check(f"{route} matches schema", False, str(exc))

        # RULE C on this build: synthetic audit => probability stays null.
        _, latest = fetch(base + "/api/v1/analysis/latest")
        signal = latest["data"]["signal"]
        check("analysis probability null (RULE C, synthetic audit)",
              signal["probability"] is None,
              str(signal["probability"]))
        check("analysis probability_calibrated false",
              signal["probability_calibrated"] is False)
        check("analysis score_is_probability false",
              latest["data"]["meta"]["score_is_probability"] is False)

        # F17-1: the live analysis payload must also satisfy the SEMANTIC layer
        # (the frozen-null invariants), read via Agent-B's shared T23 checker,
        # not a re-implementation of the frozen set.
        violations = contract_checker.analysis_contract_violations(latest, schema)
        check("analysis/latest passes semantic invariants", not violations,
              "; ".join(violations))

        # ...and the checker must have teeth: each semantic fixture is
        # structurally valid yet must be reported as an invariant violation.
        for name in sorted(os.listdir(SEMANTIC_DIR)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(SEMANTIC_DIR, name), "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            found = contract_checker.analysis_contract_violations(payload, schema)
            check(f"semantic fixture rejected: {name}", bool(found))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    passed = sum(1 for ok in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} checks passed"
          + (f"; {len(_known)} known defect(s) tracked" if _known else ""))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
