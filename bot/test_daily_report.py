"""Tests for the daily report's day selection, maths and honesty line.

Pure logic: which signals belong to a day, what the day adds up to, and that
the message never claims an edge on thin data. No network, no clock.

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

import daily_report as dr  # noqa: E402


def sig(t, direction="SHORT", outcome="TARGET", r_net=1.6, entry=4150.0,
        stop=4155.0, target=4140.0, bias="BEARISH", regime="TRENDING_DOWN",
        rsi=46.7):
    return {"time": t, "direction": direction, "outcome": outcome,
            "r_net": r_net, "entry": entry, "stop": stop, "target": target,
            "bias": bias, "regime": regime, "rsi": rsi}


def ts(day, hour, minute=0):
    return int(datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc).timestamp())


class SelectionTests(unittest.TestCase):
    def test_only_the_named_day_is_kept(self):
        tracker = {"signals": [sig(ts(13, 22)), sig(ts(14, 5)), sig(ts(14, 20))]}
        rows = dr.signals_for_day(tracker, "2026-10-14")
        self.assertEqual([r["time"] for r in rows], [ts(14, 5), ts(14, 20)])

    def test_day_boundary_is_utc(self):
        # 23:59 and 00:00 UTC land on different days.
        self.assertEqual(dr.day_of(ts(14, 23, 59)), "2026-10-14")
        self.assertEqual(dr.day_of(ts(15, 0, 0)), "2026-10-15")


class MathsTests(unittest.TestCase):
    def test_summary_counts_and_net(self):
        rows = [sig(ts(14, 5), r_net=1.6), sig(ts(14, 7), r_net=1.6),
                sig(ts(14, 12), outcome="STOP", r_net=-1.05)]
        s = dr.summarize(rows)
        self.assertEqual((s["closed"], s["wins"], s["losses"]), (3, 2, 1))
        self.assertAlmostEqual(s["net_r"], 2.15, places=2)

    def test_usd_is_minimum_position(self):
        # 1 oz: 1.6R over a $5 stop distance = $8.00.
        self.assertAlmostEqual(dr._usd(sig(ts(14, 5), r_net=1.6)), 8.0, places=2)

    def test_open_idea_is_listed_not_scored(self):
        rows = [sig(ts(14, 14), outcome="OPEN", r_net=None)]
        s = dr.summarize(rows)
        self.assertEqual(s["closed"], 0)
        self.assertEqual(len(s["open"]), 1)


class ReportTests(unittest.TestCase):
    def test_thin_data_is_flagged(self):
        rows = [sig(ts(14, 5)), sig(ts(14, 7))]
        text = dr.format_report(rows, "2026-10-14")
        self.assertIn("ما تكفي للحكم", text)
        self.assertIn("ليس نصيحة مالية", text)

    def test_empty_day_says_so(self):
        self.assertIn("ما في صفقات", dr.format_report([], "2026-10-14"))

    def test_report_lists_the_closed_trade(self):
        text = dr.format_report([sig(ts(14, 5))], "2026-10-14")
        self.assertIn("4150.0", text)
        self.assertIn("هدف", text)


class ReasonTests(unittest.TestCase):
    def test_reason_states_bias_regime_and_rsi(self):
        text = dr.format_report([sig(ts(14, 5))], "2026-10-14")
        self.assertIn("ميل هابط", text)
        self.assertIn("سوق اتجاه هابط", text)
        self.assertIn("RSI 47", text)

    def test_reason_shown_for_an_open_idea(self):
        rows = [sig(ts(14, 5), outcome="OPEN", r_net=None)]
        text = dr.format_report(rows, "2026-10-14")
        self.assertIn("مفتوحة", text)
        self.assertIn("ميل هابط", text)

    def test_reason_omits_what_is_missing(self):
        row = sig(ts(14, 5))
        row.pop("bias")
        row.pop("rsi")
        self.assertEqual(dr._reason(row), "سوق اتجاه هابط")

    def test_reason_empty_when_nothing_known(self):
        self.assertEqual(dr._reason({}), "")


if __name__ == "__main__":
    unittest.main()
