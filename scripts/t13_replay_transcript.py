#!/usr/bin/env python3
"""T13 — real-data end-to-end replay transcript (Lead directive 2026-10-08 10:10).

Runs replay -> features -> model -> API over the committed 3.5-month XAUUSD M1
corpus and writes the full transcript to research/reports/t13_realdata.md.

The path exercised is the *real* one: the real bridge (bridge_service.py) reads
the real corpus through the replay feed, the real host (aura_backend_host)
ingests it, derives the frozen FeatureSets, evaluates the model behind the
shared RULE C gate, and serves the frozen v1 API. Nothing here is staged; no
`src/` is touched. Output is labelled PROOF-OF-CONCEPT (single window).

Usage:
    python3 scripts/t13_replay_transcript.py [--out research/reports/t13_realdata.md]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests", "integration"))
import test_e2e_real_host_t13 as harness  # noqa: E402

HOST_BIN = harness.HOST_BIN
DEFAULT_CSV = os.path.join(REPO, "research", "data", "xauusd_m1", "xauusd_m1_real.csv")
DEFAULT_OUT = os.path.join(REPO, "research", "reports", "t13_realdata.md")

# Routes captured, in pipeline order, with a short human label.
ROUTES = [
    ("replay", "GET /api/v1/bridge/status", "bridge handshake + resolved symbol"),
    ("features", "GET /api/v1/timeframes", "per-timeframe closed-bar + quality/freshness"),
    ("features", "GET /api/v1/timeframes/M15/snapshot", "M15 feature snapshot"),
    ("model", "GET /api/v1/analysis/latest", "context + signal + levels + meta"),
    ("model", "GET /api/v1/analysis/history", "recent decision records"),
    ("model", "GET /api/v1/context/latest", "market context only"),
    ("model", "GET /api/v1/risk/latest", "risk proposal + portfolio"),
    ("api", "GET /api/v1/system/state", "host mode / readiness"),
    ("api", "GET /api/v1/health", "liveness"),
    ("api", "GET /api/v1/research/status", "research layer posture"),
    ("api", "GET /api/v1/governance/status", "governance posture"),
    ("api", "GET /api/v1/audit/recent", "recent audit trail"),
]


def fetch(url: str, timeout: float = 5.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def wait_for(url: str, attempts: int = 60):
    last = None
    for _ in range(attempts):
        try:
            return fetch(url)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.1)
    raise RuntimeError(f"host did not serve: {last}")


def rel(path: str) -> str:
    try:
        return os.path.relpath(path, REPO)
    except ValueError:
        return path


def run_transcript(out_path: str) -> int:
    if not os.path.exists(HOST_BIN):
        print(f"[SKIP] {HOST_BIN} not built; run `cmake --build build` first")
        return 0
    if not os.path.isfile(DEFAULT_CSV):
        print(f"[SKIP] corpus not found: {DEFAULT_CSV}")
        return 0

    staged = harness.ensure_bundle(include_replay=True)
    port = harness.free_port()
    env = dict(os.environ)
    env["FAKE_MT5_CSV"] = os.path.abspath(DEFAULT_CSV)
    env["FAKE_MT5_SYMBOL"] = env.get("FAKE_MT5_SYMBOL", "XAUUSD")

    entry = {
        "started_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        "corpus": rel(DEFAULT_CSV),
        "host_bin": rel(HOST_BIN),
        "port": port,
        "steps": [],
    }

    proc = subprocess.Popen(
        [HOST_BIN, "--api-port", str(port), "--dev-system-python"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        wait_for(base + "/api/v1/system/state")
        # Let ingestion cycles land real closed bars for every timeframe.
        for _ in range(60):
            _, tf = fetch(base + "/api/v1/timeframes")
            entries = tf["data"]
            if entries and all(e["has_closed_bar"] for e in entries):
                break
            time.sleep(0.25)
        # Give the model a moment to publish a decision at the newest closed bar.
        time.sleep(1.0)

        for stage, route, label in ROUTES:
            _, _, path = route.partition(" ")
            try:
                status, body = fetch(base + path)
            except urllib.error.HTTPError as exc:
                entry["steps"].append({
                    "stage": stage, "route": route, "label": label,
                    "status": exc.code, "body": None,
                    "error": f"HTTP {exc.code}",
                })
                continue
            entry["steps"].append({
                "stage": stage, "route": route, "label": label,
                "status": status, "body": body,
            })

        # RULE C assertion on the live payload.
        latest = next(
            (s["body"] for s in entry["steps"]
             if s["route"] == "GET /api/v1/analysis/latest"), None)
        if latest:
            sig = latest["data"]["signal"]
            meta = latest["data"]["meta"]
            entry["rule_c"] = {
                "probability": sig.get("probability"),
                "probability_calibrated": sig.get("probability_calibrated"),
                "score_is_probability": meta.get("score_is_probability"),
                "gate_closed": sig.get("probability") is None,
            }
        entry["finished_utc"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    finally:
        harness.reap(proc)
        for path in staged:
            with contextlib.suppress(OSError):
                os.remove(path)

    write_transcript(out_path, entry)
    ok = bool(entry.get("rule_c", {}).get("gate_closed")) and all(
        s["status"] == 200 for s in entry["steps"])
    print(f"transcript -> {rel(out_path)} "
          f"({len(entry['steps'])} routes, rule_c gate_closed="
          f"{entry.get('rule_c', {}).get('gate_closed')})")
    return 0 if ok else 1


def write_transcript(out_path: str, entry: dict) -> None:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    lines = [
        "# T13 — Real-data end-to-end replay transcript",
        "",
        "> **PROOF-OF-CONCEPT — single window.** 3.5-month XAUUSD M1 corpus",
        "> (2026-06-24 .. 2026-10-08 broker time). Not the publication verdict;",
        "> no walk-forward / out-of-sample claim is made here.",
        "",
        f"- Corpus: `{entry['corpus']}`",
        f"- Host: `{entry['host_bin']}` (loopback :{entry['port']})",
        f"- Window: {entry['started_utc']} .. {entry['finished_utc']} UTC",
        "",
        "Path exercised: **replay feed -> real bridge -> real host ingest -> frozen",
        "FeatureSets -> model behind the shared RULE C gate -> frozen v1 API.**",
        "No test double stands in for AURA code; the only double is the external",
        "MetaTrader5 package, which serves the committed corpus.",
        "",
    ]
    rc = entry.get("rule_c")
    if rc:
        lines += [
            "## RULE C (live)",
            "",
            f"- `signal.probability` = `{json.dumps(rc['probability'])}`",
            f"- `signal.probability_calibrated` = `{json.dumps(rc['probability_calibrated'])}`",
            f"- `meta.score_is_probability` = `{json.dumps(rc['score_is_probability'])}`",
            f"- gate closed (probability withheld): **{rc['gate_closed']}**",
            "",
        ]
    lines += ["## Transcript", ""]
    current_stage = None
    for step in entry["steps"]:
        if step["stage"] != current_stage:
            current_stage = step["stage"]
            lines += [f"### stage: {current_stage}", ""]
        lines += [
            f"**`{step['route']}`** — {step['label']}  (HTTP {step['status']})",
            "",
            "```json",
            json.dumps(step["body"], indent=2, sort_keys=True)
            if step["body"] is not None else f"ERROR: {step.get('error')}",
            "```",
            "",
        ]
    lines += [
        "---",
        "",
        "Transcript generated by `scripts/t13_replay_transcript.py` (Agent-C).",
        "AI agent (OpenHands/agent-c) on behalf of the operator.",
        "",
    ]
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args()
    return run_transcript(args.out)


if __name__ == "__main__":
    sys.exit(main())
