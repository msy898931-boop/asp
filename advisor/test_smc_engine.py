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
from typing import ClassVar
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


class AnchorTests(unittest.TestCase):
    """The entry-anchoring check: a plan must enter at a computed zone."""

    def _sheet(self, blocks=None, gaps=None, atr=5.0):
        return {"M15": {"atr": atr, "order_blocks": blocks or [],
                        "fair_value_gaps": gaps or []}}

    def test_an_entry_inside_an_order_block_is_anchored(self):
        sheet = self._sheet(blocks=[{"kind": "bullish", "low": 1998.0,
                                     "high": 2002.0}])
        tf, zone = smc_engine.entry_anchor(2000.0, sheet)
        self.assertEqual(tf, "M15")
        self.assertIn("order block", zone)

    def test_an_entry_inside_a_gap_is_anchored(self):
        sheet = self._sheet(gaps=[{"kind": "bullish", "low": 1999.0,
                                   "high": 2001.0}])
        _tf, zone = smc_engine.entry_anchor(2000.0, sheet)
        self.assertIn("fair-value gap", zone)

    def test_an_entry_in_empty_space_is_not_anchored(self):
        sheet = self._sheet(blocks=[{"kind": "bullish", "low": 1990.0,
                                     "high": 1992.0}])
        tf, zone = smc_engine.entry_anchor(2000.0, sheet)
        self.assertIsNone(tf)
        self.assertIsNone(zone)

    def test_the_tolerance_scales_with_the_frames_atr(self):
        # Entry 2 points above a zone: inside 0.5*ATR when ATR is 5, outside
        # when ATR is 2 — a quiet frame demands a tighter match.
        sheet_loose = self._sheet(blocks=[{"kind": "bullish", "low": 1996.0,
                                           "high": 1998.0}], atr=5.0)
        sheet_tight = self._sheet(blocks=[{"kind": "bullish", "low": 1996.0,
                                           "high": 1998.0}], atr=2.0)
        self.assertIsNotNone(smc_engine.entry_anchor(2000.0, sheet_loose)[1])
        self.assertIsNone(smc_engine.entry_anchor(2000.0, sheet_tight)[1])

    def test_a_plan_is_dropped_when_its_entry_is_not_anchored(self):
        sheet = self._sheet(blocks=[{"kind": "bullish", "low": 1990.0,
                                     "high": 1992.0}])
        plan, reason = smc_engine.validate_plan(
            {"direction": "LONG", "entry": 2000, "stop": 1995,
             "targets": [2010, 2020], "confidence": 0.6}, facts_by_tf=sheet)
        self.assertIsNone(plan)
        self.assertIn("not anchored", reason)

    def test_a_plan_is_kept_when_its_entry_is_anchored(self):
        sheet = self._sheet(blocks=[{"kind": "bullish", "low": 1998.0,
                                     "high": 2002.0}])
        plan, _reason = smc_engine.validate_plan(
            {"direction": "LONG", "entry": 2000, "stop": 1995,
             "targets": [2010, 2020], "confidence": 0.6}, facts_by_tf=sheet)
        self.assertIsNotNone(plan)
        self.assertTrue(plan["anchored"])
        self.assertEqual(plan["anchor_timeframe"], "M15")

    def test_no_sheet_means_the_check_is_skipped_not_failed(self):
        # A caller with no sheet (a bare reply) still gets its plan, and the
        # plan records that it was not anchored rather than pretending it was.
        plan, _reason = smc_engine.validate_plan(
            {"direction": "LONG", "entry": 2000, "stop": 1995,
             "targets": [2010, 2020], "confidence": 0.6})
        self.assertIsNotNone(plan)
        self.assertFalse(plan["anchored"])
        self.assertIsNone(plan["anchor_zone"])

    def test_the_check_can_be_turned_off_explicitly(self):
        sheet = self._sheet(blocks=[{"kind": "bullish", "low": 1990.0,
                                     "high": 1992.0}])
        plan, _reason = smc_engine.validate_plan(
            {"direction": "LONG", "entry": 2000, "stop": 1995,
             "targets": [2010, 2020], "confidence": 0.6},
            facts_by_tf=sheet, require_anchor=False)
        self.assertIsNotNone(plan)

    def test_a_bad_plan_is_still_dropped_before_the_anchor_check(self):
        # Coherence failures must be reported as such, not as anchor failures.
        sheet = self._sheet(blocks=[{"kind": "bullish", "low": 1998.0,
                                     "high": 2002.0}])
        plan, reason = smc_engine.validate_plan(
            {"direction": "LONG", "entry": 2000, "stop": 2005,
             "targets": [2010]}, facts_by_tf=sheet)
        self.assertIsNone(plan)
        self.assertIn("stop is not below", reason)


class BuildTests(unittest.TestCase):
    # A realistic fact sheet for the mocked snapshot: the reply below enters at
    # 2000.0, so the sheet must carry a computed zone the entry can anchor to —
    # otherwise the anchoring check (on by default) would drop every plan.
    _FACTS: ClassVar[dict] = {
        "timeframes": {
            "M15": {"close": 2000.0, "atr": 5.0,
                    "structure": {"bias": "bullish"},
                    "order_blocks": [{"kind": "bullish", "low": 1998.0,
                                      "high": 2002.0}],
                    "fair_value_gaps": []},
        },
        "missing_timeframes": [],
        "confluence": {"dominant": "bullish", "agreeing": 1, "total_frames": 1},
        "facts_note": "n",
    }

    def _patch(self, api_key="key", model_reply=None):
        return (
            mock.patch.object(ai_advisor, "provider_key", return_value=api_key),
            mock.patch.object(ai_advisor, "rate_limited", return_value=False),
            mock.patch.object(smc_engine, "build_snapshot",
                              return_value={"symbol": "XAUUSD", "price": 2000.0,
                                            "facts": dict(self._FACTS),
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


class MeasuredRecordTests(unittest.TestCase):
    """The engine's measured record is the project's finding, not the model's
    claim. These pin the honesty contract: it cites its source, it does not
    pretend to clear the gate, and it reaches both the model and the reply."""

    def test_the_record_cites_its_source_and_scope(self):
        rec = smc_engine.measured_record()
        self.assertIn("source", rec)
        self.assertIn("scope", rec)
        self.assertIn("n", rec)
        self.assertIn("win_pct", rec)
        self.assertIn("break_even_pct", rec)

    def test_the_record_does_not_falsely_claim_to_clear_the_gate(self):
        rec = smc_engine.measured_record()
        self.assertFalse(rec["clears_gate"])
        self.assertTrue(rec["why_not"])
        self.assertLess(rec["wilson_low_pct"], rec["break_even_pct"])

    def test_the_record_is_a_fresh_dict_each_call(self):
        first = smc_engine.measured_record()
        first["n"] = 0
        self.assertNotEqual(smc_engine.measured_record()["n"], 0)

    def test_the_record_reaches_the_model_prompt(self):
        view = smc_engine._prompt_view({"symbol": "XAUUSD", "price": 2000.0,
                                        "facts": {}, "news": [], "events": [],
                                        "measured_record": smc_engine.measured_record()})
        self.assertIsNotNone(view["engine_measured_record"])
        self.assertFalse(view["engine_measured_record"]["clears_gate"])

    def test_the_record_reaches_the_engine_block_of_the_reply(self):
        snapshot = {"price": 2000.0, "facts": {},
                    "measured_record": smc_engine.measured_record()}
        out = smc_engine.assemble(snapshot, {"plans": []})
        self.assertIsNotNone(out["engine"]["measured_record"])
        self.assertFalse(out["engine"]["measured_record"]["clears_gate"])

    def test_the_numbers_match_the_study_report(self):
        # Copied verbatim from research/reports/smc_zone_study.md; if the study
        # is re-run and the numbers move, both move in the same commit.
        rec = smc_engine.measured_record()
        self.assertEqual(rec["n"], 9845)
        self.assertEqual(rec["win_pct"], 23.2)
        self.assertEqual(rec["break_even_pct"], 27.9)


if __name__ == "__main__":
    unittest.main()
