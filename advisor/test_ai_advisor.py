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
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer

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


_MERGE_CARD = {"verdict": "LONG",
               "card": {"direction": "LONG", "entry": 2651.4, "stop": 2643.1,
                        "target": 2667.9,
                        "basis": "stop beyond the last M15 swing"}}


class MergeCardLevelsTests(unittest.TestCase):
    CARD = _MERGE_CARD

    def test_card_levels_fill_the_plan(self):
        op = {"plan": {"bias": "صعودي", "trigger": "اختراق", "note": "ملاحظة"}}
        out = advisor._merge_card_levels(op, self.CARD)
        self.assertEqual(out["plan"]["entry"], "2651.4")
        self.assertEqual(out["plan"]["stop"], "2643.1")
        self.assertEqual(out["plan"]["target"], "2667.9")

    def test_bias_and_note_fall_back_to_the_card(self):
        out = advisor._merge_card_levels({"plan": {"bias": "", "trigger": "t"}},
                                         self.CARD)
        self.assertEqual(out["plan"]["bias"], "شراء")
        self.assertIn("swing", out["plan"]["note"])

    def test_a_plan_is_created_when_the_model_omits_it(self):
        # The model is asked not to quote prices; if it drops the plan entirely
        # the levels must still come from the card.
        out = advisor._merge_card_levels({"plan": None}, self.CARD)
        self.assertEqual(out["plan"]["entry"], "2651.4")
        self.assertEqual(out["plan"]["bias"], "شراء")

    def test_no_card_leaves_the_plan_untouched(self):
        op = {"plan": {"bias": "حياد", "trigger": "t", "note": "n"}}
        out = advisor._merge_card_levels(op, {"card": None})
        self.assertNotIn("entry", out["plan"])
        self.assertEqual(out["plan"], {"bias": "حياد", "trigger": "t", "note": "n"})


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
        # The time must come from the same instant as the date. Hardcoding
        # "12:30pm" made this test fail for the six hours a day when now+6h
        # lands past it (the feed window is [-1h, +48h]).
        clock = "%d:%02d%s" % (
            (soon.tm_hour % 12) or 12, soon.tm_min, "am" if soon.tm_hour < 12 else "pm")
        xml = ("<weeklyevents>"
               "<event><title>CPI m/m</title><country>USD</country>"
               "<date>%s</date><time>%s</time><impact>High</impact>"
               "<forecast>0.6%%</forecast><previous>0.4%%</previous></event>"
               "<event><title>Low thing</title><country>USD</country>"
               "<date>%s</date><time>%s</time><impact>Low</impact></event>"
               "<event><title>JPY thing</title><country>JPY</country>"
               "<date>%s</date><time>%s</time><impact>High</impact></event>"
               "</weeklyevents>" % (stamp, clock, stamp, clock, stamp, clock)).encode()
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

    def test_run_review_still_summarizes_when_price_is_missing(self):
        # A brief feed outage must not blank a scoreboard that already has
        # scored outcomes; it only stops *new* scoring.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "outcomes.jsonl")
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"generated_at_utc": 1, "symbol": "XAUUSD",
                                     "stance": "LONG", "correct": False}) + "\n")
            old = advisor.OUTCOME_PATH
            advisor.OUTCOME_PATH = out
            try:
                summary = advisor.run_review(None)
            finally:
                advisor.OUTCOME_PATH = old
        self.assertEqual(summary["scored"], 1)
        self.assertEqual(len(summary["wrong"]), 1)

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
        self._orig_bars = advisor.fetch_bars_by_tf
        self._orig_key = os.environ.get("GROQ_API_KEY")
        self._orig_backoff = advisor._rate_limited_until
        advisor._rate_limited_until = 0.0
        advisor.append_log = lambda record: None
        advisor.recent_news = lambda: []
        advisor.upcoming_events = lambda: []
        advisor.fetch_bars_by_tf = lambda: {}
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
        advisor.fetch_bars_by_tf = self._orig_bars
        advisor._rate_limited_until = self._orig_backoff
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
        self.assertNotIn("rate_limited", result)

    def test_a_rate_limited_provider_pauses_the_opinion(self):
        # The 429 path: the model never runs, the note says why, and the flag
        # is set so the client can keep the card and snapshot on screen.
        os.environ["GROQ_API_KEY"] = "test-key"
        calls = {"n": 0}

        def refusing(prompt, key):
            calls["n"] += 1
            advisor._mark_rate_limited()
            return None

        advisor.call_model = refusing
        result = advisor.Advisor()._build()
        self.assertIsNone(result["opinion"])
        self.assertTrue(result["rate_limited"])
        self.assertIn("quota", result["note"])
        self.assertIsNotNone(result["trade_card"])
        self.assertEqual(calls["n"], 1)

    def test_rate_limited_skips_the_model_entirely(self):
        # Once the back-off is set, no further model call is even attempted.
        os.environ["GROQ_API_KEY"] = "test-key"
        calls = {"n": 0}
        advisor.call_model = lambda prompt, key: calls.__setitem__("n", calls["n"] + 1)
        advisor._mark_rate_limited()
        result = advisor.Advisor()._build()
        self.assertTrue(result["rate_limited"])
        self.assertIsNone(result["opinion"])
        self.assertEqual(calls["n"], 0)

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

    def test_prompt_keeps_the_card_levels_out(self):
        # The model must never be handed the exact entry/stop/target: it would
        # be tempted to restate them. Only the read (directions/verdict) goes in.
        snapshot = {"symbol": "XAUUSD", "mtf": {
            "available": True, "verdict": "LONG",
            "agreement": {"long": 4, "short": 1, "neutral": 0, "of": 5},
            "timeframes": {"M15": {"direction": "LONG", "rsi": 57.0}},
            "card": {"entry": 2651.4, "stop": 2643.1, "target": 2667.9, "rr": 2.0},
        }}
        prompt = advisor.build_prompt(snapshot)
        self.assertIn("LONG", prompt)
        self.assertNotIn("2651.4", prompt)
        self.assertNotIn("2643.1", prompt)

    def test_trade_card_is_exposed_but_unvalidated(self):
        advisor.fetch_bars_by_tf = lambda: {}
        result = advisor.Advisor()._build()
        self.assertIn("trade_card", result)
        self.assertFalse(result["trade_card"]["validated"])

    def test_trade_card_is_built_from_bars(self):
        n = 120
        rising = [{"open": 1800.0 + i * 0.5, "high": 1800.0 + i * 0.5 + 0.3,
                   "low": 1800.0 + i * 0.5 - 0.3, "close": 1800.0 + (i + 1) * 0.5}
                  for i in range(n)]
        advisor.fetch_bars_by_tf = lambda: dict.fromkeys(advisor.MTF_TIMEFRAMES, rising)
        result = advisor.Advisor()._build()
        card = result["trade_card"]
        self.assertTrue(card["available"])
        self.assertEqual(card["verdict"], "LONG")
        self.assertLess(card["card"]["stop"], card["card"]["entry"])
        self.assertLess(card["card"]["entry"], card["card"]["target"])


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

    def test_mistral_is_a_known_provider(self):
        self.assertEqual(advisor._KEY_ENV["mistral"], "MISTRAL_API_KEY")
        self.assertEqual(advisor.MISTRAL_MODEL, "mistral-small-latest")
        self.assertIn("api.mistral.ai", advisor.MISTRAL_URL)

    def test_mistral_key_reads_its_own_env_var(self):
        original = advisor.PROVIDER
        advisor.PROVIDER = "mistral"
        os.environ["MISTRAL_API_KEY"] = "  sk-mistral  "
        try:
            self.assertEqual(advisor.provider_key(), "sk-mistral")
            self.assertEqual(advisor.active_model(), "mistral-small-latest")
        finally:
            os.environ.pop("MISTRAL_API_KEY", None)
            advisor.PROVIDER = original

    def test_mistral_dispatch_hits_the_mistral_endpoint(self):
        captured = {}

        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                return json.dumps(
                    {"choices": [{"message": {"content": '{"stance":"SHORT"}'}}]}
                ).encode()

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["auth"] = req.get_header("Authorization")
            captured["body"] = json.loads(req.data.decode())
            return FakeResp()

        original_provider = advisor.PROVIDER
        original = advisor.urllib.request.urlopen
        advisor.PROVIDER = "mistral"
        advisor.urllib.request.urlopen = fake_urlopen
        try:
            out = advisor.call_model("hello", "sk-mistral")
        finally:
            advisor.urllib.request.urlopen = original
            advisor.PROVIDER = original_provider
        self.assertIn("api.mistral.ai", captured["url"])
        self.assertEqual(captured["auth"], "Bearer sk-mistral")
        self.assertEqual(captured["body"]["model"], "mistral-small-latest")
        self.assertEqual(captured["body"]["messages"][0]["content"], "hello")
        self.assertEqual(out, '{"stance":"SHORT"}')

    def test_mistral_429_sets_the_back_off(self):
        original_backoff = advisor._rate_limited_until
        advisor._rate_limited_until = 0.0

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 429, "Too Many Requests", {},
                __import__("io").BytesIO(b'{"error":"rate limit"}'))

        original = advisor.urllib.request.urlopen
        advisor.urllib.request.urlopen = fake_urlopen
        try:
            self.assertIsNone(advisor.call_mistral("hello", "sk-mistral"))
            self.assertTrue(advisor.rate_limited())
        finally:
            advisor.urllib.request.urlopen = original
            advisor._rate_limited_until = original_backoff

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

    def test_groq_429_sets_the_back_off(self):
        # A 429 is a spent quota, not a bad key: call_groq must record it so the
        # next build backs off instead of calling again.
        original_backoff = advisor._rate_limited_until
        advisor._rate_limited_until = 0.0

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 429, "Too Many Requests", {},
                __import__("io").BytesIO(b'{"error":{"message":"TPD"}}'))

        original = advisor.urllib.request.urlopen
        advisor.urllib.request.urlopen = fake_urlopen
        try:
            self.assertIsNone(advisor.call_groq("hello", "gsk_test"))
            self.assertTrue(advisor.rate_limited())
        finally:
            advisor.urllib.request.urlopen = original
            advisor._rate_limited_until = original_backoff

    def test_a_non_429_error_does_not_back_off(self):
        original_backoff = advisor._rate_limited_until
        advisor._rate_limited_until = 0.0

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 500, "Server Error", {},
                __import__("io").BytesIO(b"boom"))

        original = advisor.urllib.request.urlopen
        advisor.urllib.request.urlopen = fake_urlopen
        try:
            self.assertIsNone(advisor.call_groq("hello", "gsk_test"))
            self.assertFalse(advisor.rate_limited())
        finally:
            advisor.urllib.request.urlopen = original
            advisor._rate_limited_until = original_backoff

    def test_a_model_plan_limit_does_not_back_off_the_provider(self):
        # Mistral's free tier answers 429 with limit-req-minute: 0 for a model
        # it does not serve. That marks the model, not the provider, so the
        # back-off must stay clear and the next model must be tried.
        original_backoff = advisor._rate_limited_until
        advisor._rate_limited_until = 0.0

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 429, "Too Many Requests",
                {"x-ratelimit-limit-req-minute": "0"},
                __import__("io").BytesIO(b'{"error":"rate limit"}'))

        original = advisor.urllib.request.urlopen
        advisor.urllib.request.urlopen = fake_urlopen
        try:
            self.assertIsNone(advisor.call_mistral("hello", "sk-mistral"))
            self.assertFalse(advisor.rate_limited())
        finally:
            advisor.urllib.request.urlopen = original
            advisor._rate_limited_until = original_backoff

    def test_a_spent_quota_429_still_backs_off(self):
        # A 429 with no plan-limit header (or a non-zero one) is a spent quota.
        self.assertFalse(advisor._is_model_plan_limit({}))
        self.assertFalse(
            advisor._is_model_plan_limit({"x-ratelimit-limit-req-minute": "60"}))
        self.assertTrue(
            advisor._is_model_plan_limit({"x-ratelimit-limit-req-minute": "0"}))

    def test_a_rejected_key_is_recorded_not_hidden(self):
        # A 401 means the key is present but refused (expired or revoked). It
        # must be recorded with the provider's own words so the engine can say
        # "rejected" instead of the generic "no usable reply", and it must NOT
        # trip the quota back-off (a bad key is not a spent budget).
        original_auth = advisor._AUTH_FAILURE
        original_backoff = advisor._rate_limited_until
        advisor._AUTH_FAILURE = None
        advisor._rate_limited_until = 0.0

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 401, "Unauthorized", {},
                __import__("io").BytesIO(
                    b'{"detail":"Your API key expired on 2026-10-26."}'))

        original = advisor.urllib.request.urlopen
        advisor.urllib.request.urlopen = fake_urlopen
        try:
            self.assertIsNone(advisor.call_mistral("hello", "sk-mistral"))
            self.assertFalse(advisor.rate_limited())
            failure = advisor.auth_failure()
            self.assertIsNotNone(failure)
            self.assertEqual(failure["code"], 401)
            self.assertIn("expired", failure["message"])
        finally:
            advisor.urllib.request.urlopen = original
            advisor._AUTH_FAILURE = original_auth
            advisor._rate_limited_until = original_backoff

    def test_a_good_call_clears_the_auth_failure(self):
        # Once the provider answers, a stale rejection must not linger and keep
        # reporting the engine as broken.
        original_auth = advisor._AUTH_FAILURE
        advisor._AUTH_FAILURE = {"code": 401, "message": "expired"}

        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                return json.dumps(
                    {"choices": [{"message": {"content": '{"stance":"LONG"}'}}]}
                ).encode()

        original_provider = advisor.PROVIDER
        original = advisor.urllib.request.urlopen
        advisor.PROVIDER = "mistral"
        advisor.urllib.request.urlopen = lambda req, timeout=None: FakeResp()
        try:
            self.assertIsNotNone(advisor.call_model("hello", "sk-mistral"))
            self.assertIsNone(advisor.auth_failure())
        finally:
            advisor.urllib.request.urlopen = original
            advisor.PROVIDER = original_provider
            advisor._AUTH_FAILURE = original_auth

    def test_a_403_is_also_an_auth_failure(self):
        original_auth = advisor._AUTH_FAILURE
        advisor._AUTH_FAILURE = None

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 403, "Forbidden", {},
                __import__("io").BytesIO(b"forbidden"))

        original = advisor.urllib.request.urlopen
        advisor.urllib.request.urlopen = fake_urlopen
        try:
            self.assertIsNone(advisor.call_groq("hello", "gsk_test"))
            failure = advisor.auth_failure()
            self.assertIsNotNone(failure)
            self.assertEqual(failure["code"], 403)
        finally:
            advisor.urllib.request.urlopen = original
            advisor._AUTH_FAILURE = original_auth

    def test_the_mistral_chain_falls_through_a_refused_model(self):
        # The first model answers a plan-limit 429, the second answers for real:
        # call_model must move on and report the model that actually replied.
        original_provider = advisor.PROVIDER
        original = advisor.urllib.request.urlopen
        original_backoff = advisor._rate_limited_until
        original_last = advisor._LAST_MODEL
        advisor.PROVIDER = "mistral"
        advisor._rate_limited_until = 0.0
        tried = []

        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                return json.dumps(
                    {"choices": [{"message": {"content": '{"stance":"LONG"}'}}]}
                ).encode()

        def fake_urlopen(req, timeout=None):
            model = json.loads(req.data.decode())["model"]
            tried.append(model)
            if model == "mistral-small-latest":
                raise urllib.error.HTTPError(
                    req.full_url, 429, "Too Many Requests",
                    {"x-ratelimit-limit-req-minute": "0"},
                    __import__("io").BytesIO(b'{"error":"rate limit"}'))
            return FakeResp()

        advisor.urllib.request.urlopen = fake_urlopen
        try:
            out = advisor.call_model("hello", "sk-mistral")
            answered = advisor.answered_model()
        finally:
            advisor.urllib.request.urlopen = original
            advisor._rate_limited_until = original_backoff
            advisor.PROVIDER = original_provider
            advisor._LAST_MODEL = original_last
        self.assertEqual(out, '{"stance":"LONG"}')
        self.assertEqual(tried, ["mistral-small-latest", "open-mistral-nemo"])
        self.assertEqual(answered, "open-mistral-nemo")

    def test_the_chain_stops_on_a_spent_quota(self):
        # A real quota 429 on the first model must not walk the whole chain.
        original_provider = advisor.PROVIDER
        original = advisor.urllib.request.urlopen
        original_backoff = advisor._rate_limited_until
        advisor.PROVIDER = "mistral"
        advisor._rate_limited_until = 0.0
        tried = []

        def fake_urlopen(req, timeout=None):
            tried.append(json.loads(req.data.decode())["model"])
            raise urllib.error.HTTPError(
                req.full_url, 429, "Too Many Requests", {},
                __import__("io").BytesIO(b'{"error":{"message":"TPD"}}'))

        advisor.urllib.request.urlopen = fake_urlopen
        try:
            self.assertIsNone(advisor.call_model("hello", "sk-mistral"))
        finally:
            advisor.urllib.request.urlopen = original
            advisor._rate_limited_until = original_backoff
            advisor.PROVIDER = original_provider
        self.assertEqual(tried, ["mistral-small-latest"])

    def test_astra_advisor_models_overrides_the_chain(self):
        original = advisor.PROVIDER
        advisor.PROVIDER = "mistral"
        os.environ["ASTRA_ADVISOR_MODELS"] = " ministral-8b-latest , custom-a ,"
        try:
            self.assertEqual(advisor._model_chain(),
                             ("ministral-8b-latest", "custom-a"))
        finally:
            os.environ.pop("ASTRA_ADVISOR_MODELS", None)
            advisor.PROVIDER = original

    def test_the_default_chain_keeps_the_configured_model_first(self):
        original = advisor.PROVIDER
        advisor.PROVIDER = "mistral"
        try:
            chain = advisor._model_chain()
            self.assertEqual(chain[0], "mistral-small-latest")
            self.assertIn("open-mistral-nemo", chain)
        finally:
            advisor.PROVIDER = original


class CardTests(unittest.TestCase):
    """The model-free card: bars in, levels out, no quota spent."""

    def setUp(self):
        self._orig_bars = advisor.fetch_bars_by_tf
        self._orig_card_ttl = advisor.CARD_TTL

    def tearDown(self):
        advisor.fetch_bars_by_tf = self._orig_bars
        advisor.CARD_TTL = self._orig_card_ttl

    def test_card_is_built_without_a_model(self):
        called = {"model": 0}
        original_model = advisor.call_model
        advisor.call_model = lambda prompt, key: called.__setitem__(
            "model", called["model"] + 1)
        n = 120
        rising = [{"open": 1800.0 + i * 0.5, "high": 1800.0 + i * 0.5 + 0.3,
                   "low": 1800.0 + i * 0.5 - 0.3, "close": 1800.0 + (i + 1) * 0.5}
                  for i in range(n)]
        advisor.fetch_bars_by_tf = lambda: dict.fromkeys(advisor.MTF_TIMEFRAMES, rising)
        try:
            card = advisor.Advisor().card()
        finally:
            advisor.call_model = original_model
        self.assertTrue(card["available"])
        self.assertEqual(card["verdict"], "LONG")
        self.assertEqual(called["model"], 0)

    def test_card_is_cached_within_its_ttl(self):
        calls = {"n": 0}

        def bars():
            calls["n"] += 1
            return {}

        advisor.fetch_bars_by_tf = bars
        a = advisor.Advisor()
        a.card()
        a.card()
        self.assertEqual(calls["n"], 1)

    def test_health_reports_the_rate_limit(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), advisor.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        original_backoff = advisor._rate_limited_until
        try:
            host, port = server.server_address
            advisor._rate_limited_until = 0.0
            with urllib.request.urlopen(
                    "http://%s:%d/v1/health" % (host, port), timeout=5) as resp:
                self.assertFalse(json.loads(resp.read())["rate_limited"])
            advisor._mark_rate_limited()
            with urllib.request.urlopen(
                    "http://%s:%d/v1/health" % (host, port), timeout=5) as resp:
                self.assertTrue(json.loads(resp.read())["rate_limited"])
        finally:
            advisor._rate_limited_until = original_backoff
            server.shutdown()
            server.server_close()

    def test_health_surfaces_a_rejected_key(self):
        # `configured` is true whenever a key string is set, so an expired or
        # revoked key would read as healthy. The rejection must be visible on
        # the health body or the failure is silent — which is how an expired
        # key cost days.
        server = ThreadingHTTPServer(("127.0.0.1", 0), advisor.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        original_failure = advisor._AUTH_FAILURE
        original_provider = advisor.PROVIDER
        original_key = os.environ.get("MISTRAL_API_KEY")
        try:
            advisor.PROVIDER = "mistral"
            os.environ["MISTRAL_API_KEY"] = "sk-present-but-rejected"
            advisor._AUTH_FAILURE = None
            host, port = server.server_address
            with urllib.request.urlopen(
                    "http://%s:%d/v1/health" % (host, port), timeout=5) as resp:
                body = json.loads(resp.read())
            self.assertTrue(body["configured"])
            self.assertIsNone(body["auth_failure"])
            advisor._note_auth_failure(
                401, json.dumps({"detail": "Your API key expired on 2026-10-26."}))
            with urllib.request.urlopen(
                    "http://%s:%d/v1/health" % (host, port), timeout=5) as resp:
                body = json.loads(resp.read())
            self.assertTrue(body["configured"])
            self.assertEqual(body["auth_failure"]["code"], 401)
            self.assertIn("expired", body["auth_failure"]["message"])
        finally:
            advisor._AUTH_FAILURE = original_failure
            advisor.PROVIDER = original_provider
            if original_key is None:
                os.environ.pop("MISTRAL_API_KEY", None)
            else:
                os.environ["MISTRAL_API_KEY"] = original_key
            server.shutdown()
            server.server_close()

    def test_card_route_answers_without_a_model(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), advisor.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        original_bars = advisor.fetch_bars_by_tf
        try:
            advisor.fetch_bars_by_tf = lambda: {}
            host, port = server.server_address
            with urllib.request.urlopen(
                    "http://%s:%d/v1/card" % (host, port), timeout=5) as resp:
                body = json.loads(resp.read())
            self.assertIn("trade_card", body)
            self.assertFalse(body["trade_card"]["available"])
        finally:
            advisor.fetch_bars_by_tf = original_bars
            server.shutdown()
            server.server_close()


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

    def test_a_transport_failure_is_named_not_collapsed(self):
        # A 400 (bad model), a 402 (empty balance) and a 404 (wrong base URL)
        # are each actionable, and each used to reach the operator as the bare
        # "no usable reply". The reason must now survive to failure_reason().
        original = advisor.urllib.request.urlopen
        original_failure = advisor._LAST_HTTP_FAILURE
        for code, body in ((400, b'{"error":"model not found"}'),
                           (402, b'{"error":"no credits"}'),
                           (404, b'not found')):
            def fake_urlopen(req, timeout=None, _c=code, _b=body):
                raise urllib.error.HTTPError(
                    req.full_url, _c, "err", {},
                    __import__("io").BytesIO(_b))

            advisor._LAST_HTTP_FAILURE = None
            advisor.urllib.request.urlopen = fake_urlopen
            try:
                self.assertIsNone(advisor.call_mistral("hello", "sk-mistral"))
                self.assertEqual(advisor.last_http_failure()["code"], code)
                self.assertIn("HTTP %d" % code, advisor.failure_reason())
            finally:
                advisor.urllib.request.urlopen = original
        advisor._LAST_HTTP_FAILURE = original_failure

    def test_a_two_hundred_carrying_an_error_object_is_a_failure(self):
        # OpenRouter answers 200 with ``{"error": {...}}`` when an upstream
        # provider fails. That is a failure, and its message must reach the
        # operator rather than an empty completion being assumed.
        original = advisor.urllib.request.urlopen
        original_failure = advisor._LAST_HTTP_FAILURE
        advisor._LAST_HTTP_FAILURE = None

        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                return json.dumps(
                    {"error": {"message": "upstream provider failed"}}
                ).encode()

        advisor.urllib.request.urlopen = lambda req, timeout=None: FakeResp()
        try:
            self.assertIsNone(advisor.call_mistral("hello", "sk-mistral"))
            self.assertIn("upstream provider failed", advisor.failure_reason())
        finally:
            advisor.urllib.request.urlopen = original
            advisor._LAST_HTTP_FAILURE = original_failure

    def test_an_empty_completion_is_named(self):
        original = advisor.urllib.request.urlopen
        original_failure = advisor._LAST_HTTP_FAILURE
        advisor._LAST_HTTP_FAILURE = None

        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                return json.dumps(
                    {"choices": [{"message": {"content": "   "}}]}).encode()

        advisor.urllib.request.urlopen = lambda req, timeout=None: FakeResp()
        try:
            self.assertIsNone(advisor.call_mistral("hello", "sk-mistral"))
            self.assertIn("empty", advisor.failure_reason())
        finally:
            advisor.urllib.request.urlopen = original
            advisor._LAST_HTTP_FAILURE = original_failure

    def test_a_good_call_clears_the_previous_failure(self):
        # A stale reason must not keep colouring the next, successful call.
        original_provider = advisor.PROVIDER
        original = advisor.urllib.request.urlopen
        original_failure = advisor._LAST_HTTP_FAILURE
        advisor._LAST_HTTP_FAILURE = {"code": 400, "message": "stale"}

        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                return json.dumps(
                    {"choices": [{"message": {"content": '{"bias":"LONG"}'}}]}
                ).encode()

        advisor.PROVIDER = "mistral"
        advisor.urllib.request.urlopen = lambda req, timeout=None: FakeResp()
        try:
            self.assertIsNotNone(advisor.call_model("hello", "sk-mistral"))
            self.assertEqual(advisor.failure_reason(), "")
        finally:
            advisor.urllib.request.urlopen = original
            advisor.PROVIDER = original_provider
            advisor._LAST_HTTP_FAILURE = original_failure

    def test_a_key_shaped_string_is_redacted_from_the_reason(self):
        # The reason is echoed to the operator and into logs; a provider may
        # quote the credential back, so anything key-shaped is removed first.
        self.assertNotIn("sk-or-v1-abcdef1234567890",
                         advisor._redact("Invalid: Bearer sk-or-v1-abcdef1234567890"))
        self.assertIn("<redacted>", advisor._redact("key sk-1234567890abcdefgh"))
        self.assertEqual(advisor._redact(""), "")

    def test_no_reason_means_no_dangling_bracket(self):
        # failure_reason() is empty when nothing is known, so callers can append
        # it conditionally and never print "no usable reply ()".
        original_failure = advisor._LAST_HTTP_FAILURE
        advisor._LAST_HTTP_FAILURE = None
        try:
            self.assertEqual(advisor.failure_reason(), "")
        finally:
            advisor._LAST_HTTP_FAILURE = original_failure

    def test_a_nested_gateway_error_is_reduced_to_its_cause(self):
        # OpenRouter and Cloudflare AI Gateway nest the upstream fault two or
        # three levels deep and repeat "Provider returned error" at every
        # level. That outer line names nothing actionable; the innermost one
        # names the function, account or model that was refused. This exact
        # body was reported from production and used to reach the operator as
        # "no usable reply" with the cause truncated away.
        inner = json.dumps({
            "status": 404, "title": "Not Found",
            "detail": "Function 'ac74040f-9fc9-4c5e-ac74-279ba5161d69': "
                      "Not found for account 7f3a1c",
        })
        body = json.dumps({"error": {
            "message": "Provider returned error", "code": 404,
            "metadata": {"raw": inner}}})

        reason = advisor._unwrap_provider_error(body)
        self.assertIn("Not found for account 7f3a1c", reason)
        self.assertNotIn("Provider returned error", reason)
        self.assertNotIn("{", reason)

    def test_unwrapping_never_mangles_a_plain_body(self):
        # A provider that answers in plain text must read back unchanged, and a
        # string that merely mentions JSON must not be parsed as it.
        self.assertEqual(advisor._unwrap_provider_error("rate limit hit"),
                         "rate limit hit")
        self.assertEqual(advisor._unwrap_provider_error(""), "")
        self.assertEqual(advisor._unwrap_provider_error("see {json} docs"),
                         "see {json} docs")

    def test_unwrapping_falls_back_when_every_line_is_generic(self):
        # Nothing specific to show: better the wrapper than an empty string.
        body = json.dumps({"error": {"message": "Provider returned error"}})
        self.assertEqual(advisor._unwrap_provider_error(body),
                         "Provider returned error")

    def test_a_deeply_nested_cause_survives_the_read_limit(self):
        # The body is read with a limit generous enough that the nested cause
        # is not cut off before it can be parsed.
        self.assertGreaterEqual(advisor._ERROR_BODY_LIMIT, 2048)
        self.assertGreater(advisor._ERROR_REASON_LIMIT, 200)

    def test_the_auth_path_also_unwraps(self):
        # A 401 arriving through a gateway carries the same nesting; the
        # "rejected" sentence must name the real cause too.
        inner = json.dumps({"detail": "API key revoked by the account owner"})
        body = json.dumps({"error": {
            "message": "Provider returned error", "code": 401,
            "metadata": {"raw": inner}}})
        original = advisor._AUTH_FAILURE
        try:
            advisor._note_auth_failure(401, body)
            self.assertIn("revoked", advisor.auth_failure()["message"])
        finally:
            advisor._AUTH_FAILURE = original


if __name__ == "__main__":
    unittest.main()
