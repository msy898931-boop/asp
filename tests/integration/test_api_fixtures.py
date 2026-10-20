#!/usr/bin/env python3
"""T22 - analysis-API schema fixtures: validate every fixture against the frozen schema.

Every file under tests/fixtures/api_v1/ is derived from
docs/architecture/API_V1_SCHEMA.json. This checker enforces that derivation:

  1. each valid/ payload passes mock_api.validate_envelope (envelope + data);
  2. each invalid/ payload is REJECTED (a fixture that disagrees with the
     schema is a bug in the fixture, so a fixture that accidentally validates
     is itself a failure);
  3. each errors/ body matches the schema error_schema;
  4. the frozen-null contract holds in the uncalibrated default:
     probability/probability_calibrated/levels.*/meta.score_is_probability
     are null/false, and score is present (E07 / RULE C).

Exit code 0 = all checks passed.
"""

from __future__ import annotations

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
FIXTURES = os.path.join(REPO, "tests", "fixtures", "api_v1")
sys.path.insert(0, os.path.join(REPO, "scripts"))
import mock_api  # noqa: E402

# Maps a fixture filename to the schema route it is derived from.
VALID_ROUTES = {
    "system_state.json": "GET /api/v1/system/state",
    "health.json": "GET /api/v1/health",
    "health_v1.json": "GET /api/v1/health/v1",
    "timeframes.json": "GET /api/v1/timeframes",
    "timeframe_snapshot.json": "GET /api/v1/timeframes/{tf}/snapshot",
    "analysis_latest.json": "GET /api/v1/analysis/latest",
    "analysis_latest_calibrated.json": "GET /api/v1/analysis/latest",
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

_results = []


def check(name, ok, detail=""):
    _results.append(ok)
    print(("[PASS] " if ok else "[FAIL] ") + name + ("" if ok else f" - {detail}"))


def load(rel):
    with open(os.path.join(FIXTURES, rel), "r", encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    with open(mock_api.SCHEMA_PATH, "r", encoding="utf-8") as handle:
        schema = json.load(handle)
    endpoints = schema["endpoints"]

    for name, route in VALID_ROUTES.items():
        payload = load(os.path.join("valid", name))
        try:
            mock_api.validate_envelope(route, endpoints[route], payload)
            check(f"valid/{name} conforms to {route}", True)
        except Exception as exc:  # noqa: BLE001
            check(f"valid/{name} conforms to {route}", False, str(exc))

    for name, route in INVALID_ROUTES.items():
        payload = load(os.path.join("invalid", name))
        try:
            mock_api.validate_envelope(route, endpoints[route], payload)
            check(f"invalid/{name} is REJECTED", False, "unexpectedly validated")
        except Exception:  # noqa: BLE001
            check(f"invalid/{name} is REJECTED", True)

    # semantic/: structurally schema-valid, but they violate a contract
    # invariant the schema cannot encode (a schema cannot say "this boolean
    # must be false when probability is null"). The fixture is correct only if
    # it passes structural validation AND trips the invariant check below.
    for name, route in (("uncalibrated_claims_probability.json", "GET /api/v1/analysis/latest"),
                        ("uncalibrated_non_null_levels.json", "GET /api/v1/analysis/latest")):
        payload = load(os.path.join("semantic", name))
        try:
            mock_api.validate_envelope(route, endpoints[route], payload)
            check(f"semantic/{name} is structurally schema-valid", True)
        except Exception as exc:  # noqa: BLE001
            check(f"semantic/{name} is structurally schema-valid", False, str(exc))

    def invariant_violations(data):
        """Full frozen-null invariant set (E06). The schema cannot express these
        conditional requirements, so they live here. Agent-C's F17-1 check should
        consume this helper rather than re-implement it.

        Two classes:
          * unconditional frozen nulls — null this release regardless of
            calibration (horizon, confidence_lo/hi, model_version, every
            levels.*, meta.data_freshness_sec, context.mtf_agreement);
          * conditional — score_is_probability is always false, and a non-null
            probability requires probability_calibrated:true.
        """
        out = []
        sig, meta, lv, ctx = (data["signal"], data["meta"], data["levels"],
                              data["context"])
        for key in ("horizon", "confidence_lo", "confidence_hi", "model_version"):
            if sig.get(key) is not None:
                out.append(f"signal.{key} is non-null (frozen null this release)")
        for key, val in lv.items():
            # `targets` is the scaled-target ladder: structurally an empty list
            # in the no-proposal posture, never null (the host emits `[]`).
            if key == "targets":
                if val != []:
                    out.append("levels.targets is not empty (frozen posture)")
            elif val is not None:
                out.append(f"levels.{key} is non-null (frozen null this release)")
        if meta.get("data_freshness_sec") is not None:
            out.append("meta.data_freshness_sec is non-null (frozen null this release)")
        if ctx.get("mtf_agreement") is not None:
            out.append("context.mtf_agreement is non-null (frozen null this release)")
        if meta.get("score_is_probability") is not False:
            out.append("score_is_probability is not false (E07)")
        if sig["probability"] is None:
            if sig["probability_calibrated"] is not False:
                out.append("probability null but probability_calibrated is not false")
        else:
            if sig["probability_calibrated"] is not True:
                out.append("non-null probability without probability_calibrated:true")
        return out

    for name in ("uncalibrated_claims_probability.json", "uncalibrated_non_null_levels.json"):
        data = load(os.path.join("semantic", name))["data"]
        v = invariant_violations(data)
        check(f"semantic/{name} trips an invariant", len(v) > 0)

    # and the valid default trips no invariant
    check("valid/analysis_latest.json trips no invariant",
          invariant_violations(load(os.path.join("valid", "analysis_latest.json"))["data"]) == [])

    # F-HIST-1: the frozen-null set is per-entry, and it applies to history too.
    # The latest-only surface hid a populated model_version/features_contributing
    # in the history fixture; guard every entry here so it cannot recur.
    for i, entry in enumerate(load(os.path.join("valid", "analysis_history.json"))["data"]):
        v = invariant_violations(entry)
        check(f"valid/analysis_history.json[{i}] trips no invariant (F-HIST-1)",
              not v, "; ".join(v))

    error_schema = schema["error_schema"]
    for name in ("503_missing_dependency.json", "404_unknown_route.json",
                 "405_method_not_allowed.json"):
        body = load(os.path.join("errors", name))
        ok = all(k in body for k in error_schema["required"]) and body.get("error") == "true"
        check(f"errors/{name} matches error_schema", ok, str(body))

    # Frozen-null contract (E07 / RULE C): the uncalibrated default must not
    # present a score as a probability, and must not carry SL/TP levels.
    uncal = load(os.path.join("valid", "analysis_latest.json"))["data"]
    sig, meta, lv = uncal["signal"], uncal["meta"], uncal["levels"]
    check("uncalibrated: probability is null", sig["probability"] is None)
    check("uncalibrated: probability_calibrated is false",
          sig["probability_calibrated"] is False)
    check("uncalibrated: score is present", sig["score"] is not None)
    check("uncalibrated: score_is_probability is false",
          meta["score_is_probability"] is False)
    check("uncalibrated: every level is null",
          all(v is None for k, v in lv.items() if k != "targets")
          and lv.get("targets") == [])
    # F22-1: model_version is a frozen null this release (BACKEND_FRONTEND_API_V1.md)
    check("uncalibrated: model_version is null (F22-1)",
          sig["model_version"] is None)
    check("uncalibrated: features_contributing is empty (F22-1)",
          sig["features_contributing"] == [])

    cal = load(os.path.join("valid", "analysis_latest_calibrated.json"))["data"]
    check("calibrated: probability in [0,1]",
          isinstance(cal["signal"]["probability"], (int, float))
          and 0.0 <= cal["signal"]["probability"] <= 1.0)
    check("calibrated: probability_calibrated is true",
          cal["signal"]["probability_calibrated"] is True)
    check("calibrated: score_is_probability still false (E07)",
          cal["meta"]["score_is_probability"] is False)
    # F22-1b: the calibrated branch must trip NO invariant either — the frozen
    # nulls hold regardless of calibration.
    check("calibrated: trips no invariant (F22-1b)",
          invariant_violations(cal) == [])
    # and the frozen-null fields must be identical between the two branches
    frozen_paths = [
        ("signal", "horizon"), ("signal", "confidence_lo"),
        ("signal", "confidence_hi"), ("signal", "model_version"),
        ("meta", "data_freshness_sec"), ("context", "mtf_agreement"),
    ]
    for a, b in frozen_paths:
        check(f"frozen-null {a}.{b} identical across branches",
              uncal[a][b] == cal[a][b] is None)
    check("levels identical (all null) across branches",
          uncal["levels"] == cal["levels"])

    # The two fixtures are two snapshots of the same shape. Everything outside
    # the frozen-null set and the calibration-sanctioned fields must match, so a
    # drift in either file is caught. Allowed differences, each justified:
    #   - calibration itself: probability, probability_calibrated, coverage_tier
    #   - live snapshot state: timestamp, context.{regime,h4_bias,m15_trigger,
    #     volatility_state}, signal.{direction,score}, meta.{degraded,disclaimer}
    allowed = {
        ("signal", "probability"), ("signal", "probability_calibrated"),
        ("meta", "coverage_tier"),
        ("timestamp",), ("signal", "direction"), ("signal", "score"),
        ("meta", "degraded"), ("meta", "disclaimer"),
    }
    allowed_ctx = {"regime", "h4_bias", "m15_trigger", "volatility_state"}

    def leaf_paths(obj, prefix=()):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield from leaf_paths(v, prefix + (k,))
        else:
            yield prefix, obj

    changed = {p for p, v in leaf_paths(uncal)
               if dict(leaf_paths(cal)).get(p) != v}
    unexpected = changed - allowed - {("context", k) for k in allowed_ctx}
    check("branch diff is only calibration + documented live fields",
          not unexpected, f"unexpected differing fields: {sorted(unexpected)}")

    # F22-4b (in-zone hardening): the schema validator cannot reject NaN (JSON
    # has no NaN literal, and NaN compares false against min/max). No fixture may
    # carry a non-finite number; this is the fixture-side guard. The validator
    # itself (scripts/mock_api.py, Agent-C's zone) still needs a finite guard for
    # untrusted input — reported to Agent-C.
    def non_finite(obj, path=()):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield from non_finite(v, path + (k,))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                yield from non_finite(v, path + (i,))
        elif isinstance(obj, float) and not math.isfinite(obj):
            yield path

    bad = []

    def scan(dirname):
        for name in sorted(os.listdir(os.path.join(FIXTURES, dirname))):
            if name.endswith(".json"):
                bad.extend(non_finite(load(os.path.join(dirname, name))))

    for d in ("valid", "invalid", "semantic", "errors"):
        scan(d)
    check("no fixture carries NaN/inf (F22-4b)", not bad, f"{bad}")

    failed = _results.count(False)
    print(f"\n{len(_results)} check(s), {failed} failed")
    print("RESULT: " + ("PASS" if failed == 0 else "FAIL"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
