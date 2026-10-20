"""Tests for the isolated AI analysis engine (advisor/ai_engine.py).

Every test stubs the provider and the upstream fetches: the engine's own logic
(prompt assembly, trade validation, anchoring, honest stamps, isolation) is what
is under test, never a live model or a live bridge.
"""

from __future__ import annotations

import json
import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ai_advisor  # noqa: E402
import ai_engine  # noqa: E402


def bars(count: int = 120, start: float = 2000.0, slope: float = 0.3) -> list:
    """A deterministic trending series the reads can consume."""
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
        "news_used": False,
        "key_event": "",
        "trades": [{
            "direction": "LONG", "entry": 2000.0, "stop": 1995.0,
            "targets": [2006.0, 2012.0, 2020.0], "win_rate": 0.62,
            "rationale": "سبب", "timeframe": "M15",
        }],
    }
    body.update(over)
    return json.dumps(body)


class ValidateTradeTests(unittest.TestCase):
    def test_a_coherent_long_is_kept(self):
        trade, reason = ai_engine.validate_trade(
            {"direction": "LONG", "entry": 2000, "stop": 1995,
             "targets": [2006, 2012, 2020], "win_rate": 0.6})
        self.assertEqual(reason, "ok")
        self.assertEqual(trade["targets"], [2006.0, 2012.0, 2020.0])
        self.assertEqual(trade["target"], 2020.0)  # singular = last rung
        self.assertAlmostEqual(trade["reward_risk"], 4.0)
        self.assertFalse(trade["win_rate_validated"])

    def test_a_coherent_short_is_kept(self):
        trade, reason = ai_engine.validate_trade(
            {"direction": "SHORT", "entry": 2000, "stop": 2005,
             "targets": [1990, 1980]})
        self.assertEqual(reason, "ok")
        self.assertEqual(trade["targets"], [1990.0, 1980.0])

    def test_a_long_stop_above_entry_is_dropped(self):
        trade, reason = ai_engine.validate_trade(
            {"direction": "LONG", "entry": 2000, "stop": 2005,
             "targets": [2010]})
        self.assertIsNone(trade)
        self.assertIn("below the entry", reason)

    def test_unordered_targets_are_dropped(self):
        trade, reason = ai_engine.validate_trade(
            {"direction": "LONG", "entry": 2000, "stop": 1995,
             "targets": [2012, 2006]})
        self.assertIsNone(trade)
        self.assertIn("ascending", reason)

    def test_a_first_target_behind_the_entry_is_dropped(self):
        trade, reason = ai_engine.validate_trade(
            {"direction": "SHORT", "entry": 2000, "stop": 2005,
             "targets": [2010]})
        self.assertIsNone(trade)
        self.assertIn("not below the entry", reason)

    def test_a_scalar_target_is_accepted(self):
        trade, reason = ai_engine.validate_trade(
            {"direction": "LONG", "entry": 2000, "stop": 1995,
             "targets": 2010})
        self.assertEqual(reason, "ok")
        self.assertEqual(trade["targets"], [2010.0])

    def test_nonnumeric_levels_are_dropped(self):
        for bad in ("2000", None, True, float("nan"), float("inf")):
            with self.subTest(value=bad):
                trade, _ = ai_engine.validate_trade(
                    {"direction": "LONG", "entry": bad, "stop": 1995,
                     "targets": [2010]})
                self.assertIsNone(trade)

    def test_win_rate_is_clamped_and_may_be_absent(self):
        trade, _ = ai_engine.validate_trade(
            {"direction": "LONG", "entry": 2000, "stop": 1995,
             "targets": [2010], "win_rate": 3})
        self.assertEqual(trade["win_rate"], 1.0)
        trade, _ = ai_engine.validate_trade(
            {"direction": "LONG", "entry": 2000, "stop": 1995,
             "targets": [2010]})
        self.assertIsNone(trade["win_rate"])


class ParseEngineTests(unittest.TestCase):
    def test_plain_json(self):
        parsed = ai_engine.parse_engine(reply())
        self.assertEqual(parsed["bias"], "LONG")
        self.assertEqual(len(parsed["trades"]), 1)
        self.assertEqual(parsed["rejected"], [])

    def test_fenced_and_prose_wrapped_json(self):
        self.assertIsNotNone(ai_engine.parse_engine("```json\n" + reply() + "\n```"))
        self.assertIsNotNone(ai_engine.parse_engine("here: " + reply() + " done"))

    def test_garbage_yields_none(self):
        self.assertIsNone(ai_engine.parse_engine("not json"))
        self.assertIsNone(ai_engine.parse_engine(""))
        self.assertIsNone(ai_engine.parse_engine("[1,2,3]"))

    def test_an_unknown_bias_falls_back_to_neutral(self):
        self.assertEqual(ai_engine.parse_engine(reply(bias="sideways"))["bias"],
                         "NEUTRAL")

    def test_a_bad_trade_is_reported_not_repaired(self):
        parsed = ai_engine.parse_engine(reply(trades=[
            {"direction": "LONG", "entry": 2000, "stop": 2005,
             "targets": [2010]},
        ]))
        self.assertEqual(parsed["trades"], [])
        self.assertEqual(len(parsed["rejected"]), 1)
        self.assertIn("below the entry", parsed["rejected"][0]["reason"])

    def test_the_trade_limit_is_enforced(self):
        one = {"direction": "LONG", "entry": 2000, "stop": 1995,
               "targets": [2010]}
        parsed = ai_engine.parse_engine(reply(trades=[one] * 8))
        self.assertEqual(len(parsed["trades"]), ai_engine.MAX_TRADES)
        self.assertTrue(any("limit" in r["reason"] for r in parsed["rejected"]))

    def test_an_empty_trade_list_is_allowed(self):
        parsed = ai_engine.parse_engine(reply(trades=[], bias="NEUTRAL"))
        self.assertEqual(parsed["trades"], [])
        self.assertEqual(parsed["bias"], "NEUTRAL")


class AssembleTests(unittest.TestCase):
    def _snapshot(self, price=2000.0, card_entry=2000.0, atr=2.0):
        return {
            "symbol": "XAUUSD",
            "price": price,
            "timeframes": {"M15": {"atr": atr}},
            "missing_timeframes": ["W1"],
            "rule_card": {"entry": card_entry} if card_entry is not None else None,
        }

    def test_a_near_trade_is_anchored(self):
        parsed = ai_engine.parse_engine(reply())
        out = ai_engine.assemble(self._snapshot(), parsed)
        self.assertTrue(out["trades"][0]["anchor"]["anchored"])

    def test_a_far_trade_is_flagged_not_dropped(self):
        parsed = ai_engine.parse_engine(reply(trades=[{
            "direction": "LONG", "entry": 2100.0, "stop": 2095.0,
            "targets": [2110.0]}]))
        out = ai_engine.assemble(self._snapshot(card_entry=2000.0, atr=2.0), parsed)
        self.assertEqual(len(out["trades"]), 1)
        self.assertFalse(out["trades"][0]["anchor"]["anchored"])
        self.assertIn("ATR", out["trades"][0]["anchor"]["note"])

    def test_distance_from_price_is_recorded(self):
        parsed = ai_engine.parse_engine(reply())
        out = ai_engine.assemble(self._snapshot(price=1990.0), parsed)
        self.assertAlmostEqual(out["trades"][0]["distance_from_price"], 10.0)

    def test_the_engine_block_names_the_isolation(self):
        parsed = ai_engine.parse_engine(reply())
        out = ai_engine.assemble(self._snapshot(), parsed)
        self.assertIn("isolated", out["engine"]["note"])
        self.assertIn("no orders", out["engine"]["note"])


class BuildTests(unittest.TestCase):
    def setUp(self):
        self._saved = {
            "call_model": ai_advisor.call_model,
            "get_json": ai_advisor._get_json,
            "recent_news": ai_advisor.recent_news,
            "upcoming_events": ai_advisor.upcoming_events,
            "append_log": ai_engine.append_log,
            "backoff": ai_advisor._rate_limited_until,
        }
        self._key = os.environ.get("GROQ_API_KEY")
        ai_advisor._rate_limited_until = 0.0
        ai_engine.append_log = lambda record: None
        ai_advisor.recent_news = lambda: []
        ai_advisor.upcoming_events = lambda: []
        ai_engine.fetch_all_bars = lambda: {"M15": bars(), "H1": bars(slope=0.5)}
        ai_advisor._get_json = lambda url: (
            {"payload": {"last": 2001.5}} if "/v1/tick" in url else None)

    def tearDown(self):
        ai_advisor.call_model = self._saved["call_model"]
        ai_advisor._get_json = self._saved["get_json"]
        ai_advisor.recent_news = self._saved["recent_news"]
        ai_advisor.upcoming_events = self._saved["upcoming_events"]
        ai_engine.append_log = self._saved["append_log"]
        ai_advisor._rate_limited_until = self._saved["backoff"]
        if self._key is None:
            os.environ.pop("GROQ_API_KEY", None)
        else:
            os.environ["GROQ_API_KEY"] = self._key

    def test_unconfigured_reports_the_snapshot_without_trades(self):
        os.environ.pop("GROQ_API_KEY", None)
        result = ai_engine.Engine()._build()
        self.assertFalse(result["configured"])
        self.assertIsNone(result["analysis"])
        self.assertFalse(result["validated"])
        self.assertEqual(result["snapshot"]["price"], 2001.5)

    def test_configured_returns_trades_and_an_honest_stamp(self):
        os.environ["GROQ_API_KEY"] = "test-key"
        ai_advisor.call_model = lambda prompt, key: reply()
        result = ai_engine.Engine()._build()
        self.assertTrue(result["configured"])
        self.assertEqual(len(result["analysis"]["trades"]), 1)
        self.assertFalse(result["validated"])
        self.assertIn("estimates", result["note"])

    def test_the_prompt_carries_every_frame_and_the_news(self):
        captured = {}
        os.environ["GROQ_API_KEY"] = "test-key"
        ai_advisor.recent_news = lambda: [{"title": "Gold rises"}]

        def grab(prompt, key):
            captured["prompt"] = prompt
            return reply()

        ai_advisor.call_model = grab
        ai_engine.Engine()._build()
        self.assertIn("M15", captured["prompt"])
        self.assertIn("H1", captured["prompt"])
        self.assertIn("Gold rises", captured["prompt"])

    def test_an_unusable_reply_produces_no_trades(self):
        os.environ["GROQ_API_KEY"] = "test-key"
        ai_advisor.call_model = lambda prompt, key: "not json"
        result = ai_engine.Engine()._build()
        self.assertIsNone(result["analysis"])
        self.assertIn("unavailable", result["note"])

    def test_a_rate_limited_provider_pauses_the_engine(self):
        os.environ["GROQ_API_KEY"] = "test-key"

        def refusing(prompt, key):
            ai_advisor._mark_rate_limited()
            return None

        ai_advisor.call_model = refusing
        result = ai_engine.Engine()._build()
        self.assertTrue(result.get("rate_limited"))
        self.assertIsNone(result["analysis"])

    def test_no_key_means_no_model_call(self):
        os.environ.pop("GROQ_API_KEY", None)
        called = {"n": 0}

        def spy(prompt, key):
            called["n"] += 1
            return reply()

        ai_advisor.call_model = spy
        ai_engine.Engine()._build()
        self.assertEqual(called["n"], 0)


class IsolationTests(unittest.TestCase):
    def test_the_engine_imports_no_engine_or_ledger_module(self):
        # The whole point is that it is a separate process that only reads. It
        # must not pull in the prediction ledger, the C++ engine binding, or any
        # order-execution path.
        with open(os.path.join(HERE, "ai_engine.py"), encoding="utf-8") as fh:
            source = fh.read()
        for forbidden in ("import ledger", "PredictionLedger", "place_order",
                          "execute_order", "import aura", "import engine"):
            self.assertNotIn(forbidden, source,
                             "the isolated engine must not use %r" % forbidden)

    def test_the_only_outbound_hosts_are_loopback_and_the_provider(self):
        # Reads go to loopback only; the single outbound call is the provider,
        # which lives in ai_advisor. This module itself opens no other socket.
        with open(os.path.join(HERE, "ai_engine.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertNotIn("http://localhost", source)
        self.assertNotIn("https://", source)  # provider URLs stay in ai_advisor
        self.assertIn("127.0.0.1", ai_advisor.BRIDGE_BASE)
        self.assertIn("127.0.0.1", ai_advisor.API_BASE)

    def test_the_engine_writes_only_its_own_log(self):
        # The only file the engine opens is its own trade log. Anything else
        # would be a write into the pipeline it is meant to stay out of.
        with open(os.path.join(HERE, "ai_engine.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn('open(LOG_PATH, "a"', source)
        self.assertEqual(source.count("open("), 1)


class PromptTests(unittest.TestCase):
    def test_the_system_prompt_states_the_coherence_rule(self):
        for piece in ("stop < entry", "LONG", "SHORT", "win_rate",
                      "not a measured number"):
            with self.subTest(piece=piece):
                self.assertIn(piece, ai_engine._SYSTEM_PROMPT)

    def test_the_prompt_view_keeps_every_frame(self):
        snapshot = {
            "symbol": "XAUUSD", "price": 2000.0,
            "timeframes": {"M1": {"close": 2000.0}, "MN1": {"close": 1900.0}},
            "missing_timeframes": [],
            "rule_card": None, "news": [], "events": [],
        }
        view = ai_engine._prompt_view(snapshot)
        self.assertEqual(set(view["timeframes"]), {"M1", "MN1"})


if __name__ == "__main__":
    unittest.main()
