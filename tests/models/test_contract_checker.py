"""Deterministic tests for the frozen-contract + invariant checker (T23).

Covers the two layers the Lead's E06 ruling requires, the parity of this
module's structural validator with Agent-A's `mock_api.validate_envelope`, and
the teeth of the semantic layer (a clean payload must trip nothing; each frozen
violation must be caught).
"""

from __future__ import annotations

import copy
import json
import os
import sys
import unittest

from src.models import contract_checker as cc

REPO = cc.REPO_ROOT
FIXTURES = os.path.join(REPO, "tests", "fixtures", "api_v1")
sys.path.insert(0, os.path.join(REPO, "scripts"))
import mock_api  # noqa: E402


def load(*parts):
    with open(os.path.join(FIXTURES, *parts), "r", encoding="utf-8") as handle:
        return json.load(handle)


def analysis_default():
    return load("valid", "analysis_latest.json")


def analysis_calibrated():
    return load("valid", "analysis_latest_calibrated.json")


VALID = (
    "system_state.json",
    "health.json",
    "health_v1.json",
    "timeframes.json",
    "timeframe_snapshot.json",
    "analysis_latest.json",
    "analysis_latest_calibrated.json",
    "analysis_history.json",
    "context_latest.json",
    "bridge_status.json",
    "risk_latest.json",
    "shadow_positions.json",
    "shadow_outcomes.json",
    "research_status.json",
    "governance_status.json",
    "audit_recent.json",
)

INVALID = (
    "missing_levels_field.json",
    "bad_coverage_tier_const.json",
    "probability_above_one.json",
    "probability_below_zero.json",
    "not_enveloped.json",
    "missing_envelope_schema_key.json",
    "wrong_envelope_api_const.json",
)

# The route each fixture filename is derived from (mirrors test_api_fixtures.py).
ROUTES = {
    "analysis_latest.json": "GET /api/v1/analysis/latest",
    "analysis_latest_calibrated.json": "GET /api/v1/analysis/latest",
    "analysis_history.json": "GET /api/v1/analysis/history",
    "system_state.json": "GET /api/v1/system/state",
    "health.json": "GET /api/v1/health",
    "health_v1.json": "GET /api/v1/health/v1",
    "timeframes.json": "GET /api/v1/timeframes",
    "timeframe_snapshot.json": "GET /api/v1/timeframes/{tf}/snapshot",
    "context_latest.json": "GET /api/v1/context/latest",
    "bridge_status.json": "GET /api/v1/bridge/status",
    "risk_latest.json": "GET /api/v1/risk/latest",
    "shadow_positions.json": "GET /api/v1/shadow/positions",
    "shadow_outcomes.json": "GET /api/v1/shadow/outcomes",
    "research_status.json": "GET /api/v1/research/status",
    "governance_status.json": "GET /api/v1/governance/status",
    "audit_recent.json": "GET /api/v1/audit/recent",
    "missing_levels_field.json": "GET /api/v1/analysis/latest",
    "bad_coverage_tier_const.json": "GET /api/v1/analysis/latest",
    "probability_above_one.json": "GET /api/v1/analysis/latest",
    "probability_below_zero.json": "GET /api/v1/analysis/latest",
    "not_enveloped.json": "GET /api/v1/analysis/latest",
    "missing_envelope_schema_key.json": "GET /api/v1/analysis/latest",
    "wrong_envelope_api_const.json": "GET /api/v1/analysis/latest",
}


class StructuralLayerTest(unittest.TestCase):
    def setUp(self):
        self.schema = cc.load_schema()
        self.endpoints = self.schema["endpoints"]

    def test_valid_fixtures_pass(self):
        for name in VALID:
            route = ROUTES[name]
            payload = load("valid", name)
            cc.validate_envelope(route, self.endpoints[route], payload)  # must not raise

    def test_invalid_fixtures_rejected(self):
        for name in INVALID:
            route = ROUTES[name]
            payload = load("invalid", name)
            with self.assertRaises(cc.ContractError, msg=name):
                cc.validate_envelope(route, self.endpoints[route], payload)

    def test_rejects_bare_object_and_wrong_envelope(self):
        """Self-contained: the structural layer needs no fixture to have teeth."""
        route = "GET /api/v1/analysis/latest"
        spec = self.endpoints[route]
        with self.assertRaises(cc.ContractError):
            cc.validate_envelope(route, spec, analysis_default()["data"])  # not enveloped
        for mutate in (
            lambda p: p.update(api="v2"),
            lambda p: p.update(schema="2.0"),
            lambda p: p.pop("data"),
        ):
            bad = analysis_default()
            mutate(bad)
            with self.assertRaises(cc.ContractError):
                cc.validate_envelope(route, spec, bad)

    def test_rejects_type_and_range_violations(self):
        route = "GET /api/v1/analysis/latest"
        spec = self.endpoints[route]
        bad_type = analysis_default()
        bad_type["data"]["signal"]["probability"] = 0.5  # needs calibrated true too, but type-ok
        bad_type["data"]["signal"]["probability_calibrated"] = True
        cc.validate_envelope(route, spec, bad_type)  # structurally fine
        out_of_range = analysis_default()
        out_of_range["data"]["signal"]["probability"] = 1.5
        with self.assertRaises(cc.ContractError):
            cc.validate_envelope(route, spec, out_of_range)

    def test_rejects_non_finite_numbers(self):
        """F23-1: the range check alone lets NaN/inf through; parity with the
        mock validator (F22-4b-v) requires a finite guard."""
        route = "GET /api/v1/analysis/latest"
        spec = self.endpoints[route]
        for value in (float("nan"), float("inf"), float("-inf")):
            bad = analysis_default()
            bad["data"]["signal"]["score"] = value
            with self.assertRaises(cc.ContractError, msg=repr(value)):
                cc.validate_envelope(route, spec, bad)
        # probability is bounded; NaN must still be caught (not just out-of-range)
        nan_prob = analysis_default()
        nan_prob["data"]["signal"]["probability"] = float("nan")
        nan_prob["data"]["signal"]["probability_calibrated"] = True
        with self.assertRaises(cc.ContractError):
            cc.validate_envelope(route, spec, nan_prob)

    def test_non_finite_parity_with_mock_validator(self):
        """F23-1 parity: both readers of the schema must reject a non-finite."""
        route = "GET /api/v1/analysis/latest"
        spec = self.endpoints[route]
        bad = analysis_default()
        bad["data"]["signal"]["score"] = float("nan")
        with self.assertRaises(cc.ContractError):
            cc.validate_envelope(route, spec, bad)
        with self.assertRaises(mock_api.SchemaError):
            mock_api.validate_envelope(route, spec, bad)

    def test_rejects_missing_required_top_level(self):
        route = "GET /api/v1/analysis/latest"
        spec = self.endpoints[route]
        bad = analysis_default()
        del bad["data"]["signal"]
        with self.assertRaises(cc.ContractError):
            cc.validate_envelope(route, spec, bad)

    def test_parity_with_mock_api_validator(self):
        """This module's structural reader and Agent-A's mock validator must
        agree on every fixture, so the two readers of one schema cannot drift."""
        for name in VALID + INVALID:
            route = ROUTES[name]
            payload = load("valid", name) if name in VALID else load("invalid", name)

            def _mock(route=route, payload=payload):
                mock_api.validate_envelope(route, self.endpoints[route], payload)

            def _mine(route=route, payload=payload):
                cc.validate_envelope(route, self.endpoints[route], payload)

            mock_error = None
            mine_error = None
            try:
                _mock()
            except Exception as exc:  # noqa: BLE001
                mock_error = exc
            try:
                _mine()
            except cc.ContractError as exc:
                mine_error = exc
            self.assertEqual(
                mock_error is None,
                mine_error is None,
                f"validator disagreement on {name}",
            )


class SemanticLayerTest(unittest.TestCase):
    def test_default_and_calibrated_trip_nothing(self):
        self.assertEqual(cc.frozen_violations(analysis_default()["data"]), [])
        self.assertEqual(cc.frozen_violations(analysis_calibrated()["data"]), [])

    def test_semantic_fixtures_trip(self):
        for name in ("uncalibrated_claims_probability.json",
                     "uncalibrated_non_null_levels.json"):
            data = load("semantic", name)["data"]
            self.assertTrue(cc.frozen_violations(data), name)

    def test_dynamic_fields_are_not_falsely_flagged(self):
        """F22-3: live fields (symbol/timestamp/degraded/score/direction/regime)
        are real runtime state; changing them must not trip an invariant."""
        mutated = analysis_default()
        data = mutated["data"]
        data["symbol"] = "EURUSD"
        data["timestamp"] = "2030-01-01T00:00:00Z"
        data["meta"]["degraded"] = False
        data["meta"]["disclaimer"] = "other"
        data["signal"]["score"] = -123.4
        data["signal"]["direction"] = "DOWN"
        data["context"]["regime"] = "TRENDING"
        data["context"]["h4_bias"] = "UP"
        data["context"]["m15_trigger"] = "UP"
        data["context"]["volatility_state"] = "HIGH"
        self.assertEqual(cc.frozen_violations(data), [])

    def test_every_frozen_null_is_enforced(self):
        """Teeth: populating each frozen field one at a time must be caught."""
        paths = (
            ("signal", "horizon"),
            ("signal", "confidence_lo"),
            ("signal", "confidence_hi"),
            ("signal", "model_version"),
            ("meta", "data_freshness_sec"),
            ("context", "mtf_agreement"),
        )
        for parent, key in paths:
            bad = analysis_default()
            bad["data"][parent][key] = 1
            self.assertTrue(cc.frozen_violations(bad["data"]), f"{parent}.{key}")
        for key in cc.FROZEN_NULL_LEVELS:
            bad = analysis_default()
            bad["data"]["levels"][key] = 1
            self.assertTrue(cc.frozen_violations(bad["data"]), f"levels.{key}")

    def test_score_is_probability_must_be_false(self):
        bad = analysis_default()
        bad["data"]["meta"]["score_is_probability"] = True
        violations = cc.frozen_violations(bad["data"])
        self.assertTrue(any("score_is_probability" in v for v in violations))

    def test_null_probability_requires_calibrated_false(self):
        bad = analysis_default()
        bad["data"]["signal"]["probability_calibrated"] = True
        violations = cc.frozen_violations(bad["data"])
        self.assertTrue(any("probability_calibrated" in v for v in violations))

    def test_non_null_probability_requires_calibrated_true(self):
        bad = analysis_default()
        bad["data"]["signal"]["probability"] = 0.7
        violations = cc.frozen_violations(bad["data"])
        self.assertTrue(any("probability_calibrated" in v for v in violations))

    def test_missing_frozen_field_is_a_violation(self):
        bad = analysis_default()
        del bad["data"]["levels"]["sl_method"]
        violations = cc.frozen_violations(bad["data"])
        self.assertTrue(any("levels.sl_method" in v for v in violations))

    # DEC-022 / D2: `levels` is conditional on a live proposal.

    def test_populated_levels_accepted_when_proposal_available(self):
        """Positive posture: a fully-populated value-level object is clean."""
        good = analysis_default()
        for key in cc.FROZEN_VALUE_LEVELS:
            good["data"]["levels"][key] = 1900.0
        self.assertEqual(cc.frozen_violations(good["data"]), [])

    def test_proposal_available_flag_accepts_populated_levels(self):
        """Explicit `proposal_available:true` also opens the populated posture."""
        good = analysis_default()
        good["data"]["proposal_available"] = True
        for key in cc.FROZEN_VALUE_LEVELS:
            good["data"]["levels"][key] = 1900.0
        self.assertEqual(cc.frozen_violations(good["data"]), [])

    def test_partially_populated_levels_still_rejected(self):
        """A mixed value-level object is incoherent in either posture."""
        bad = analysis_default()
        bad["data"]["levels"]["stop_loss"] = 1900.0  # only one populated
        violations = cc.frozen_violations(bad["data"])
        self.assertTrue(violations)
        self.assertTrue(any("take_profit" in v or "no-proposal" in v for v in violations))

    def test_null_value_level_with_proposal_is_rejected(self):
        """With a proposal available, a null value level is the reverse violation."""
        bad = analysis_default()
        bad["data"]["proposal_available"] = True
        for key in cc.FROZEN_VALUE_LEVELS:
            bad["data"]["levels"][key] = 1900.0
        bad["data"]["levels"]["take_profit"] = None
        violations = cc.frozen_violations(bad["data"])
        self.assertTrue(any("take_profit" in v and "null" in v for v in violations))

    def test_method_identifiers_frozen_in_both_postures(self):
        """sl_method/tp_method stay null even when a proposal is available."""
        bad = analysis_default()
        bad["data"]["proposal_available"] = True
        for key in cc.FROZEN_VALUE_LEVELS:
            bad["data"]["levels"][key] = 1900.0
        bad["data"]["levels"]["sl_method"] = "atr"
        violations = cc.frozen_violations(bad["data"])
        self.assertTrue(any("sl_method" in v for v in violations))


class HistoryScopeTest(unittest.TestCase):
    """F23-2 / AUDIT-HISTORY: the frozen-null set applies per history entry."""

    def test_clean_history_trips_nothing(self):
        payload = load("valid", "analysis_history.json")
        self.assertEqual(cc.history_violations(payload), [])
        self.assertEqual(
            cc.analysis_contract_violations(payload, endpoint=cc.HISTORY_ENDPOINT), []
        )

    def test_populated_model_version_in_entry_fails(self):
        """The exact drift Agent-D found: a history entry with model_version set."""
        payload = load("valid", "analysis_history.json")
        payload["data"][0]["signal"]["model_version"] = "logistic-t03"
        violations = cc.history_violations(payload)
        self.assertTrue(any("model_version" in v for v in violations))
        combined = cc.analysis_contract_violations(
            payload, endpoint=cc.HISTORY_ENDPOINT
        )
        self.assertTrue(any("model_version" in v for v in combined))

    def test_multi_entry_reports_index(self):
        payload = load("valid", "analysis_history.json")
        entry = copy.deepcopy(payload["data"][0])
        entry["levels"]["sl_method"] = "atr"
        payload["data"].append(entry)
        violations = cc.history_violations(payload)
        self.assertTrue(any(v.startswith("data[1]:") for v in violations), violations)

    def test_latest_scoped_call_still_uses_data_object(self):
        """`/analysis/latest` semantics must be unchanged by the history addition."""
        self.assertEqual(cc.analysis_contract_violations(analysis_default()), [])


class CombinedEntryPointTest(unittest.TestCase):
    def test_clean_default_has_no_violations(self):
        self.assertEqual(cc.analysis_contract_violations(analysis_default()), [])
        self.assertEqual(cc.analysis_contract_violations(analysis_calibrated()), [])

    def test_structural_failure_short_circuits(self):
        violations = cc.analysis_contract_violations(load("invalid", "not_enveloped.json"))
        self.assertEqual(len(violations), 1)
        self.assertTrue(violations[0].startswith("structure:"))

    def test_semantic_failure_is_reported_with_prefix(self):
        violations = cc.analysis_contract_violations(
            load("semantic", "uncalibrated_non_null_levels.json")
        )
        self.assertTrue(all(v.startswith("invariant:") for v in violations))
        self.assertTrue(violations)

    def test_require_valid_analysis_raises_on_violation(self):
        bad = analysis_default()
        bad["data"]["meta"]["score_is_probability"] = True
        with self.assertRaises(cc.ContractError):
            cc.require_valid_analysis(bad)

    def test_require_valid_analysis_passes_clean(self):
        cc.require_valid_analysis(analysis_default())  # must not raise

    def test_violations_do_not_mutate_input(self):
        payload = analysis_default()
        before = copy.deepcopy(payload)
        cc.analysis_contract_violations(payload)
        cc.frozen_violations(payload["data"])
        self.assertEqual(payload, before)


if __name__ == "__main__":
    unittest.main()
