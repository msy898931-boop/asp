#!/usr/bin/env python3
"""Tests for the weekly akoan report: week selection, wording and honesty.

Pure logic: which week a run belongs to, what the message says, and that it
never claims an edge on thin data. The study is stubbed — these tests do not
read the corpus or touch the network.

Run: python3 -m unittest discover -s bot -p "test_*.py"
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import weekly_akoan as wk  # noqa: E402


def bucket(value, n=20, win_rate=0.3, edge=0.0, significant=False,
           insufficient=False):
    return {"value": value, "n": n, "wins": int(n * (win_rate or 0)),
            "losses": n - int(n * (win_rate or 0)), "timeouts": 0, "net_r": -2.0,
            "win_rate": None if insufficient else win_rate,
            "edge_vs_base": None if insufficient else edge,
            "significant": significant}


def result(verdict="NO_EDGE_MEASURED", survivors=0, dims=None, coverage=None):
    return {
        "engine": "akoan", "verdict": verdict, "scored": 353, "wins": 123,
        "losses": 230, "net_r": -33.44, "base_rate": 0.348, "survivors": survivors,
        "fdr_q": 0.10,
        "dimensions": dims if dims is not None else [
            {"name": "blackout", "comparable": False,
             "buckets": [bucket("quiet", n=353, win_rate=0.348)]},
            {"name": "recent_release", "comparable": True,
             "buckets": [bucket("after_release", n=22, win_rate=0.364, edge=0.016),
                         bucket("no_release", n=331, win_rate=0.347, edge=-0.001)]},
        ],
        "coverage": coverage if coverage is not None else
                    {"events": 1659, "released": 1332, "span_days": 119},
    }


class WeekTests(unittest.TestCase):
    def test_iso_week_format(self):
        now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(wk.iso_week(now), "2026-W42")

    def test_week_changes_at_the_boundary(self):
        a = wk.iso_week(datetime(2026, 10, 18, tzinfo=timezone.utc))
        b = wk.iso_week(datetime(2026, 10, 19, tzinfo=timezone.utc))
        self.assertNotEqual(a, b)


class FormatTests(unittest.TestCase):
    def test_names_the_week_and_the_baseline(self):
        text = wk.format_weekly(result(), "2026-W42")
        self.assertIn("2026-W42", text)
        self.assertIn("خط الأساس", text)

    def test_states_the_verdict_plainly(self):
        text = wk.format_weekly(result(verdict="NO_EDGE_MEASURED"), "2026-W42")
        self.assertIn("لا حافة", text)

    def test_says_when_a_dimension_cannot_be_compared(self):
        text = wk.format_weekly(result(), "2026-W42")
        self.assertIn("جهة واحدة فقط", text)

    def test_reports_coverage_and_its_caveat(self):
        text = wk.format_weekly(result(), "2026-W42")
        self.assertIn("تغطية", text)
        self.assertIn("جزئية", text)

    def test_always_disclaims(self):
        text = wk.format_weekly(result(), "2026-W42")
        self.assertIn("ليس نصيحة مالية", text)

    def test_insufficient_dimension_is_not_given_a_rate(self):
        dims = [{"name": "recent_release", "comparable": True,
                 "buckets": [bucket("after_release", n=5, insufficient=True),
                             bucket("no_release", n=300)]}]
        text = wk.format_weekly(result(dims=dims), "2026-W42")
        self.assertIn("غير كافية", text)

    def test_a_survivor_is_marked_and_explained(self):
        dims = [{"name": "recent_release", "comparable": True,
                 "buckets": [bucket("after_release", significant=True),
                             bucket("no_release")]}]
        text = wk.format_weekly(result(verdict="EDGE_CANDIDATE", survivors=1,
                                       dims=dims), "2026-W42")
        self.assertIn("⭐", text)
        self.assertIn("تصحيح التعدّد", text)

    def test_no_survivor_is_stated(self):
        text = wk.format_weekly(result(survivors=0), "2026-W42")
        self.assertIn("ولا بُعد نجا", text)

    def test_unknown_verdict_still_renders(self):
        text = wk.format_weekly(result(verdict="SOMETHING_NEW"), "2026-W42")
        self.assertIn("SOMETHING_NEW", text)

    def test_missing_coverage_does_not_crash(self):
        r = result()
        r.pop("coverage")
        text = wk.format_weekly(r, "2026-W42")
        self.assertIn("ليس نصيحة مالية", text)


class StateTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self._orig = wk.STATE_PATH
        wk.STATE_PATH = os.path.join(self.tmp, "state.json")

    def tearDown(self):
        wk.STATE_PATH = self._orig

    def test_round_trip(self):
        wk.save_state({"sent": ["2026-W41"]})
        self.assertEqual(wk.load_state()["sent"], ["2026-W41"])

    def test_missing_state_is_empty(self):
        self.assertEqual(wk.load_state(), {})

    def test_corrupt_state_is_empty(self):
        with open(wk.STATE_PATH, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(wk.load_state(), {})


if __name__ == "__main__":
    unittest.main()
