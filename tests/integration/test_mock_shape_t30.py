#!/usr/bin/env python3
"""T30(b) - exact-shape assertion for the MOCK payloads (mock-keys <= schema-keys).

The mock must emit exactly the frozen schema's declared shape (no extra keys), so
that fixtures derived from it and the host both conform. This is the mock-side twin
of the real-host assertion in test_e2e_real_host_t13.py.

Exit 0 = conformant. Reads scripts/mock_api.py + the frozen schema; no network.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import mock_api  # noqa: E402
import schema_shape  # noqa: E402

_results = []


def check(name, ok, detail=""):
    _results.append(ok)
    print(("[PASS] " if ok else "[FAIL] ") + name + (f" - {detail}" if detail and not ok else ""))


def main() -> int:
    schema = schema_shape.load_schema(mock_api.SCHEMA_PATH)
    payloads = mock_api.build_payloads(schema, calibrated=False)

    check("mock emits a payload for every frozen route",
          set(payloads) == set(schema["endpoints"]),
          f"mock-only={set(payloads) - set(schema['endpoints'])} "
          f"schema-only={set(schema['endpoints']) - set(payloads)}")

    for route in sorted(schema["endpoints"]):
        spec = schema["endpoints"][route]
        found = schema_shape.check_exact_shape(route, spec, payloads[route])
        detail = "; ".join(
            f"{k}: {'; '.join(v)}" for k, v in sorted(found.items())
        )
        check(f"{route} mock is exact-shape (no undeclared, no missing required)",
              not found, detail)

    # Teeth-bite: prove the assertion is not vacuously green - it must DETECT a
    # synthetic extra key and a synthetic missing-required key (the red-before
    # evidence Agent-D requires: before T30 these classes went undetected).
    import copy as _copy

    bridge = schema["endpoints"]["GET /api/v1/bridge/status"]
    sample = payloads["GET /api/v1/bridge/status"]

    with_extra = _copy.deepcopy(sample)
    with_extra["data"]["totally_undeclared_field"] = 1
    got = schema_shape.check_exact_shape("GET /api/v1/bridge/status", bridge, with_extra)
    check("tooth detects an undeclared extra key",
          "undeclared" in got and "data.totally_undeclared_field" in got["undeclared"],
          str(got))

    with_missing = _copy.deepcopy(sample)
    del with_missing["data"]["observed"]
    got = schema_shape.check_exact_shape("GET /api/v1/bridge/status", bridge, with_missing)
    check("tooth detects a missing required key",
          "missing_required" in got and "data.observed" in got["missing_required"],
          str(got))

    empty_array_ok = _copy.deepcopy(payloads["GET /api/v1/audit/recent"])
    got = schema_shape.check_exact_shape(
        "GET /api/v1/audit/recent", schema["endpoints"]["GET /api/v1/audit/recent"], empty_array_ok)
    check("empty arrays are not flagged as missing element keys", not got, str(got))

    passed = sum(1 for ok in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
