"""Tests for the released-event calendar read.

Pure logic over synthetic events: no network, no clock. The point is to pin the
two things that make the read honest — it always names what it compares, and it
only asserts a gold direction where the link is actually direct.

Run: python3 -m unittest discover -s advisor -p "test_*.py"
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import calendar_feed as cf  # noqa: E402


def ev(title="CPI m/m", country="US", importance=1, actual=None,
       forecast=None, previous=None, at=1000):
    return {"title": title, "title_ar": cf.arabic_event_name(title),
            "country": country,
            "importance": importance, "at_utc": at, "actual": actual,
            "forecast": forecast, "previous": previous}


class ClassifyTests(unittest.TestCase):
    def test_unreleased_has_no_read(self):
        self.assertIsNone(cf.classify(ev(actual=None, forecast=1.0)))

    def test_above_forecast_is_stronger_currency_and_pressures_gold(self):
        read = cf.classify(ev(title="Nonfarm Payrolls", actual=200, forecast=150))
        self.assertEqual(read["surprise"], "above")
        self.assertEqual(read["vs"], "forecast")
        self.assertEqual(read["currency_dir"], 1)
        self.assertEqual(read["gold_dir"], -1)
        self.assertTrue(read["certain"])

    def test_below_forecast_is_weaker_currency_and_supports_gold(self):
        read = cf.classify(ev(title="Nonfarm Payrolls", actual=100, forecast=150))
        self.assertEqual(read["surprise"], "below")
        self.assertEqual(read["currency_dir"], -1)
        self.assertEqual(read["gold_dir"], 1)

    def test_inline_is_no_surprise(self):
        read = cf.classify(ev(title="CPI m/m", actual=0.3, forecast=0.3))
        self.assertEqual(read["surprise"], "inline")
        self.assertEqual(read["currency_dir"], 0)
        self.assertEqual(read["gold_dir"], 0)

    def test_jobless_claims_invert(self):
        # More claims than forecast = weaker currency, even though the number
        # is higher. This is the inversion the naive read gets backwards.
        read = cf.classify(ev(title="Initial Jobless Claims", actual=250, forecast=200))
        self.assertEqual(read["surprise"], "above")
        self.assertEqual(read["currency_dir"], -1)
        self.assertEqual(read["gold_dir"], 1)

    def test_unemployment_rate_inverts(self):
        read = cf.classify(ev(title="Unemployment Rate", actual=4.5, forecast=4.0))
        self.assertEqual(read["currency_dir"], -1)

    def test_falls_back_to_previous_and_says_so(self):
        read = cf.classify(ev(title="ADP Employment Change Weekly",
                              actual=24.75, forecast=None, previous=23.75))
        self.assertEqual(read["vs"], "previous")
        self.assertEqual(read["surprise"], "above")
        self.assertEqual(read["currency_dir"], 1)

    def test_no_base_means_no_read_but_still_released(self):
        read = cf.classify(ev(actual=5.0, forecast=None, previous=None))
        self.assertEqual(read["surprise"], "unknown")
        self.assertEqual(read["currency_dir"], 0)

    def test_non_us_event_asserts_no_gold_direction(self):
        read = cf.classify(ev(title="CPI", country="EU", actual=2.5, forecast=2.0))
        self.assertEqual(read["currency"], "EUR")
        self.assertEqual(read["currency_dir"], 1)
        self.assertEqual(read["gold_dir"], 0)      # indirect, not asserted
        self.assertFalse(read["certain"])


class ReleasedTests(unittest.TestCase):
    def test_only_events_with_an_actual_are_returned(self):
        events = [ev(actual=None, at=900), ev(actual=1.0, at=900)]
        out = cf.released(events, now=1000, lookback_h=24)
        self.assertEqual(len(out), 1)

    def test_lookback_window_is_respected(self):
        events = [ev(actual=1.0, at=1000 - 30 * 3600),
                  ev(actual=1.0, at=1000 - 2 * 3600)]
        out = cf.released(events, now=1000, lookback_h=24)
        self.assertEqual(len(out), 1)

    def test_low_impact_is_excluded(self):
        events = [ev(importance=-1, actual=1.0, at=999),
                  ev(importance=0, actual=1.0, at=999)]
        out = cf.released(events, now=1000, lookback_h=24)
        self.assertEqual([e["importance"] for e in out], [0])

    def test_newest_first(self):
        events = [ev(actual=1.0, at=900), ev(actual=1.0, at=980)]
        out = cf.released(events, now=1000, lookback_h=24)
        self.assertEqual([e["at_utc"] for e in out], [980, 900])


class FormatTests(unittest.TestCase):
    def test_message_names_the_baseline_and_the_direction(self):
        event = ev(title="ADP Employment Change Weekly", actual=24.75,
                   previous=23.75)
        text = cf.format_release(event, cf.classify(event))
        self.assertIn("التغيّر الأسبوعي في التوظيف (ADP)", text)
        self.assertIn("24.75", text)
        self.assertIn("السابق (بلا تقدير)", text)
        self.assertIn("USD أقوى", text)
        self.assertIn("ضغط على الذهب", text)

    def test_non_us_message_refuses_to_assert_gold(self):
        event = ev(title="CPI", country="EU", actual=2.5, forecast=2.0)
        text = cf.format_release(event, cf.classify(event))
        self.assertIn("غير مباشر", text)
        self.assertNotIn("ضغط على الذهب", text)

    def test_unknown_baseline_is_stated_not_guessed(self):
        event = ev(actual=5.0)
        text = cf.format_release(event, cf.classify(event))
        self.assertIn("ما في تقدير ولا سابق", text)


class NormalizeTests(unittest.TestCase):
    def test_iso_stamp_and_numbers_are_parsed(self):
        rows = [{"id": 1, "title": "CPI m/m", "country": "US", "importance": 1,
                 "date": "2026-10-14T12:30:00.000Z", "actual": 0.6,
                 "forecast": 0.5, "previous": 0.4}]
        out = cf.normalize(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["actual"], 0.6)
        self.assertEqual(out[0]["forecast"], 0.5)
        self.assertTrue(out[0]["at_utc"] > 0)

    def test_missing_actual_stays_none_not_zero(self):
        rows = [{"title": "CPI", "country": "US", "date": "2026-10-14T12:30:00.000Z",
                 "actual": None, "forecast": None, "previous": None}]
        out = cf.normalize(rows)
        self.assertIsNone(out[0]["actual"])
        self.assertIsNone(out[0]["forecast"])


if __name__ == "__main__":
    unittest.main()
