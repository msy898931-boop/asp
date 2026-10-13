"""Tests for the news alert: the due-window and dedup logic, no network."""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import news_alert as na  # noqa: E402


def ev(title, hours, at=1_791_981_000, ccy="USD", **kw):
    base = {"title": title, "country": ccy, "at_utc": at, "in_hours": hours}
    base.update(kw)
    return base


class DueAlertTests(unittest.TestCase):
    def test_only_events_inside_the_lead_window(self):
        events = [ev("soon", 0.5), ev("far", 39.0), ev("past", -2.0),
                  ev("edge", 1.0)]
        due = na.due_alerts(events, set(), lead_hours=1.0)
        self.assertEqual([e["title"] for e in due], ["soon", "edge"])

    def test_already_alerted_is_skipped(self):
        events = [ev("CPI m/m", 0.5)]
        key = na.event_key(events[0])
        self.assertEqual(na.due_alerts(events, {key}), [])

    def test_missing_hours_is_ignored(self):
        self.assertEqual(na.due_alerts([ev("x", None)], set()), [])

    def test_sorted_by_time(self):
        events = [ev("b", 0.5, at=200), ev("a", 0.5, at=100)]
        due = na.due_alerts(events, set())
        self.assertEqual([e["title"] for e in due], ["a", "b"])


class FormatTests(unittest.TestCase):
    def test_alert_names_the_event_in_arabic(self):
        text = na.format_alert(ev("CPI m/m", 0.5,
                                  title_ar="مؤشر أسعار المستهلك (CPI)",
                                  forecast="0.6%", previous="0.4%"))
        self.assertIn("مؤشر أسعار المستهلك (CPI)", text)
        self.assertIn("0.6%", text)
        self.assertIn("30 دقيقة", text)
        self.assertIn("لا تدخل قبل الخبر", text)

    def test_key_is_stable(self):
        a = ev("CPI m/m", 0.5)
        self.assertEqual(na.event_key(a), na.event_key(dict(a)))


if __name__ == "__main__":
    unittest.main()
