"""Tests for the isolated SMC engine (advisor/smc_engine.py).

Every test stubs the provider and the upstream fetches: the engine's own logic
(prompt assembly, plan validation with the R:R floor, honest stamps, isolation)
is what is under test, never a live model or a live bridge.
"""

from __future__ import annotations

import json
import math
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ai_advisor  # noqa: E402
import smc_engine  # noqa: E402


def bars(count: int = 120, start: float = 2000.0, slope: float = 0.3) -> list:
    """A deterministic series the reads can consume."""
    out = []
    for i in range(count):
        base = start + slope * i
        wobble = math.sin(i * 0.7) * 0.8
        op = base + wobble
        close = base + slope * 0.6
        high = max(op, close) + 0.6
        low = min(op, close) - 0.6
        out.append({"time": 1760000000 + i * 900, "open": op, "high": high,
                    "low": low, "close": close})
    return out


def reply(**over) -> str:
    body = {
        "market_read": "قراءة السوق.",
        "bias": "LONG",
        "news_caution": False,
        "key_event": "",
        "plans": [{
            "direction": "LONG", "entry": 2000.0, "stop": 1995.0,
            "targets": [2010.0, 2015.0], "confidence": 0.6,
            "rationale": "سبب", "sweep": "سيولة", "poi": "بلوك",
        }],
    }
    body.update(over)
    return json.dumps(body)


class ValidatePlanTests(unittest.TestCase):
    def test_a_coherent_long_is_kept(self):
        plan, reason = smc_engine.validate_plan({
            "direction": "LONG", "entry": 2000, "stop": 1995,
            "targets": [2010, 2015], "confidence": 0.6})
        self.assertEqual(reason, "ok")
        self.assertEqual(plan["reward_risk"], 3.0)
        self.assertFalse(plan["confidence_validated"])

    def test_a_coherent_short_is_kept(self):
        plan, reason = smc_engine.validate_plan({
            "direction": "SHORT", "entry": 2000, "stop": 2005,
            "targets": [1990, 1985]})
        self.assertEqual(reason, "ok")
        self.assertEqual(plan["reward_risk"], 3.0)

    def test_a_long_stop_above_entry_is_dropped(self):
        plan, reason = smc_engine.validate_plan({
            "direction": "LONG", "entry": 2000, "stop": 2005,
            "targets": [2010]})
        self.assertIsNone(plan)
        self.assertIn("stop", reason)

    def test_unordered_targets_are_dropped(self):
        plan, reason = smc_engine.validate_plan({
            "direction": "LONG", "entry": 2000, "stop": 1995,
            "targets": [2015, 2010]})
        self.assertIsNone(plan)
        self.assertIn("ascending", reason)

    def test_a_first_target_behind_the_entry_is_dropped(self):
        plan, reason = smc_engine.validate_plan({
            "direction": "LONG", "entry": 2000, "stop": 1995,
            "targets": [1998, 2015]})
        self.assertIsNone(plan)
        self.assertIn("first target", reason)

    def test_a_plan_below_the_reward_risk_floor_is_dropped(self):
        # Risk 10, reward 15 -> 1.5:1, under the methodology's 1:2 minimum.
        plan, reason = smc_engine.validate_plan({
            "direction": "LONG", "entry": 2000, "stop": 1990,
            "targets": [2015]})
        self.assertIsNone(plan)
        self.assertIn("reward-to-risk", reason)

    def test_the_floor_is_configurable(self):
        _plan, reason = smc_engine.validate_plan({
            "direction": "LONG", "entry": 2000, "stop": 1990,
            "targets": [2015]}, min_rr=1.0)
        self.assertEqual(reason, "ok")

    def test_a_scalar_target_is_accepted(self):
        _plan, reason = smc_engine.validate_plan({
            "direction": "LONG", "entry": 2000, "stop": 1995,
            "targets": 2015})
        self.assertEqual(reason, "ok")

    def test_nonnumeric_levels_are_dropped(self):
        plan, reason = smc_engine.validate_plan({
            "direction": "LONG", "entry": "2000", "stop": 1995,
            "targets": [2015]})
        self.assertIsNone(plan)
        self.assertIn("entry", reason)

    def test_confidence_is_clamped_and_may_be_absent(self):
        plan, _ = smc_engine.validate_plan({
            "direction": "LONG", "entry": 2000, "stop": 1995,
            "targets": [2015], "confidence": 5.0})
        self.assertEqual(plan["confidence"], 1.0)
        plan2, _ = smc_engine.validate_plan({
            "direction": "LONG", "entry": 2000, "stop": 1995,
            "targets": [2015]})
        self.assertIsNone(plan2["confidence"])


class ParseEngineTests(unittest.TestCase):
    def test_plain_json(self):
        parsed = smc_engine.parse_engine(reply())
        self.assertEqual(parsed["bias"], "LONG")
        self.assertEqual(len(parsed["plans"]), 1)

    def test_fenced_and_prose_wrapped_json(self):
        wrapped = "Here you go:\n```json\n" + reply() + "\n```\nDone."
        parsed = smc_engine.parse_engine(wrapped)
        self.assertIsNotNone(parsed)
        self.assertEqual(len(parsed["plans"]), 1)

    def test_garbage_yields_none(self):
        self.assertIsNone(smc_engine.parse_engine("not json at all"))
        self.assertIsNone(smc_engine.parse_engine(""))

    def test_an_unknown_bias_falls_back_to_neutral(self):
        parsed = smc_engine.parse_engine(reply(bias="SIDEWAYS"))
        self.assertEqual(parsed["bias"], "NEUTRAL")

    def test_a_bad_plan_is_reported_not_repaired(self):
        bad = {"direction": "LONG", "entry": 2000, "stop": 2010,
               "targets": [2015]}
        parsed = smc_engine.parse_engine(reply(plans=[bad]))
        self.assertEqual(parsed["plans"], [])
        self.assertEqual(len(parsed["rejected"]), 1)

    def test_the_plan_limit_is_enforced(self):
        good = {"direction": "LONG", "entry": 2000, "stop": 1995,
                "targets": [2015]}
        parsed = smc_engine.parse_engine(reply(plans=[good] * 5))
        self.assertLessEqual(len(parsed["plans"]), smc_engine.MAX_PLANS)

    def test_an_empty_plan_list_is_allowed(self):
        parsed = smc_engine.parse_engine(reply(plans=[]))
        self.assertEqual(parsed["plans"], [])


class AssembleTests(unittest.TestCase):
    def _snapshot(self):
        return {
            "symbol": "XAUUSD", "price": 2001.0,
            "facts": {"timeframes": {"M15": {}}, "missing_timeframes": ["H4"],
                      "facts_note": "note"},
            "news": [], "events": [],
        }

    def test_distance_from_price_is_recorded(self):
        parsed = smc_engine.parse_engine(reply())
        out = smc_engine.assemble(self._snapshot(), parsed)
        self.assertEqual(out["plans"][0]["distance_from_price"], -1.0)

    def test_the_engine_block_names_the_isolation_and_the_floor(self):
        parsed = smc_engine.parse_engine(reply())
        out = smc_engine.assemble(self._snapshot(), parsed)
        note = out["engine"]["note"]
        self.assertIn("isolated", note)
        self.assertIn("no orders", note)
        self.assertEqual(out["engine"]["min_reward_risk"], smc_engine.MIN_RR)
        self.assertEqual(out["engine"]["timeframes_missing"], ["H4"])


class BuildTests(unittest.TestCase):
    def _patch(self, api_key="key", model_reply=None):
        return (
            mock.patch.object(ai_advisor, "provider_key", return_value=api_key),
            mock.patch.object(ai_advisor, "rate_limited", return_value=False),
            mock.patch.object(smc_engine, "build_snapshot",
                              return_value={"symbol": "XAUUSD", "price": 2000.0,
                                            "facts": {"timeframes": {},
                                                      "missing_timeframes": [],
                                                      "facts_note": "n"},
                                            "news": [], "events": []}),
            mock.patch.object(ai_advisor, "call_model",
                              return_value=model_reply),
            mock.patch.object(smc_engine, "append_log"),
        )

    def test_unconfigured_reports_the_snapshot_without_plans(self):
        with mock.patch.object(ai_advisor, "provider_key", return_value=""):
            result = smc_engine.Engine()._build()
        self.assertFalse(result["configured"])
        self.assertIsNone(result["analysis"])
        self.assertIn("not configured", result["note"])

    def test_configured_returns_plans_and_an_honest_stamp(self):
        p1, p2, p3, p4, p5 = self._patch(model_reply=reply())
        with p1, p2, p3, p4, p5:
            result = smc_engine.Engine()._build()
        self.assertFalse(result["validated"])
        self.assertEqual(result["framework"], "SMC/ICT")
        self.assertEqual(len(result["analysis"]["plans"]), 1)

    def test_an_unusable_reply_produces_no_plans(self):
        p1, p2, p3, p4, p5 = self._patch(model_reply="not json")
        with p1, p2, p3, p4, p5:
            result = smc_engine.Engine()._build()
        self.assertIsNone(result["analysis"])
        self.assertIn("unavailable", result["note"])

    def test_a_rate_limited_provider_pauses_the_engine(self):
        with mock.patch.object(ai_advisor, "provider_key", return_value="k"), \
             mock.patch.object(ai_advisor, "rate_limited", return_value=True), \
             mock.patch.object(smc_engine, "build_snapshot",
                               return_value={"symbol": "XAUUSD", "price": None,
                                             "facts": None, "news": [],
                                             "events": []}):
            result = smc_engine.Engine()._build()
        self.assertTrue(result["rate_limited"])
        self.assertIn("quota", result["note"])

    def test_a_rejected_key_is_named_not_collapsed(self):
        # The provider refusing the key is a distinct, operator-visible failure;
        # it must be named (with the provider's words), not folded into the
        # generic "no usable reply".
        p1, p2, p3, p4, p5 = self._patch(model_reply=None)
        with p1, p2, p3, p4, \
             mock.patch.object(ai_advisor, "auth_failure",
                               return_value={"code": 401,
                                             "message": "key expired"}), \
             p5:
            result = smc_engine.Engine()._build()
        self.assertIsNone(result["analysis"])
        self.assertTrue(result.get("auth_failed"))
        self.assertIn("rejected", result["note"])
        self.assertIn("expired", result["note"])

    def test_no_key_means_no_model_call(self):
        with mock.patch.object(ai_advisor, "provider_key", return_value=""), \
             mock.patch.object(smc_engine, "build_snapshot",
                               return_value={"symbol": "XAUUSD", "price": None,
                                             "facts": None, "news": [],
                                             "events": []}), \
             mock.patch.object(ai_advisor, "call_model") as call:
            smc_engine.Engine()._build()
        call.assert_not_called()


class IsolationTests(unittest.TestCase):
    def test_the_engine_imports_no_engine_or_ledger_module(self):
        # The whole point is that it is a separate process that only reads. It
        # must not pull in the prediction ledger, the C++ engine binding, or any
        # order-execution path.
        with open(os.path.join(HERE, "smc_engine.py"), encoding="utf-8") as fh:
            source = fh.read()
        for forbidden in ("import ledger", "PredictionLedger", "place_order",
                          "execute_order", "import aura", "import engine"):
            self.assertNotIn(forbidden, source,
                             "the isolated engine must not use %r" % forbidden)

    def test_the_only_outbound_hosts_are_loopback_and_the_provider(self):
        # Reads go to loopback only; the single outbound call is the provider,
        # which lives in ai_advisor. This module itself opens no other socket.
        with open(os.path.join(HERE, "smc_engine.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertNotIn("http://localhost", source)
        self.assertNotIn("https://", source)  # provider URLs stay in ai_advisor
        self.assertIn("127.0.0.1", ai_advisor.BRIDGE_BASE)

    def test_the_engine_writes_only_its_own_log(self):
        # The only file the engine opens is its own plan log. Anything else
        # would be a write into the pipeline it is meant to stay out of.
        with open(os.path.join(HERE, "smc_engine.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn('open(LOG_PATH, "a"', source)
        self.assertEqual(source.count("open("), 1)

    def test_the_fact_sheet_is_deterministic_and_opinion_free(self):
        # smc_read must compute from bars alone: no network, no model. It may
        # use time.gmtime to bucket session windows, but it never reads the
        # wall clock (time.time / datetime.now) or opens a socket.
        with open(os.path.join(HERE, "smc_read.py"), encoding="utf-8") as fh:
            source = fh.read()
        for forbidden in ("urllib", "requests", "socket", "http",
                          "time.time", "datetime.now", "import random"):
            self.assertNotIn(forbidden, source,
                             "the fact sheet must not reach outside the bars")


class PromptTests(unittest.TestCase):
    def test_the_system_prompt_states_the_coherence_rule(self):
        for piece in ("stop < entry", "LONG", "SHORT", "confidence",
                      "not a measured number"):
            with self.subTest(piece=piece):
                self.assertIn(piece, smc_engine._SYSTEM_PROMPT)

    def test_the_prompt_keeps_the_smc_methodology(self):
        for piece in ("CHoCH", "BOS", "order block", "fair-value gap",
                      "premium", "discount", "liquidity", "sweep"):
            with self.subTest(piece=piece):
                self.assertIn(piece, smc_engine._SYSTEM_PROMPT)

    def test_the_prompt_forbids_retail_indicators(self):
        self.assertIn("do NOT use retail indicators",
                      smc_engine._SYSTEM_PROMPT)

    def test_the_prompt_requires_the_two_to_one_floor(self):
        self.assertIn("1:2", smc_engine._SYSTEM_PROMPT)

    def test_the_prompt_warns_about_high_impact_news(self):
        self.assertIn("CPI", smc_engine._SYSTEM_PROMPT)
        self.assertIn("FOMC", smc_engine._SYSTEM_PROMPT)

    def test_the_prompt_view_carries_the_fact_sheet(self):
        snapshot = {"symbol": "XAUUSD", "price": 2000.0,
                    "facts": {"timeframes": {"M15": {}}},
                    "news": [], "events": []}
        view = smc_engine._prompt_view(snapshot)
        self.assertIn("smc_facts", view)
        self.assertEqual(view["smc_facts"], {"timeframes": {"M15": {}}})


class TimeframeSelectionTests(unittest.TestCase):
    def test_default_is_the_five_structural_frames(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ASTRA_SMC_TIMEFRAMES", None)
            self.assertEqual(smc_engine._resolve_timeframes(),
                             smc_engine.ALL_TIMEFRAMES)

    def test_an_allow_list_narrows_the_frames_in_canonical_order(self):
        with mock.patch.dict(os.environ,
                             {"ASTRA_SMC_TIMEFRAMES": "H4, M5 ,bogus"}):
            self.assertEqual(smc_engine._resolve_timeframes(), ("M5", "H4"))

    def test_an_empty_allow_list_falls_back_to_all(self):
        with mock.patch.dict(os.environ, {"ASTRA_SMC_TIMEFRAMES": ",, "}):
            self.assertEqual(smc_engine._resolve_timeframes(),
                             smc_engine.ALL_TIMEFRAMES)


if __name__ == "__main__":
    unittest.main()
