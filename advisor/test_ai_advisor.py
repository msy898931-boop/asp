"""Tests for the isolated AI advisor.

Pure logic (snapshot assembly, prompt, reply parsing) is tested without any
network or key. The HTTP surface is exercised through the real handler class
with a stubbed fetch, so the routing and the "not configured" degradation are
covered without reaching Gemini.

Run: python3 -m unittest discover -s advisor -p "test_*.py"
"""

from __future__ import annotations

import json
import os
import sys
import unittest
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ai_advisor as advisor  # noqa: E402


class ParseAdvisoryTests(unittest.TestCase):
    def test_plain_json(self):
        got = advisor.parse_advisory(
            '{"stance":"LONG","confidence":0.7,"thesis":"t","risks":"r",'
            '"news_used":true}')
        self.assertEqual(got["stance"], "LONG")
        self.assertAlmostEqual(got["confidence"], 0.7)
        self.assertTrue(got["news_used"])

    def test_fenced_json(self):
        got = advisor.parse_advisory(
            '```json\n{"stance":"short","confidence":0.2,"thesis":"t",'
            '"risks":"r","news_used":false}\n```')
        self.assertEqual(got["stance"], "SHORT")

    def test_prose_around_json(self):
        got = advisor.parse_advisory(
            'Here you go: {"stance":"NEUTRAL","confidence":0.5,'
            '"thesis":"t","risks":"r","news_used":false} hope it helps')
        self.assertEqual(got["stance"], "NEUTRAL")

    def test_unknown_stance_falls_back_to_neutral(self):
        got = advisor.parse_advisory('{"stance":"MOON","confidence":1}')
        self.assertEqual(got["stance"], "NEUTRAL")

    def test_confidence_is_clamped(self):
        self.assertEqual(
            advisor.parse_advisory('{"stance":"LONG","confidence":9}')["confidence"],
            1.0)
        self.assertEqual(
            advisor.parse_advisory('{"stance":"LONG","confidence":-3}')["confidence"],
            0.0)

    def test_garbage_yields_none(self):
        self.assertIsNone(advisor.parse_advisory("no json here"))
        self.assertIsNone(advisor.parse_advisory(""))
        self.assertIsNone(advisor.parse_advisory(None))

    def test_missing_fields_are_filled(self):
        got = advisor.parse_advisory('{"stance":"LONG"}')
        self.assertEqual(got["thesis"], "")
        self.assertEqual(got["risks"], "")
        self.assertFalse(got["news_used"])
        self.assertEqual(got["confidence"], 0.0)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self._orig = advisor._get_json

    def tearDown(self):
        advisor._get_json = self._orig

    def test_snapshot_collects_tick_and_analysis(self):
        def fake(url):
            if "/v1/tick" in url:
                return {"payload": {"last": 4185.9, "bid": 4185.4, "ask": 4186.4}}
            return {"data": {"context": {"regime": "RANGE"},
                             "signal": {"direction": "NONE"},
                             "levels": {"entry": None}}}
        advisor._get_json = fake
        snap = advisor.build_snapshot()
        self.assertEqual(snap["price"], 4185.9)
        self.assertEqual(snap["bid"], 4185.4)
        self.assertTrue(snap["analysis_available"])
        self.assertEqual(snap["analysis"]["context"]["regime"], "RANGE")

    def test_snapshot_records_the_no_decision_state(self):
        def fake(url):
            if "/v1/tick" in url:
                return {"payload": {"last": 4185.9}}
            return {"data": {"available": False, "reason": "no decisions recorded yet"}}
        advisor._get_json = fake
        snap = advisor.build_snapshot()
        self.assertFalse(snap["analysis_available"])
        self.assertEqual(snap["analysis_note"], "no decisions recorded yet")

    def test_snapshot_survives_unreachable_upstreams(self):
        advisor._get_json = lambda url: None
        snap = advisor.build_snapshot()
        self.assertIsNone(snap["price"])
        self.assertFalse(snap["analysis_available"])


class BuildTests(unittest.TestCase):
    def setUp(self):
        self._orig_get = advisor._get_json
        self._orig_call = advisor.call_model
        self._orig_log = advisor.append_log
        self._orig_key = os.environ.get("GROQ_API_KEY")
        advisor.append_log = lambda record: None
        advisor._get_json = lambda url: (
            {"payload": {"last": 4185.9}} if "/v1/tick" in url
            else {"data": {"available": False, "reason": "none"}})

    def tearDown(self):
        advisor._get_json = self._orig_get
        advisor.call_model = self._orig_call
        advisor.append_log = self._orig_log
        if self._orig_key is None:
            os.environ.pop("GROQ_API_KEY", None)
        else:
            os.environ["GROQ_API_KEY"] = self._orig_key

    def test_unconfigured_reports_the_snapshot_without_an_opinion(self):
        os.environ.pop("GROQ_API_KEY", None)
        result = advisor.Advisor()._build()
        self.assertFalse(result["configured"])
        self.assertIsNone(result["opinion"])
        self.assertFalse(result["validated"])
        self.assertEqual(result["snapshot"]["price"], 4185.9)

    def test_configured_builds_an_unvalidated_opinion(self):
        os.environ["GROQ_API_KEY"] = "test-key"
        advisor.call_model = lambda prompt, key: json.dumps(
            {"stance": "SHORT", "confidence": 0.6, "thesis": "t",
             "risks": "r", "news_used": False})
        result = advisor.Advisor()._build()
        self.assertTrue(result["configured"])
        self.assertEqual(result["opinion"]["stance"], "SHORT")
        self.assertFalse(result["validated"])
        self.assertIn("outside the validated system", result["note"])

    def test_unusable_reply_does_not_produce_an_opinion(self):
        os.environ["GROQ_API_KEY"] = "test-key"
        advisor.call_model = lambda prompt, key: "not json at all"
        result = advisor.Advisor()._build()
        self.assertIsNone(result["opinion"])
        self.assertIn("unavailable", result["note"])

    def test_prompt_carries_the_snapshot_and_the_rules(self):
        prompt = advisor.build_prompt({"symbol": "XAUUSD", "price": 4185.9})
        self.assertIn("4185.9", prompt)
        self.assertIn("JSON only", prompt)
        self.assertIn("news_used", prompt)


class ProviderTests(unittest.TestCase):
    def test_default_provider_is_groq(self):
        self.assertEqual(advisor.PROVIDER, "groq")
        self.assertEqual(advisor._KEY_ENV["groq"], "GROQ_API_KEY")

    def test_provider_key_reads_the_right_env_var(self):
        os.environ["GROQ_API_KEY"] = "  gsk_abc  "
        try:
            self.assertEqual(advisor.provider_key(), "gsk_abc")
        finally:
            os.environ.pop("GROQ_API_KEY", None)

    def test_unknown_provider_is_inert(self):
        original = advisor.PROVIDER
        advisor.PROVIDER = "nope"
        try:
            self.assertIsNone(advisor.call_model("p", "k"))
        finally:
            advisor.PROVIDER = original

    def test_groq_request_shape_is_openai_compatible(self):
        captured = {}

        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                return json.dumps(
                    {"choices": [{"message": {"content": '{"stance":"LONG"}'}}]}
                ).encode()

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["auth"] = req.get_header("Authorization")
            captured["body"] = json.loads(req.data.decode())
            return FakeResp()

        original = advisor.urllib.request.urlopen
        advisor.urllib.request.urlopen = fake_urlopen
        try:
            out = advisor.call_groq("hello", "gsk_test")
        finally:
            advisor.urllib.request.urlopen = original
        self.assertIn("api.groq.com", captured["url"])
        self.assertEqual(captured["auth"], "Bearer gsk_test")
        self.assertEqual(captured["body"]["messages"][0]["content"], "hello")
        self.assertEqual(captured["body"]["temperature"], 0.0)
        self.assertEqual(out, '{"stance":"LONG"}')


class CacheTests(unittest.TestCase):
    def test_repeated_calls_are_served_from_cache(self):
        calls = {"n": 0}

        class Counting(advisor.Advisor):
            def _build(self):
                calls["n"] += 1
                return {"built": calls["n"]}

        a = Counting()
        first = a.current()
        second = a.current()
        self.assertEqual(first, second)
        self.assertEqual(calls["n"], 1)

    def test_force_bypasses_the_cache(self):
        calls = {"n": 0}

        class Counting(advisor.Advisor):
            def _build(self):
                calls["n"] += 1
                return {"built": calls["n"]}

        a = Counting()
        a.current()
        a.current(force=True)
        self.assertEqual(calls["n"], 2)


if __name__ == "__main__":
    unittest.main()
