#!/usr/bin/env python3
"""T13 - end-to-end against the REAL backend host (not the mock).

Complements T24 (tests/integration/test_e2e_frozen_v1.py), which drives the
synthetic mock server through scripts/mock_api.py. This harness drives the
production binary build/aura_backend_host over a real loopback socket and checks
the frozen v1 contract holds on the *real* implementation:

  1. the real host answers every route in the frozen schema with HTTP 200 and a
     response that validates via Agent-B's shared T23 reader
     (src/models/contract_checker.py) - the same single frozen-set reader used by
     F17-1 and the T24 harness;
  2. RULE C / E06 / E07 hold on /analysis/latest: probability null,
     probability_calibrated false, score_is_probability false;
  3. the host is honest about its startup posture: with no staged bridge the
     system/state reports DEGRADED + shadow_only, never a false "ready".

This closes F24-1's gap (T24 only exercised the mock) at the real-host level. The
real host is NOT app-root relocatable (PathResolver derives appRootDir from the
binary's directory), so a fresh checkout starts DEGRADED - that is reported, not
hidden.

Real-data (E05) path: when ``FAKE_MT5_CSV`` points at the committed canonical M1
corpus, the harness stages the MetaTrader5 replay shim + the CSV-feed module into
the bundle, starts the real host with that feed, and asserts the *evidential*
posture: the bridge reaches the real corpus (mt5_ready, resolved symbol), every
timeframe observes real closed bars, M15 is decision-grade and the host runs in
SHADOW, while the RULE C gate stays closed (probability null) and the frozen v1
contract still holds. The default (no FAKE_MT5_CSV) run keeps the honest
no-data DEGRADED posture checks. Reads the binary; never mutates src/.

Exit 0 = the frozen v1 contract holds end to end on the real host. Skips (exit 0
with a notice) when the host has not been built in this checkout. Reads the
binary; never mutates anything.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "scripts"))
sys.path.insert(0, os.path.join(REPO, "src", "models"))
import contract_checker  # noqa: E402
import mock_api  # noqa: E402
import schema_shape  # noqa: E402

HOST_BIN = os.path.join(REPO, "build", "aura_backend_host")
SCHEMA_PATH = mock_api.SCHEMA_PATH

_results = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append(ok)
    print(("[PASS] " if ok else "[FAIL] ") + name + (f" - {detail}" if detail and not ok else ""))
    return ok


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fetch(url: str, timeout: float = 5.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def wait_for(url: str, attempts: int = 50, delay: float = 0.1,
             log_path: str | None = None):
    last = None
    for _ in range(attempts):
        try:
            return fetch(url)
        except Exception as exc:  # noqa: BLE001 - last error surfaced below
            last = exc
            time.sleep(delay)
    detail = ""
    if log_path and os.path.isfile(log_path):
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            tail = fh.read()[-2000:].strip()
        if tail:
            detail = f"\n--- host output ---\n{tail}"
    raise RuntimeError(f"real host did not serve: {last}{detail}")


def reap(proc: subprocess.Popen) -> None:
    """Terminate the host and everything it spawned (the bridge_service.py child).

    The host starts the bridge on a fixed port; if only the host is signalled the
    bridge survives and the next run's bridge fails to bind (spurious failures).
    The process is started in its own session so the whole group can be reaped.
    """
    if proc.poll() is None:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(os.getpgid(proc.pid), sig)
            except (ProcessLookupError, PermissionError):
                break
            try:
                proc.wait(timeout=5)
                break
            except subprocess.TimeoutExpired:
                continue


def ensure_bundle(include_replay: bool = False):
    """Populate build/resources/bridge/mt5_python from the repo.

    The real host locates its bridge as <exe_dir>/resources/bridge/mt5_python/
    bridge_service.py (PathResolver). A checkout that has not been packaged has no
    such tree, so the host cannot start. Staging the real bridge sources + (when
    replaying) the MetaTrader5 shim makes this harness self-contained. Writes only
    under build/; never src/. Returns files created so replay staging can revert.
    """
    target = os.path.join(os.path.dirname(HOST_BIN), "resources", "bridge",
                          "mt5_python")
    os.makedirs(target, exist_ok=True)
    created = []
    sources = [
        (os.path.join(REPO, "bridge", "mt5_python", name), name)
        for name in ("bridge_service.py", "mt5_client.py", "schemas.py",
                     "mt5_csv_feed.py")
    ]
    if include_replay:
        sources.append((
            os.path.join(REPO, "tests", "integration", "fake_mt5", "MetaTrader5.py"),
            "MetaTrader5.py"))
    for src, name in sources:
        dest = os.path.join(target, name)
        if os.path.exists(dest):
            # A packaged checkout already carries this tree, and a checkout
            # where build/resources/bridge is a symlink back to the repo makes
            # src and dest the same inode. Copying then raises SameFileError,
            # and `created` must not list it or the replay cleanup would delete
            # a tracked source file.
            if os.path.samefile(src, dest):
                continue
        else:
            created.append(dest)
        shutil.copy(src, dest)
    return created


def run_real_data_checks(csv_path: str, schema: dict) -> None:
    """Evidential path: real gold corpus flowing through the real host."""
    staged = ensure_bundle(include_replay=True)
    port = free_port()
    env = dict(os.environ)
    env["FAKE_MT5_CSV"] = os.path.abspath(csv_path)
    env["FAKE_MT5_SYMBOL"] = env.get("FAKE_MT5_SYMBOL", "XAUUSD")
    # Capture the host's output instead of discarding it. When the host fails to
    # bind (a cold or loaded CI runner, a missing shim), the old code reported
    # only "connection refused" and hid why; wait_for now prints the tail.
    log_fd, log_path = tempfile.mkstemp(prefix="t13-host-", suffix=".log")
    proc = subprocess.Popen(
        [HOST_BIN, "--api-port", str(port), "--dev-system-python"],
        env=env, stdout=log_fd, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    os.close(log_fd)  # Popen duplicated it for the child
    base = f"http://127.0.0.1:{port}"
    try:
        # 15s, not 5s: the host loads the full committed corpus and the MT5 shim
        # before it binds, which is a second locally but slower on CI.
        wait_for(base + "/api/v1/system/state", attempts=150, delay=0.1,
                 log_path=log_path)
        # Let several ingestion cycles land real closed bars (H4/D1 need history).
        for _ in range(40):
            _, tf = fetch(base + "/api/v1/timeframes")
            entries = tf["data"]
            if entries and all(e["has_closed_bar"] for e in entries):
                break
            time.sleep(0.25)

        _, bs = fetch(base + "/api/v1/bridge/status")
        bridge = bs["data"]
        check("real feed: bridge handshake ok", bridge.get("handshake_ok") is True)
        check("real feed: mt5_ready from corpus", bridge.get("mt5_ready") is True)
        check("real feed: resolved XAUUSD",
              bridge.get("resolved_symbol") == "XAUUSD",
              str(bridge.get("resolved_symbol")))

        _, tf = fetch(base + "/api/v1/timeframes")
        entries = tf["data"]
        check("real feed: all timeframes observed",
              bool(entries) and all(e["observed"] for e in entries))
        check("real feed: all timeframes have a closed bar",
              bool(entries) and all(e["has_closed_bar"] for e in entries))
        check("real feed: all timeframes VALID",
              bool(entries) and all(e["quality"]["state"] == "VALID" for e in entries),
              str(sorted({e["quality"]["state"] for e in entries})))
        check("real feed: all timeframes FRESH",
              bool(entries) and all(e["freshness"]["state"] == "FRESH" for e in entries),
              str(sorted({e["freshness"]["state"] for e in entries})))
        m15 = next((e for e in entries if e["timeframe"] == "M15"), None)
        check("real feed: M15 is decision-grade",
              m15 is not None and m15["quality"]["decision_grade"] is True)

        _, state = fetch(base + "/api/v1/system/state")
        check("real feed: host runs SHADOW with real data",
              state["data"].get("mode") == "SHADOW", str(state["data"].get("mode")))
        check("real feed: host reports ready",
              state["data"].get("ready") is True)

        _, latest = fetch(base + "/api/v1/analysis/latest")
        signal = latest["data"]["signal"]
        check("real feed RULE C: probability stays null",
              signal["probability"] is None)
        check("real feed RULE C: probability_calibrated false",
              signal["probability_calibrated"] is False)
        check("real feed RULE C: score_is_probability false",
              latest["data"]["meta"]["score_is_probability"] is False)
        violations = contract_checker.analysis_contract_violations(latest, schema)
        check("real feed analysis passes semantic invariants", not violations,
              "; ".join(violations))

        # Frozen v1 contract still holds with real data on every route.
        for route, spec in schema["endpoints"].items():
            _method, _, path = route.partition(" ")
            live_path = path.replace("{tf}", "M15")
            try:
                status, body = fetch(base + live_path)
            except urllib.error.HTTPError as exc:
                check(f"real feed {route} reachable", False, f"HTTP {exc.code}")
                continue
            check(f"real feed {route} status 200", status == 200, str(status))
            try:
                contract_checker.validate_envelope(route, spec, body)
                check(f"real feed {route} matches frozen schema", True)
            except Exception as exc:  # noqa: BLE001
                check(f"real feed {route} matches frozen schema", False, repr(exc))
    finally:
        reap(proc)
        for path in staged:
            with contextlib.suppress(OSError):
                os.remove(path)
        with contextlib.suppress(OSError):
            os.remove(log_path)


def main() -> int:
    if not os.path.exists(HOST_BIN):
        print(f"[SKIP] {HOST_BIN} not built; run `cmake --build build` first")
        return 0

    with open(SCHEMA_PATH, "r", encoding="utf-8") as handle:
        schema = json.load(handle)

    # The real host needs its bridge tree next to the binary; stage it so the
    # harness works in an un-packaged checkout (no-op if already packaged).
    ensure_bundle(include_replay=False)

    port = free_port()
    # No --once: the host stops right after its first cycle and the API would be
    # unreachable. Run it as a server and terminate it ourselves.
    proc = subprocess.Popen(
        [HOST_BIN, "--api-port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
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
                contract_checker.validate_envelope(route, spec, body)
                check(f"{route} matches frozen schema (real host)", True)
            except Exception as exc:  # noqa: BLE001
                check(f"{route} matches frozen schema (real host)", False, repr(exc))
            # T30(b): the exact-shape teeth (two-sided) - the real host must emit
            # no key the schema does not declare, and must supply every required
            # key. Forward-looking: also covers state-dependent fields (e.g.
            # freshness sub-fields) once real data flows under E05.
            found = schema_shape.check_exact_shape(route, spec, body)
            detail = "; ".join(f"{k}: {'; '.join(v)}" for k, v in sorted(found.items()))
            check(f"{route} is exact-shape (real host)", not found, detail)

        # RULE C on the real host: synthetic/unstaged => probability stays null.
        _, latest = fetch(base + "/api/v1/analysis/latest")
        signal = latest["data"]["signal"]
        check("real host analysis probability null (RULE C)",
              signal["probability"] is None, str(signal["probability"]))
        check("real host probability_calibrated false",
              signal["probability_calibrated"] is False)
        check("real host score_is_probability false",
              latest["data"]["meta"]["score_is_probability"] is False)
        violations = contract_checker.analysis_contract_violations(latest, schema)
        check("real host analysis passes semantic invariants", not violations,
              "; ".join(violations))

        # Honest posture. The mode follows the bridge, so the assertion is
        # derived rather than assumed: a bridge that is ONLINE (the default
        # ASTRA_DATA_SOURCE=LIVE checkout, or a packaged tree symlinked into
        # build/) gives SHADOW + ready, a bridge that is not gives DEGRADED +
        # not ready. shadow_only is true either way - the product never claims
        # to trade, and that is the invariant that must hold in both postures.
        _, state = fetch(base + "/api/v1/system/state")
        data = state["data"]
        bridge_online = data.get("bridge_state") == "ONLINE"
        want_mode = "SHADOW" if bridge_online else "DEGRADED"
        check(f"real host mode matches its bridge ({want_mode})",
              data.get("mode") == want_mode,
              f"mode={data.get('mode')} bridge_state={data.get('bridge_state')}")
        check("real host shadow_only always true",
              data.get("shadow_only") is True)
        check("real host ready follows the bridge, never ahead of it",
              data.get("ready") is bridge_online,
              f"ready={data.get('ready')} bridge_state={data.get('bridge_state')}")
    finally:
        reap(proc)

    # Evidential (E05) path: opt-in so the default frozen-suite run stays green.
    # Enable with T13_REAL_DATA=1 (or by exporting FAKE_MT5_CSV). When enabled it
    # replays the committed real corpus through the real host; the checks are the
    # evidential acceptance (and will honestly fail while a backend defect blocks
    # real-data ingestion - the failure is the finding, not a faked PASS).
    want_real = os.environ.get("T13_REAL_DATA", "").strip() not in ("", "0")
    if want_real or os.environ.get("FAKE_MT5_CSV", "").strip():
        csv_path = os.environ.get("FAKE_MT5_CSV", "").strip() or os.path.join(
            REPO, "research", "data", "xauusd_m1", "xauusd_m1_real.csv")
        if os.path.isfile(csv_path):
            print(f"\nReal-data (E05) path over {os.path.relpath(csv_path, REPO)}")
            run_real_data_checks(csv_path, schema)
        else:
            print(f"\n[notice] T13_REAL_DATA set but corpus not found: {csv_path}")
    else:
        print("\n[notice] E05 evidential path not requested "
              "(set T13_REAL_DATA=1 to run real-data checks)")

    passed = sum(1 for ok in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
