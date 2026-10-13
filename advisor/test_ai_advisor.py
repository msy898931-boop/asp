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
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ai_advisor as advisor  # noqa: E402
import self_review  # noqa: E402


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
        self._orig_news = advisor.recent_news
        self._orig_events = advisor.upcoming_events
        advisor.recent_news = lambda: []
        advisor.upcoming_events = lambda: []

    def tearDown(self):
        advisor._get_json = self._orig
        advisor.recent_news = self._orig_news
        advisor.upcoming_events = self._orig_events

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
        self.assertFalse(snap["news_available"])
        self.assertEqual(snap["news"], [])

    def test_snapshot_carries_headlines_when_present(self):
        advisor._get_json = lambda url: {"payload": {"last": 4185.9}}
        advisor.recent_news = lambda: [
            {"title": "Gold steadies ahead of CPI", "published": "x",
             "source": "TestWire"}]
        snap = advisor.build_snapshot()
        self.assertTrue(snap["news_available"])
        self.assertEqual(snap["news"][0]["title"], "Gold steadies ahead of CPI")

    def test_snapshot_carries_events_when_present(self):
        advisor._get_json = lambda url: {"payload": {"last": 4185.9}}
        advisor.upcoming_events = lambda: [
            {"title": "CPI m/m", "title_ar": "مؤشر أسعار المستهلك (CPI)",
             "country": "USD", "impact": "high", "at_utc": 1, "in_hours": 15.0,
             "forecast": "0.6%", "previous": "0.4%"}]
        snap = advisor.build_snapshot()
        self.assertTrue(snap["events_available"])
        self.assertEqual(snap["events"][0]["title_ar"], "مؤشر أسعار المستهلك (CPI)")


class NewsTests(unittest.TestCase):
    def setUp(self):
        self._orig = advisor.fetch_news
        self._orig_cache = dict(advisor._news_cache)

    def tearDown(self):
        advisor.fetch_news = self._orig
        advisor._news_cache.update(self._orig_cache)

    def test_fetch_news_parses_rss_without_network(self):
        rss = (b"<rss><channel>"
               b"<item><title>Gold up on softer yields</title>"
               b"<pubDate>Mon, 13 Oct 2026 10:00:00 GMT</pubDate>"
               b"<source>Wire</source></item>"
               b"<item><title></title></item>"
               b"</channel></rss>")
        orig = urllib.request.urlopen

        class Fake:
            def __enter__(self_):
                return self_
            def __exit__(self_, *a):
                return False
            def read(self_):
                return rss

        urllib.request.urlopen = lambda req, timeout=None: Fake()
        try:
            items = advisor.fetch_news()
        finally:
            urllib.request.urlopen = orig
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Gold up on softer yields")
        self.assertEqual(items[0]["source"], "Wire")

    def test_fetch_news_is_inert_when_disabled(self):
        original = advisor.NEWS_ENABLED
        advisor.NEWS_ENABLED = False
        try:
            self.assertEqual(advisor.fetch_news(), [])
        finally:
            advisor.NEWS_ENABLED = original

    def test_recent_news_caches(self):
        calls = {"n": 0}

        def fake():
            calls["n"] += 1
            return [{"title": "t", "published": "", "source": ""}]

        advisor.fetch_news = fake
        advisor._news_cache.update({"at": 0.0, "items": []})
        advisor.recent_news()
        advisor.recent_news()
        self.assertEqual(calls["n"], 1)


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self._orig = advisor.fetch_calendar
        self._orig_cache = dict(advisor._calendar_cache)

    def tearDown(self):
        advisor.fetch_calendar = self._orig
        advisor._calendar_cache.update(self._orig_cache)

    def test_arabic_names_for_gold_movers(self):
        self.assertEqual(advisor.arabic_event_name("Core CPI m/m"),
                         "مؤشر أسعار المستهلك الأساسي (Core CPI)")
        self.assertEqual(advisor.arabic_event_name("Non-Farm Employment Change"),
                         "التغيّر في الوظائف غير الزراعية (NFP)")
        self.assertEqual(advisor.arabic_event_name("FOMC Statement"),
                         "قرار الفائدة الفيدرالي (FOMC)")
        self.assertEqual(advisor.arabic_event_name("Something Unmapped"),
                         "Something Unmapped")

    def test_stamp_is_utc(self):
        at = advisor._parse_calendar_stamp("10-14-2026", "12:30pm")
        self.assertIsNotNone(at)
        dt = datetime.fromtimestamp(at, timezone.utc)
        self.assertEqual((dt.hour, dt.minute, dt.day, dt.month), (12, 30, 14, 10))
        self.assertIsNone(advisor._parse_calendar_stamp("", "12:30pm"))
        self.assertIsNone(advisor._parse_calendar_stamp("bad", "nope"))

    def test_fetch_calendar_filters_high_impact_usd_only(self):
        import time as _t
        soon = _t.gmtime(_t.time() + 6 * 3600)
        stamp = "%02d-%02d-%04d" % (soon.tm_mon, soon.tm_mday, soon.tm_year)
        xml = ("<weeklyevents>"
               "<event><title>CPI m/m</title><country>USD</country>"
               "<date>%s</date><time>12:30pm</time><impact>High</impact>"
               "<forecast>0.6%%</forecast><previous>0.4%%</previous></event>"
               "<event><title>Low thing</title><country>USD</country>"
               "<date>%s</date><time>12:30pm</time><impact>Low</impact></event>"
               "<event><title>JPY thing</title><country>JPY</country>"
               "<date>%s</date><time>12:30pm</time><impact>High</impact></event>"
               "</weeklyevents>" % (stamp, stamp, stamp)).encode()
        orig = urllib.request.urlopen

        class Fake:
            def __enter__(self_):
                return self_
            def __exit__(self_, *a):
                return False
            def read(self_):
                return xml

        urllib.request.urlopen = lambda req, timeout=None: Fake()
        try:
            events = advisor.fetch_calendar()
        finally:
            urllib.request.urlopen = orig
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title_ar"], "مؤشر أسعار المستهلك (CPI)")
        self.assertEqual(events[0]["country"], "USD")

    def test_upcoming_events_backs_off_after_failure(self):
        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            return []

        advisor.fetch_calendar = boom
        advisor._calendar_cache.update({"at": 0.0, "items": [], "failed_at": 0.0})
        advisor.upcoming_events()
        advisor.upcoming_events()
        advisor.upcoming_events()
        self.assertEqual(calls["n"], 1)


class ReviewTests(unittest.TestCase):
    def test_score_due_waits_for_horizon_and_scores_once(self):
        now = 10_000_000.0
        advs = [
            {"generated_at_utc": int(now - 7200), "symbol": "XAUUSD",
             "stance": "LONG", "price": 4100.0},
            {"generated_at_utc": int(now - 60), "symbol": "XAUUSD",
             "stance": "LONG", "price": 4100.0},
        ]
        new = self_review.score_due(advs, [], price_now=4130.0,
                                    now=now, horizon_s=3600)
        self.assertEqual(len(new), 1)
        self.assertTrue(new[0]["correct"])
        # Already-scored calls are not scored again.
        again = self_review.score_due(advs, new, price_now=4130.0,
                                      now=now, horizon_s=3600)
        self.assertEqual(again, [])

    def test_stance_verdicts(self):
        up = {"price": 100.0}
        self.assertTrue(self_review.evaluate({**up, "stance": "LONG"}, 101.0)["correct"])
        self.assertFalse(self_review.evaluate({**up, "stance": "SHORT"}, 101.0)["correct"])
        self.assertTrue(self_review.evaluate({**up, "stance": "NEUTRAL"}, 100.05)["correct"])
        self.assertFalse(self_review.evaluate({**up, "stance": "NEUTRAL"}, 101.0)["correct"])
        self.assertIsNone(self_review.evaluate({"stance": "LONG"}, 101.0))

    def test_summarize_and_lessons(self):
        outcomes = [
            {"stance": "LONG", "correct": True, "price": 1, "price_after": 2,
             "move_pct": 1.0},
            {"stance": "SHORT", "correct": False, "price": 2, "price_after": 3,
             "move_pct": 1.0},
        ]
        summary = self_review.summarize(outcomes)
        self.assertEqual(summary["scored"], 2)
        self.assertEqual(summary["correct"], 1)
        self.assertEqual(summary["hit_rate"], 0.5)
        self.assertEqual(len(summary["recent_wrong"]), 1)
        block = self_review.lessons_block(summary)
        self.assertIn("SHORT", block)
        self.assertIn("50%", block)
        # No outcomes -> no block, so an empty history adds nothing.
        self.assertEqual(self_review.lessons_block(
            {"scored": 0, "correct": 0, "hit_rate": None, "recent_wrong": []}), "")

    def test_summarize_exposes_every_wrong_not_just_recent(self):
        outcomes = [
            {"stance": "LONG", "correct": False, "generated_at_utc": 1},
            {"stance": "SHORT", "correct": True, "generated_at_utc": 2},
            {"stance": "SHORT", "correct": False, "generated_at_utc": 3},
            {"stance": "NEUTRAL", "correct": False, "generated_at_utc": 4},
            {"stance": "LONG", "correct": False, "generated_at_utc": 5},
        ]
        summary = self_review.summarize(outcomes)
        self.assertEqual(len(summary["recent_wrong"]), 3)
        self.assertEqual(len(summary["wrong"]), 4)
        self.assertTrue(all(not o["correct"] for o in summary["wrong"]))

    def test_format_review_is_arabic_and_honest(self):
        empty = self_review.format_review(
            {"scored": 0, "correct": 0, "hit_rate": None, "recent_wrong": []})
        self.assertIn("ما في نتائج", empty)
        summary = {
            "scored": 4, "correct": 3, "hit_rate": 0.75,
            "by_stance": {"LONG": {"n": 2, "correct": 2},
                          "SHORT": {"n": 2, "correct": 1}},
            "recent_wrong": [{"stance": "SHORT", "price": 4100, "price_after": 4130,
                              "move_pct": 0.73}],
        }
        text = self_review.format_review(summary)
        self.assertIn("3 من 4 (75%)", text)
        self.assertIn("شراء", text)
        self.assertIn("بيع", text)
        self.assertIn("قلت بيع", text)
        self.assertIn("الدرس مُحقَن", text)

    def test_read_jsonl_skips_bad_lines(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl",
                                         delete=False) as fh:
            fh.write('{"a": 1}\n')
            fh.write("not json\n")
            fh.write('{"a": 2}\n')
            path = fh.name
        try:
            records = self_review.read_jsonl(path)
        finally:
            os.unlink(path)
        self.assertEqual([r["a"] for r in records], [1, 2])
        self.assertEqual(self_review.read_jsonl("/no/such/file"), [])


class BuildTests(unittest.TestCase):
    def setUp(self):
        self._orig_get = advisor._get_json
        self._orig_call = advisor.call_model
        self._orig_log = advisor.append_log
        self._orig_news = advisor.recent_news
        self._orig_events = advisor.upcoming_events
        self._orig_review = advisor.run_review
        self._orig_key = os.environ.get("GROQ_API_KEY")
        advisor.append_log = lambda record: None
        advisor.recent_news = lambda: []
        advisor.upcoming_events = lambda: []
        advisor.run_review = lambda price: {
            "scored": 0, "correct": 0, "hit_rate": None,
            "by_stance": {}, "recent_wrong": []}
        advisor._get_json = lambda url: (
            {"payload": {"last": 4185.9}} if "/v1/tick" in url
            else {"data": {"available": False, "reason": "none"}})

    def tearDown(self):
        advisor._get_json = self._orig_get
        advisor.call_model = self._orig_call
        advisor.append_log = self._orig_log
        advisor.recent_news = self._orig_news
        advisor.upcoming_events = self._orig_events
        advisor.run_review = self._orig_review
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
        self.assertIn("Arabic", prompt)

    def test_prompt_injects_lessons_when_present(self):
        plain = advisor.build_prompt({"symbol": "XAUUSD"})
        with_lessons = advisor.build_prompt(
            {"symbol": "XAUUSD"}, "مراجعة ذاتية: خطأ سابق")
        self.assertNotIn("مراجعة ذاتية", plain)
        self.assertIn("مراجعة ذاتية", with_lessons)


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
