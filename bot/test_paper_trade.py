"""Tests for the paper-trading observer.

The network, the feed and the clock are not exercised: the pieces that decide
what gets recorded and how it is scored are pure, and they are what a regression
would break. The rule itself is imported from the module under test, so a change
to the rule's definition is caught here rather than silently shifting the trial.

Run: python3 -m unittest discover -s bot -p "test_paper_trade.py"
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for extra in (HERE, os.path.join(ROOT, "tracker")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import paper_trade as paper  # noqa: E402


class TradeIdTests(unittest.TestCase):
    def test_id_is_stable_and_field_ordered(self):
        a = {"time": 100, "direction": "LONG", "entry": 4200.0}
        b = {"time": 100, "direction": "LONG", "entry": 4200.0, "extra": 1}
        self.assertEqual(paper.trade_id(a), paper.trade_id(b))

    def test_id_differs_on_direction(self):
        a = {"time": 100, "direction": "LONG", "entry": 4200.0}
        b = {"time": 100, "direction": "SHORT", "entry": 4200.0}
        self.assertNotEqual(paper.trade_id(a), paper.trade_id(b))


class LedgerTests(unittest.TestCase):
    def test_append_is_idempotent(self):
        rows = [{"time": 100, "direction": "LONG", "entry": 4200.0, "r_net": 1.0}]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "paper.jsonl")
            self.assertEqual(paper.append_ledger(rows, path), 1)
            self.assertEqual(paper.append_ledger(rows, path), 0)
            self.assertEqual(len(paper.read_ledger(path)), 1)

    def test_read_skips_torn_lines(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "paper.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"time": 1, "direction": "LONG", "entry": 1}) + "\n")
                fh.write("{not json\n")
                fh.write(json.dumps({"time": 2, "direction": "SHORT", "entry": 2}) + "\n")
            self.assertEqual(len(paper.read_ledger(path)), 2)


class ScoreboardTests(unittest.TestCase):
    def test_rate_only_when_sufficient(self):
        rows = [{"outcome": "TARGET", "r_net": 1.5},
                {"outcome": "STOP", "r_net": -1.0}] * 20
        sb = paper.scoreboard(rows, min_scored=30)
        self.assertTrue(sb["sufficient"])
        self.assertEqual(sb["win_rate"], 50.0)
        self.assertEqual(sb["scored"], 40)

    def test_thin_sample_withholds_the_rate(self):
        rows = [{"outcome": "TARGET", "r_net": 1.5},
                {"outcome": "STOP", "r_net": -1.0}]
        sb = paper.scoreboard(rows, min_scored=30)
        self.assertFalse(sb["sufficient"])
        self.assertNotIn("win_rate", sb)

    def test_timeouts_are_not_scored(self):
        rows = [{"outcome": "TARGET", "r_net": 1.5},
                {"outcome": "TIMEOUT", "r_net": 0.0}]
        sb = paper.scoreboard(rows, min_scored=1)
        self.assertEqual(sb["scored"], 1)
        self.assertEqual(sb["wins"], 1)

    def test_net_r_sums_all_resolved_rows(self):
        rows = [{"outcome": "TARGET", "r_net": 1.5},
                {"outcome": "STOP", "r_net": -1.0},
                {"outcome": "TIMEOUT", "r_net": 0.2}]
        sb = paper.scoreboard(rows, min_scored=1)
        self.assertEqual(sb["net_r"], 0.7)
        self.assertEqual(sb["r_per_trade"], round(0.7 / 3, 4))


class RuleShapeTests(unittest.TestCase):
    def test_rule_thresholds_are_the_plan_c_ones(self):
        # If this changes, the trial is no longer testing the pre-specified rule.
        self.assertEqual(paper.POST_NEWS_MIN, 120.0)
        self.assertEqual(paper.ALIGN, "ALIGNED")


if __name__ == "__main__":
    unittest.main()
