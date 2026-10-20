#!/usr/bin/env python3
"""T19 - Mock ASTRA backend API v1 server.

Serves the *frozen* contract in docs/architecture/API_V1_SCHEMA.json with
realistic synthetic data, so the frontend can be built and tested without MT5,
a bridge, or live data. Swap the base URL to the live backend when ready; no
code change beyond the base URL.

The mock is honest about RULE C: by default it serves the **uncalibrated**
shape (probability null, probability_calibrated false, score_is_probability
false). Pass --calibrated to exercise the calibrated branch (probability
populated, score_is_probability true). This lets the frontend test both render
paths without a real calibration.

Loopback only. No third-party dependencies (stdlib http.server).

Usage:
    python3 scripts/mock_api.py                 # serve on 127.0.0.1:8790
    python3 scripts/mock_api.py --port 8792
    python3 scripts/mock_api.py --calibrated
    python3 scripts/mock_api.py --check         # validate payloads, exit
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, ClassVar

SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docs",
    "architecture",
    "API_V1_SCHEMA.json",
)

DISCLAIMER = "Decision support only. Not financial advice."


# --------------------------------------------------------------------------
# Schema validation (purpose-built for the API_V1_SCHEMA.json dialect).
# --------------------------------------------------------------------------

class SchemaError(Exception):
    pass


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _matches_type(value: Any, expected: Any) -> bool:
    names = expected if isinstance(expected, list) else [expected]
    actual = _json_type(value)
    for name in names:
        if name == "number" and actual in ("integer", "number"):
            return True
        if name == actual:
            return True
    return False


def validate_properties(value: Any, spec: dict, where: str) -> None:
    """Validate one object/array against the {type,enum,const,...} dialect."""
    expected = spec.get("type")
    if expected is not None and not _matches_type(value, expected):
        raise SchemaError(
            f"{where}: expected type {expected}, got {_json_type(value)}"
        )
    if "const" in spec and value != spec["const"]:
        raise SchemaError(f"{where}: expected const {spec['const']!r}, got {value!r}")
    if "enum" in spec and value not in spec["enum"]:
        raise SchemaError(f"{where}: {value!r} not in enum {spec['enum']}")
    if value is not None and isinstance(value, (int, float)) and not isinstance(
        value, bool
    ):
        # F22-4b-v: NaN/inf compare false against every bound, so a non-finite
        # number would slip past min/max. JSON has no NaN literal; reject it.
        if not math.isfinite(value):
            raise SchemaError(f"{where}: non-finite number {value!r}")
        if "minimum" in spec and value < spec["minimum"]:
            raise SchemaError(f"{where}: {value} below minimum {spec['minimum']}")
        if "maximum" in spec and value > spec["maximum"]:
            raise SchemaError(f"{where}: {value} above maximum {spec['maximum']}")


def validate_data(endpoint: str, spec: dict, data: Any) -> None:
    """Validate the `data` member against the endpoint spec."""
    if spec.get("data_type") == "array":
        if not isinstance(data, list):
            raise SchemaError(f"{endpoint}.data: expected array")
        for i, element in enumerate(data):
            _validate_object(
                endpoint,
                f"data[{i}]",
                element,
                spec.get("element_required", []),
                spec.get("element_properties", {}),
            )
        return
    _validate_object(
        endpoint,
        "data",
        data,
        spec.get("data_required", []),
        spec.get("data_properties", {}),
    )


def _validate_object(
    endpoint: str, where: str, value: Any, required: list, properties: dict
) -> None:
    if not isinstance(value, dict):
        raise SchemaError(f"{endpoint}.{where}: expected object")
    for key in required:
        if key not in value:
            raise SchemaError(f"{endpoint}.{where}: missing required '{key}'")
    for key, subspec in properties.items():
        if key not in value:
            continue
        node = value[key]
        validate_properties(node, subspec, f"{endpoint}.{where}.{key}")
        if subspec.get("type") == "object" and "required" in subspec:
            for sub in subspec["required"]:
                if sub not in node:
                    raise SchemaError(
                        f"{endpoint}.{where}.{key}: missing required '{sub}'"
                    )
        if subspec.get("type") == "object" and "properties" in subspec:
            for sub, subsub in subspec["properties"].items():
                if sub in node:
                    validate_properties(
                        node[sub], subsub, f"{endpoint}.{where}.{key}.{sub}"
                    )


def validate_envelope(endpoint: str, spec: dict, payload: dict) -> None:
    for key in spec.get("required", []):
        if key not in payload:
            raise SchemaError(f"{endpoint}: envelope missing '{key}'")
    if payload.get("api") != "v1":
        raise SchemaError(f"{endpoint}: api != v1")
    if payload.get("schema") != "1.0":
        raise SchemaError(f"{endpoint}: schema != 1.0")
    validate_data(endpoint, spec, payload.get("data"))


# --------------------------------------------------------------------------
# Synthetic payloads (deterministic; schema-valid).
# --------------------------------------------------------------------------

def envelope(data: Any) -> dict:
    return {"api": "v1", "schema": "1.0", "data": data}


def error_body(code: str, message: str) -> dict:
    return {"error": "true", "code": code, "message": message}


def context_obj(calibrated: bool = False) -> dict:
    # v1 default is the frozen-null posture: the backend sources no live context
    # yet, so every field is UNKNOWN/NONE/None (F19-1 fidelity). The calibrated
    # branch is the only one that carries a populated context.
    if not calibrated:
        return {
            "regime": "UNKNOWN",
            "h4_bias": "UNKNOWN",
            "m15_trigger": "UNKNOWN",
            "mtf_agreement": None,
            "volatility_state": "UNKNOWN",
        }
    return {
        "regime": "TREND_UP",
        "h4_bias": "BULLISH",
        "m15_trigger": "UP",
        "mtf_agreement": None,
        "volatility_state": "NORMAL",
    }


def signal_obj(calibrated: bool) -> dict:
    # Frozen nulls in v1: horizon / confidence_lo / confidence_hi / model_version
    # stay null even when calibrated (the decision model is not frozen yet).
    return {
        "direction": "UP" if calibrated else "NONE",
        "horizon": None,
        "probability": 0.61 if calibrated else None,
        "probability_calibrated": calibrated,
        "score": 0.58 if calibrated else 0.512,
        "confidence_lo": None,
        "confidence_hi": None,
        "model_version": None,
        "features_contributing": [],
    }


def levels_obj(with_targets: bool = True) -> dict:
    # Frozen nulls in v1: levels come from a live risk proposal, which does not
    # exist on the synthetic path (F19-1; F15-3 T17 freeze). `targets` is the
    # scaled-target ladder the host emits on /analysis/latest only; the history
    # route persists no levels at all, so it omits the key (matching the host).
    out: dict[str, object] = {
        "entry": None,
        "stop_loss": None,
        "take_profit": None,
    }
    if with_targets:
        out["targets"] = []
    out.update({
        "reward_risk": None,
        "suggested_risk_pct": None,
        "sl_method": None,
        "tp_method": None,
    })
    return out


def meta_obj(calibrated: bool, tier: str) -> dict:
    return {
        "coverage_tier": tier,
        "data_freshness_sec": None,
        "degraded": False,
        # E07: the surfaced value is a raw score, never a calibrated probability,
        # so this is ALWAYS false in v1. signal.probability_calibrated is the
        # single source of truth for show-probability-vs-show-score.
        "score_is_probability": False,
        "disclaimer": DISCLAIMER,
    }


def analysis_obj(calibrated: bool, index: int = 0, with_targets: bool = True) -> dict:
    tier = "medium" if calibrated else "unknown"
    return {
        "timestamp": f"2026-10-07T22:{index:02d}:00Z" if calibrated else None,
        "symbol": "XAUUSD",
        "context": context_obj(calibrated),
        "signal": signal_obj(calibrated),
        "levels": levels_obj(with_targets),
        "meta": meta_obj(calibrated, tier),
    }


def build_payloads(schema: dict, calibrated: bool) -> dict:
    """Every frozen endpoint -> its response body."""
    payloads: dict[str, dict] = {
        "GET /api/v1/system/state": envelope(
            {
                "mode": "SHADOW",
                "shadow_only": True,
                "ready": True,
                "bridge_state": "ONLINE",
                "startup_stage": "READY",
                "api": "v1",
                "schema": "1.0",
            }
        ),
        "GET /api/v1/health": envelope(
            {
                "aggregate": "ONLINE",
                "decision_grade_data": True,
                "degraded_reasons": [],
                "observed_at": 1760000000000,
                "unknown_is_not_safe": True,
            }
        ),
        "GET /api/v1/health/v1": envelope(
            {"status": "ok", "bridge": "ok", "version": "v1", "uptime_sec": 42}
        ),
        "GET /api/v1/timeframes": envelope(
            [
                {
                    "timeframe": tf,
                    "observed": True,
                    "has_closed_bar": True,
                    "quality": {"state": "VALID", "decision_grade": True},
                    "decision_grade": True,
                    "freshness": {
                        "state": "FRESH",
                        "is_fresh": True,
                        "last_update": 1760000000000,
                        "age_millis": 0,
                        "max_age_millis": 60000,
                    },
                    "last_successful_update": 1760000000,
                    "last_closed_bar_open": 1760000000,
                    "sequence": 42,
                }
                for tf in ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]
            ]
        ),
        "GET /api/v1/analysis/latest": envelope(analysis_obj(calibrated)),
        "GET /api/v1/context/latest": envelope(
            {
                "timestamp": "2026-10-08T14:30:00Z",
                "symbol": "XAUUSD",
                "context": context_obj(),
            }
        ),
        "GET /api/v1/bridge/status": envelope(
            {
                "bridge_process_state": "ONLINE",
                "startup_stage": "READY",
                "handshake_ok": True,
                "mt5_ready": True,
                "transport": "http_loopback",
                "host": "127.0.0.1",
                "loopback_only": True,
                "managed_by_application": True,
                "requires_manual_cmd": False,
                "resolved_symbol": "XAUUSD",
                "package_available": True,
                "initialized": True,
                "mt5_ready_live": True,
                "broker": None,
                "server": None,
                "bridge_symbol": "XAUUSD",
                "process_state": "ONLINE",
                "last_error": "",
                "last_successful_request": 1760000000000,
                "observed": True,
            }
        ),
        "GET /api/v1/risk/latest": envelope(
            {
                "available": True,
                "open_positions": 0,
                "aggregate_open_risk_fraction": 0.0,
                "risk_bounded_by_guardian": True,
                "proposal_available": False,
                "proposal": None,
                "proposal_reason": "no decision evaluated yet",
            }
        ),
        "GET /api/v1/shadow/positions": envelope(
            [
                {
                    "position_id": "pos-0001",
                    "decision_id": "dec-0001",
                    "direction": "LONG",
                    "state": "OPEN",
                    "entry_price": 2400.0,
                    "shadow_only": True,
                }
            ]
        ),
        "GET /api/v1/shadow/outcomes": envelope(
            [
                {
                    "outcome_id": "out-0001",
                    "position_id": "pos-0001",
                    "decision_id": "dec-0001",
                    "direction": "LONG",
                    "exit_state": "TP",
                    "realized_pnl": 12.5,
                    "shadow_only": True,
                }
            ]
        ),
        "GET /api/v1/research/status": envelope(
            {
                "available": True,
                "mode": "SHADOW",
                "note": "research output never grants execution authority",
                "experiment_count": 0,
                "experiments": [],
                "failure_count": 0,
                "failures": [],
            }
        ),
        "GET /api/v1/governance/status": envelope(
            {
                "live_trading_authorised": False,
                "pending_count": 0,
                "pending": [],
                "history": [],
            }
        ),
        "GET /api/v1/audit/recent": envelope(
            {
                "count": 0,
                "audit_stream_size": 0,
                "audit_records": [],
                "active_incidents": [],
            }
        ),
    }
    # Snapshot is templated on the timeframe; validate a representative one.
    snapshot = schema["endpoints"]["GET /api/v1/timeframes/{tf}/snapshot"]
    payloads["GET /api/v1/timeframes/{tf}/snapshot"] = envelope(
        {
            "timeframe": "M15",
            "observed": True,
            "quality": {"state": "VALID", "decision_grade": True},
            "has_closed_bar": True,
            "last_successful_update": 1760000000,
            "sequence": 42,
            "open": 2412.5,
            "high": 2415.0,
            "low": 2410.0,
            "close": 2413.75,
            "open_time": 1760000000,
        }
    )
    payloads["GET /api/v1/analysis/history"] = envelope(
        [analysis_obj(calibrated, i, with_targets=False) for i in range(3)]
    )
    del snapshot
    return payloads


def check(schema: dict) -> int:
    """Validate every synthetic payload against the frozen schema."""
    failures = 0
    for calibrated in (False, True):
        payloads = build_payloads(schema, calibrated)
        for route, payload in payloads.items():
            spec = schema["endpoints"][route]
            try:
                validate_envelope(route, spec, payload)
            except SchemaError as exc:
                failures += 1
                print(f"[FAIL] calibrated={calibrated} {route}: {exc}")
            else:
                print(f"[PASS] calibrated={calibrated} {route}")
    # The error schema must also validate.
    err_spec = {"required": ["error", "code", "message"]}
    try:
        validate_envelope(
            "error", {"required": [], "data_required": err_spec["required"]},
            envelope(error_body("not_found", "unknown route")),
        )
        print("[PASS] error schema")
    except SchemaError as exc:
        failures += 1
        print(f"[FAIL] error schema: {exc}")

    # F19-4: the default (uncalibrated) mock must match the frozen-null contract
    # the real backend emits in v1. These are semantics the JSON Schema cannot
    # express, so they are asserted explicitly.
    default = build_payloads(schema, False)["GET /api/v1/analysis/latest"]["data"]
    frozen_nulls = [
        default["signal"]["horizon"],
        default["signal"]["confidence_lo"],
        default["signal"]["confidence_hi"],
        default["signal"]["model_version"],
        default["meta"]["data_freshness_sec"],
        default["context"]["mtf_agreement"],
        default["timestamp"],
        # `targets` is the one levels key that is structurally an empty list in
        # the no-proposal posture (the host emits `[]`, never null), so it is
        # asserted as empty rather than null.
        *[v for k, v in default["levels"].items() if k != "targets"],
    ]
    if any(v is not None for v in frozen_nulls) or default["levels"]["targets"] != []:
        failures += 1
        print("[FAIL] F19-4 frozen-null set present in default analysis payload")
    else:
        print("[PASS] F19-4 default payload is the frozen-null posture")

    # E07: score_is_probability is always false in v1 (both branches).
    e07_ok = all(
        build_payloads(schema, cal)["GET /api/v1/analysis/latest"]["data"]["meta"][
            "score_is_probability"
        ]
        is False
        for cal in (False, True)
    )
    if not e07_ok:
        failures += 1
        print("[FAIL] E07 score_is_probability must be false in both branches")
    else:
        print("[PASS] E07 score_is_probability false in both branches")

    print(f"{failures} failure(s)")
    return 1 if failures else 0


# --------------------------------------------------------------------------
# HTTP server.
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    schema: ClassVar[dict] = {}
    calibrated: ClassVar[bool] = False

    def log_message(self, fmt, *args):  # quieter, deterministic-ish logging
        sys.stderr.write("mock_api: " + (fmt % args) + "\n")

    def _send(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        path, _, query = self.path.partition("?")
        payloads = build_payloads(self.schema, self.calibrated)
        by_path = {route.split(" ", 1)[1]: body for route, body in payloads.items()}

        if path == "/api/v1/analysis/history":
            limit = 50
            for part in query.split("&"):
                if part.startswith("limit="):
                    try:
                        limit = int(part[len("limit="):])
                    except ValueError:
                        limit = 50
            limit = max(0, min(500, limit))
            entries = [
                analysis_obj(self.calibrated, i, with_targets=False) for i in range(limit)
            ]
            self._send(200, envelope(entries))
            return

        if path in by_path:
            self._send(200, by_path[path])
            return
        # /api/v1/timeframes/{tf}/snapshot
        prefix = "/api/v1/timeframes/"
        if path.startswith(prefix) and path.endswith("/snapshot"):
            tf = path[len(prefix):-len("/snapshot")]
            body = by_path["/api/v1/timeframes/{tf}/snapshot"]
            body["data"]["timeframe"] = tf
            self._send(200, body)
            return

        self._send(404, error_body("not_found", f"unknown route: {path}"))

    def do_POST(self) -> None:
        self._send(405, error_body("method_not_allowed", "only GET is supported"))


def serve(schema: dict, host: str, port: int, calibrated: bool) -> None:
    Handler.schema = schema
    Handler.calibrated = calibrated
    server = HTTPServer((host, port), Handler)
    mode = "CALIBRATED" if calibrated else "UNCALIBRATED"
    print(f"mock ASTRA API v1 ({mode}) on http://{host}:{port}/api/v1/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nmock_api: stopped")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Mock ASTRA backend API v1.")
    parser.add_argument("--host", default="127.0.0.1", help="loopback host")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument(
        "--calibrated",
        action="store_true",
        help="serve the calibrated branch (probability populated)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate synthetic payloads against the frozen schema and exit",
    )
    args = parser.parse_args(argv)

    if args.host != "127.0.0.1":
        print("mock_api: refusing non-loopback host", file=sys.stderr)
        return 2
    with open(SCHEMA_PATH, "r", encoding="utf-8") as handle:
        schema = json.load(handle)

    if args.check:
        return check(schema)
    serve(schema, args.host, args.port, args.calibrated)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
