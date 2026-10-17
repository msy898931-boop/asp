#!/usr/bin/env python3
"""T24 - T13 end-to-end harness against the FROZEN v1 contract.

Drives the canonical synthetic backend (scripts/mock_api.py, the T19 generator
that serves docs/architecture/API_V1_SCHEMA.json) over loopback HTTP and checks
the whole surface end to end:

  1. the live server answers every route in the frozen schema with HTTP 200 and
     a response that validates against the schema via Agent-B's shared T23
     reader (src/models/contract_checker.py) - the same reader Agent-C's F17-1
     check and Agent-D's audit consume (one frozen-set reader, not three);
  2. RULE C / E06 / E07 hold on /analysis/latest: probability null,
     probability_calibrated false, score_is_probability false, and
     analysis_contract_violations() returns clean;
  3. every valid/ fixture is structurally consistent with the live mock of its
     route (identical key-path skeleton) - this is where the T22 corpus and the
     live backend meet;
  4. every semantic/ fixture is structurally schema-valid yet rejected by the
     shared checker (the checker has teeth);
  5. every invalid/ fixture is still rejected by the frozen reader;
  6. errors/ fixtures match the frozen error_schema, and the mock's own 404/405
     responses carry the same error shape;
  7. the calibrated branch serves too and still satisfies the frozen nulls.

Exit 0 = the frozen v1 contract holds end to end on the synthetic path.

The real-data PASS is gated on E05 (no real XAUUSD dataset exists); this
harness exercises the synthetic/mock path fully, exactly as the Lead's cycle 29
ruling allows. It never touches the baseline or production code.

Scope note (F24-1): this harness drives the *mock* backend. The *production*
host binary's conformance to the same frozen schema is covered separately by
tests/integration/test_contract_t16.py (real socket, real build/aura_backend_host),
so a change to the host alone would not be caught here - the two scripts are
deliberately complementary.
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
FIXTURES = os.path.join(REPO, "tests", "fixtures", "api_v1")
sys.path.insert(0, os.path.join(REPO, "scripts"))
sys.path.insert(0, os.path.join(REPO, "src", "models"))
import contract_checker  # noqa: E402
import mock_api  # noqa: E402

MOCK = os.path.join(REPO, "scripts", "mock_api.py")

# Fixture -> schema route. Files not listed (analysis_latest_calibrated) are the
# calibrated branch of analysis/latest and are handled separately.
VALID_ROUTES = {
    "system_state.json": "GET /api/v1/system/state",
    "health.json": "GET /api/v1/health",
    "health_v1.json": "GET /api/v1/health/v1",
    "timeframes.json": "GET /api/v1/timeframes",
    "timeframe_snapshot.json": "GET /api/v1/timeframes/{tf}/snapshot",
    "analysis_latest.json": "GET /api/v1/analysis/latest",
    "analysis_history.json": "GET /api/v1/analysis/history",
    "context_latest.json": "GET /api/v1/context/latest",
    "bridge_status.json": "GET /api/v1/bridge/status",
    "risk_latest.json": "GET /api/v1/risk/latest",
    "shadow_positions.json": "GET /api/v1/shadow/positions",
    "shadow_outcomes.json": "GET /api/v1/shadow/outcomes",
    "research_status.json": "GET /api/v1/research/status",
    "governance_status.json": "GET /api/v1/governance/status",
    "audit_recent.json": "GET /api/v1/audit/recent",
}

INVALID_ROUTES = {
    "missing_levels_field.json": "GET /api/v1/analysis/latest",
    "bad_coverage_tier_const.json": "GET /api/v1/analysis/latest",
    "probability_above_one.json": "GET /api/v1/analysis/latest",
    "probability_below_zero.json": "GET /api/v1/analysis/latest",
    "not_enveloped.json": "GET /api/v1/analysis/latest",
    "missing_envelope_schema_key.json": "GET /api/v1/analysis/latest",
    "wrong_envelope_api_const.json": "GET /api/v1/analysis/latest",
}

SEMANTIC_FIXTURES = ("uncalibrated_claims_probability.json",
                     "uncalibrated_non_null_levels.json")

# List routes: the fixture carries representative element(s), so the live payload
# is compared element-skeleton-wise. FULL_LIST routes additionally require equal
# length (the fixture stands for the complete response).
FULL_LIST_ROUTES = {"/api/v1/timeframes"}

_results = []


def check(name, ok, detail=""):
    _results.append(ok)
    print(("[PASS] " if ok else "[FAIL] ") + name + ("" if ok else f" - {detail}"))


def load(rel):
    with open(os.path.join(FIXTURES, rel), "r", encoding="utf-8") as handle:
        return json.load(handle)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fetch(url, timeout=5.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        return response.status, response.headers.get("Content-Type", ""), json.loads(raw)


def wait_for(base, attempts=50):
    last = None
    for _ in range(attempts):
        try:
            return fetch(base + "/api/v1/system/state")
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.1)
    raise RuntimeError(f"mock did not serve: {last}")


def fetch_req(req, timeout=5.0):
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.status, response.headers.get("Content-Type", ""), json.loads(
            response.read().decode("utf-8")
        )


class Server:
    def __init__(self, calibrated=False):
        self.calibrated = calibrated
        self.port = free_port()
        self.proc = None

    def __enter__(self):
        argv = [sys.executable, MOCK, "--port", str(self.port)]
        if self.calibrated:
            argv.append("--calibrated")
        self.proc = subprocess.Popen(
            argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        wait_for(self.base)
        return self

    def __exit__(self, *exc):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"


def keypaths(obj, prefix="data"):
    """Set of structural key paths (keys + nested keys, ``[]`` for array item)."""
    out = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            out.add(f"{prefix}.{key}")
            out |= keypaths(value, f"{prefix}.{key}")
    elif isinstance(obj, list) and obj:
        out |= keypaths(obj[0], f"{prefix}[]")
    return out


def main() -> int:
    with open(mock_api.SCHEMA_PATH, "r", encoding="utf-8") as handle:
        schema = json.load(handle)
    endpoints = schema["endpoints"]

    with Server() as server:
        # 1. Every frozen route: 200 + schema-valid via the shared reader.
        for route, spec in endpoints.items():
            method, _, path = route.partition(" ")
            assert method == "GET", route
            live_path = path.replace("{tf}", "M15")
            try:
                status, ctype, body = fetch(server.base + live_path)
            except urllib.error.HTTPError as exc:
                check(f"e2e {route} reachable", False, f"HTTP {exc.code}")
                continue
            check(f"e2e {route} status 200", status == 200, str(status))
            check(f"e2e {route} is application/json", "json" in ctype, ctype)
            try:
                contract_checker.validate_envelope(route, spec, body)
                check(f"e2e {route} validates vs frozen schema", True)
            except contract_checker.ContractError as exc:
                check(f"e2e {route} validates vs frozen schema", False, str(exc))

        # 2. RULE C / E06 / E07 on the live default analysis surface.
        _, _, latest = fetch(server.base + "/api/v1/analysis/latest")
        signal = latest["data"]["signal"]
        check("e2e analysis probability null (RULE C)",
              signal["probability"] is None, str(signal["probability"]))
        check("e2e analysis probability_calibrated false",
              signal["probability_calibrated"] is False)
        check("e2e analysis score_is_probability false (E07)",
              latest["data"]["meta"]["score_is_probability"] is False)
        violations = contract_checker.analysis_contract_violations(latest, schema)
        check("e2e analysis/latest passes E06 invariants", not violations,
              "; ".join(violations))

        # 3. Fixture <-> live mock structural consistency (fixture corpus meets
        #    the live backend). Structural only: values are synthetic/temporal.
        for name, route in VALID_ROUTES.items():
            path_only = route.split(" ", 1)[1]
            _, _, live = fetch(server.base + path_only.replace("{tf}", "M15"))
            fixture = load(os.path.join("valid", name))["data"]
            live_data = live["data"]
            if path_only in FULL_LIST_ROUTES:
                # full list: same length and same per-element skeleton
                if len(fixture) != len(live_data):
                    check(f"e2e valid/{name} matches live {route}", False,
                          f"len {len(fixture)} != {len(live_data)}")
                    continue
                ok = all(keypaths(f) == keypaths(l) for f, l in zip(fixture, live_data))
            elif isinstance(live_data, list) != isinstance(fixture, list):
                ok = False
            elif isinstance(live_data, list):
                # representative element(s): every fixture element skeleton must
                # appear among the live element skeletons.
                live_skel = {frozenset(keypaths(l)) for l in live_data}
                ok = all(frozenset(keypaths(f)) in live_skel for f in fixture)
            else:
                ok = keypaths(fixture) == keypaths(live_data)
            check(f"e2e valid/{name} structurally matches live {route}", ok)

        # 4. The shared checker has teeth: semantic fixtures are structurally
        #    valid yet must be reported as invariant violations.
        for name in SEMANTIC_FIXTURES:
            payload = load(os.path.join("semantic", name))
            try:
                contract_checker.validate_envelope(
                    "GET /api/v1/analysis/latest", endpoints["GET /api/v1/analysis/latest"], payload
                )
                check(f"e2e semantic/{name} structurally schema-valid", True)
            except contract_checker.ContractError as exc:
                check(f"e2e semantic/{name} structurally schema-valid", False, str(exc))
            found = contract_checker.analysis_contract_violations(payload, schema)
            check(f"e2e semantic/{name} rejected by shared checker", bool(found))

        # 5. Invalid fixtures stay rejected by the frozen reader.
        for name, route in INVALID_ROUTES.items():
            payload = load(os.path.join("invalid", name))
            try:
                contract_checker.validate_envelope(route, endpoints[route], payload)
                check(f"e2e invalid/{name} rejected", False, "unexpectedly validated")
            except contract_checker.ContractError:
                check(f"e2e invalid/{name} rejected", True)

        # 6. Error shape: local error fixtures AND the mock's own 404/405 agree.
        err_required = schema["error_schema"]["required"]
        for name in ("503_missing_dependency.json", "404_unknown_route.json",
                     "405_method_not_allowed.json"):
            body = load(os.path.join("errors", name))
            check(f"e2e errors/{name} matches error_schema",
                  all(k in body for k in err_required) and body.get("error") == "true")

        def error_shape_ok(body):
            return (all(k in body for k in err_required)
                    and body.get("error") == "true"
                    and isinstance(body.get("code"), str)
                    and isinstance(body.get("message"), str))

        try:
            fetch(server.base + "/api/v1/nope")
            check("e2e unknown route -> 404", False, "no error raised")
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            check("e2e unknown route -> 404", exc.code == 404, str(exc.code))
            check("e2e 404 body matches error_schema", error_shape_ok(body), str(body))

        req = urllib.request.Request(
            server.base + "/api/v1/system/state", data=b"{}", method="POST"
        )
        try:
            fetch_req(req)
            check("e2e POST -> 405", False, "no error raised")
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            check("e2e POST -> 405", exc.code == 405, str(exc.code))
            check("e2e 405 body matches error_schema", error_shape_ok(body), str(body))

        # 7. Query handling: history honours ?limit and every element validates.
        status, _, hist = fetch(server.base + "/api/v1/analysis/history?limit=2")
        check("e2e history?limit=2 returns 2", status == 200 and len(hist["data"]) == 2,
              str(len(hist.get("data", []))))
        ok = True
        for element in hist["data"]:
            try:
                contract_checker.validate_envelope(
                    "GET /api/v1/analysis/history",
                    endpoints["GET /api/v1/analysis/history"],
                    {"api": "v1", "schema": "1.0", "data": [element]},
                )
            except contract_checker.ContractError:
                ok = False
        check("e2e history elements validate vs frozen schema", ok)

    # 8. Calibrated branch: the alternate render path, still frozen-null clean.
    with Server(calibrated=True) as server:
        _, _, latest = fetch(server.base + "/api/v1/analysis/latest")
        violations = contract_checker.analysis_contract_violations(latest, schema)
        check("e2e calibrated branch passes E06 invariants", not violations,
              "; ".join(violations))
        fixture = load(os.path.join("valid", "analysis_latest_calibrated.json"))["data"]
        check("e2e calibrated fixture structurally matches live mock",
              keypaths(fixture) == keypaths(latest["data"]))
        sig = latest["data"]["signal"]
        check("e2e calibrated probability populated",
              isinstance(sig["probability"], (int, float)))
        check("e2e calibrated probability_calibrated true",
              sig["probability_calibrated"] is True)

    failed = _results.count(False)
    print(f"\n{len(_results)} check(s), {failed} failed")
    print("RESULT: " + ("PASS" if failed == 0 else "FAIL"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
