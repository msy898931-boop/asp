"""Tests for the released-event alert's selection and dedup.

Pure logic: which released events deserve a message, and never sending the same
one twice. No network, no clock.

Run: python3 -m unittest discover -s bot -p "test_*.py"
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for extra in (os.path.join(ROOT, "advisor"), HERE):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import calendar_feed as cal  # noqa: E402
import news_result as nr  # noqa: E402


def ev(title, actual=None, forecast=None, previous=None, importance=1,
       country="US", at=1000):
    return {"title": title, "title_ar": cal.arabic_event_name(title),
            "country": country, "importance": importance, "at_utc": at,
            "actual": actual, "forecast": forecast, "previous": previous}


class RelevanceTests(unittest.TestCase):
    def test_high_impact_is_relevant(self):
        self.assertTrue(nr.is_relevant(ev("Whatever Index", importance=1)))

    def test_low_impact_trivial_is_not_relevant(self):
        self.assertFalse(nr.is_relevant(ev("Some Minor Survey", importance=-1)))

    def test_medium_but_gold_moving_is_relevant(self):
        # ADP weekly is medium-impact on the calendar but moves gold.
        self.assertTrue(nr.is_relevant(
            ev("ADP Employment Change Weekly", importance=0)))

    def test_medium_and_trivial_is_not_relevant(self):
        self.assertFalse(nr.is_relevant(ev("Existing Home Sales", importance=0)))


class DueTests(unittest.TestCase):
    def test_unreleased_is_not_due(self):
        events = [ev("CPI m/m", actual=None, forecast=0.2, at=990)]
        self.assertEqual(nr.due_results(events, set(), now=1000), [])

    def test_already_alerted_is_skipped(self):
        e = ev("CPI m/m", actual=0.3, forecast=0.2, at=990)
        alerted = {nr.event_key(e)}
        self.assertEqual(nr.due_results([e], alerted, now=1000), [])

    def test_fresh_release_is_due(self):
        e = ev("CPI m/m", actual=0.3, forecast=0.2, at=990)
        due = nr.due_results([e], set(), now=1000)
        self.assertEqual(len(due), 1)

    def test_old_release_outside_lookback_is_not_due(self):
        e = ev("CPI m/m", actual=0.3, forecast=0.2, at=1000 - 10 * 3600)
        self.assertEqual(nr.due_results([e], set(), now=1000), [])

    def test_irrelevant_release_is_not_due(self):
        e = ev("Some Minor Survey", actual=1, forecast=0, importance=-1, at=990)
        self.assertEqual(nr.due_results([e], set(), now=1000), [])


class StateTests(unittest.TestCase):
    def test_roundtrip(self):
        import tempfile
        nr.STATE_PATH = os.path.join(tempfile.mkdtemp(), "s.json")
        nr.save_state({"alerted": ["a", "b"]})
        self.assertEqual(nr.load_state()["alerted"], ["a", "b"])

    def test_missing_file_is_empty(self):
        nr.STATE_PATH = "/nonexistent/dir/s.json"
        self.assertEqual(nr.load_state(), {})


if __name__ == "__main__":
    unittest.main()
