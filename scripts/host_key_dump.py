#!/usr/bin/env python3
"""T30 prep - dump the REAL host's exact key tree per route to JSON.

Read-only: launches build/aura_backend_host on a loopback port, fetches every
frozen v1 route, and records the exact key paths the host emits (with a type
summary), so the Lead can extend API_V1_SCHEMA.json additively from an
authoritative list. Writes coordination/agent-c/host_leaf_keys.json.
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import mock_api  # noqa: E402

HOST = os.path.join(REPO, "build", "aura_backend_host")
OUT = os.path.join(REPO, "coordination", "agent-c", "host_leaf_keys.json")
with open(mock_api.SCHEMA_PATH, encoding="utf-8") as fh:
    schema = json.load(fh)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fetch(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read().decode())


def describe(value, pre, paths):
    if isinstance(value, dict):
        for k, v in value.items():
            describe(v, f"{pre}.{k}" if pre else k, paths)
    elif isinstance(value, list):
        for e in value:
            describe(e, f"{pre}[]", paths)
    else:
        paths.add(pre)  # leaf
    return paths


def type_of(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return type(value).__name__


def collect(value, pre, out):
    """Record every path with a type summary; recurse into objects/arrays."""
    if isinstance(value, dict):
        out[pre or "$"] = "object"
        for k, v in value.items():
            collect(v, f"{pre}.{k}" if pre else k, out)
    elif isinstance(value, list):
        out[pre or "$"] = "array"
        for e in value:
            collect(e, f"{pre}[]", out)
    else:
        out[pre] = type_of(value)
    return out


port = free_port()
proc = subprocess.Popen([HOST, "--api-port", str(port)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
base = f"http://127.0.0.1:{port}"
try:
    for _ in range(50):
        try:
            fetch(base + "/api/v1/system/state")
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)

    routes = {}
    for route in schema["endpoints"]:
        path = route.split(" ", 1)[1].replace("{tf}", "M15")
        body = fetch(base + path)
        tree = {}
        collect(body, "", tree)
        leaves = sorted(describe(body, "", set()))
        routes[route] = {"returned_paths": tree, "leaf_paths": leaves}
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

payload = {
    "source": "real host build/aura_backend_host (read-only)",
    "note": "exact key paths the real host emits, for additive API_V1_SCHEMA.json extension (T30)",
    "schema_path": os.path.relpath(mock_api.SCHEMA_PATH, REPO),
    "routes": routes,
}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2, sort_keys=True)
    fh.write("\n")
print(f"wrote {OUT}: {len(routes)} routes")
