#!/usr/bin/env python3
"""Tests for akoan's causal news state and the ledger join.

The property that matters most here is causality: a trade must never be
explained by an event that had not happened yet. These tests pin that, plus the
de-duplication that keeps one trade from being counted hundreds of times.

Run: python3 -m unittest discover -s akoan -p "test_*.py"
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for extra in (HERE, os.path.join(ROOT, "advisor")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import events as ev  # noqa: E402
import link as lk  # noqa: E402
import state as ns  # noqa: E402


def event(at, title="CPI", country="US", importance=1, actual=None,
          forecast=0.3, previous=0.2):
    return {"at_utc": at, "title": title, "title_ar": title, "country": country,
            "importance": importance, "actual": actual, "forecast": forecast,
            "previous": previous}


def trade(at, direction="LONG", outcome="TARGET", resolved=True, **kw):
    row = {"time": at, "direction": direction, "outcome": outcome,
           "resolved": resolved, "r_net": 1.6 if outcome == "TARGET" else -1.0,
           "regime": "TREND", "entry": 4000.0, "stop": 3995.0, "target": 4010.0}
    row.update(kw)
    return row


class CausalityTests(unittest.TestCase):
    def test_a_later_event_cannot_explain_an_earlier_trade(self):
        # The release prints 10 minutes *after* the trade: invisible to it.
        events = [event(at=1000, actual=0.9)]
        st = ns.state_at(events, ts=900, direction="LONG")
        self.assertFalse(st["recent_release"])
        self.assertFalse(st["blackout"])
        self.assertEqual(st["alignment"], "NEUTRAL")

    def test_blackout_is_seen_when_it_contains_the_trade(self):
        events = [event(at=1000, actual=0.9)]
        st = ns.state_at(events, ts=1005, direction="LONG")
        self.assertTrue(st["blackout"])

    def test_recent_release_is_seen_after_it_prints(self):
        events = [event(at=1000, actual=0.9)]
        st = ns.state_at(events, ts=1000 + 30 * 60, direction="LONG")
        self.assertTrue(st["recent_release"])
        self.assertFalse(st["blackout"])

    def test_upcoming_is_a_scheduled_event_only(self):
        # Not yet printed (actual None) but visible as scheduled, 20 minutes out.
        events = [event(at=1000 + 20 * 60, actual=None)]
        st = ns.state_at(events, ts=1000, direction="LONG")
        self.assertTrue(st["upcoming"])
        self.assertEqual(st["upcoming_minutes"], 20)

    def test_no_events_means_no_news(self):
        st = ns.state_at([], ts=1000, direction="LONG")
        self.assertFalse(st["news_in_play"])
        self.assertEqual(st["alignment"], "NEUTRAL")


class AlignmentTests(unittest.TestCase):
    def test_gold_up_read_aligns_with_a_long(self):
        # A weak USD print (below forecast) supports gold -> gold_dir +1.
        events = [event(at=1000, title="Non Farm Payrolls", actual=0.1)]
        st = ns.state_at(events, ts=1000 + 60, direction="LONG")
        self.assertEqual(st["recent_gold_dir"], 1)
        self.assertEqual(st["alignment"], "WITH_NEWS")

    def test_gold_down_read_conflicts_with_a_long(self):
        events = [event(at=1000, title="CPI", actual=0.9)]
        st = ns.state_at(events, ts=1000 + 60, direction="LONG")
        self.assertEqual(st["recent_gold_dir"], -1)
        self.assertEqual(st["alignment"], "AGAINST_NEWS")

    def test_non_us_event_is_neutral_for_gold(self):
        events = [event(at=1000, title="EU CPI", country="EU", actual=0.9)]
        st = ns.state_at(events, ts=1000 + 60, direction="LONG")
        self.assertEqual(st["alignment"], "NEUTRAL")


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "events.jsonl")

    def test_append_then_dedup(self):
        recs = [ev._record(event(at=1000), "test")]
        self.assertEqual(ev.append(recs, self.path), 1)
        self.assertEqual(ev.append(recs, self.path), 0)  # same key, no growth
        self.assertEqual(ev.coverage(self.path)["events"], 1)

    def test_key_ignores_actual_so_a_revision_does_not_duplicate(self):
        before = ev._record(event(at=1000, actual=None), "a")
        after = ev._record(event(at=1000, actual=0.5), "b")
        self.assertEqual(before["key"], after["key"])

    def test_a_torn_line_does_not_poison_the_ledger(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(ev._record(event(at=1000), "a")) + "\n")
            fh.write("{not json\n")
        self.assertEqual(ev.coverage(self.path)["events"], 1)

    def test_coverage_counts_released_and_high_impact(self):
        recs = [ev._record(event(at=1000, actual=0.5), "a"),
                ev._record(event(at=2000, actual=None, importance=0), "a")]
        ev.append(recs, self.path)
        cov = ev.coverage(self.path)
        self.assertEqual(cov["events"], 2)
        self.assertEqual(cov["released"], 1)
        self.assertEqual(cov["high_impact"], 1)


class SeedTests(unittest.TestCase):
    """The committed seed backs a fresh container that has no live ledger.

    A non-default path must read only that file, so tests and tooling that pass
    an explicit ledger are not polluted by the shipped seed.
    """

    def test_the_shipped_seed_exists_and_is_parseable(self):
        self.assertTrue(os.path.isfile(ev.SEED_PATH), ev.SEED_PATH)
        self.assertGreater(len(ev._read(ev.SEED_PATH)), 0)

    def test_an_explicit_path_reads_only_that_file(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "events.jsonl")
        ev.append([ev._record(event(at=1000), "a")], path)
        self.assertEqual(len(ev.load(path)), 1)

    def test_the_live_ledger_wins_over_the_seed_on_a_shared_key(self):
        seed = ev._read(ev.SEED_PATH)
        key = next(iter(seed))
        rec = dict(seed[key])
        rec["actual"] = "REVISED"
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "events.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        original = ev.LEDGER_PATH
        try:
            ev.LEDGER_PATH = path
            self.assertEqual(ev.load()["%s" % key]["actual"], "REVISED")
        finally:
            ev.LEDGER_PATH = original


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "trades.jsonl")

    def _write(self, rows):
        with open(self.path, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def test_open_trades_are_skipped(self):
        self._write([trade(1000), trade(2000, resolved=False, outcome="OPEN")])
        self.assertEqual(len(lk.load_trades(self.path)), 1)

    def test_repeated_appends_collapse_to_one_trade(self):
        # The tracker re-appends the same idea on every poll.
        self._write([trade(1000)] * 5)
        self.assertEqual(len(lk.load_trades(self.path)), 1)

    def test_different_levels_are_different_trades(self):
        self._write([trade(1000), trade(1000, entry=4001.0)])
        self.assertEqual(len(lk.load_trades(self.path)), 2)

    def test_label_and_win_flag(self):
        self.assertEqual(lk.label_of(trade(1, outcome="TARGET")), "WIN")
        self.assertIs(lk.win_flag(trade(1, outcome="TARGET")), True)
        self.assertIs(lk.win_flag(trade(1, outcome="STOP")), False)
        self.assertIsNone(lk.win_flag(trade(1, outcome="TIMEOUT")))

    def test_link_stamps_news_state(self):
        events = [event(at=1000, actual=0.9)]
        rows = lk.link([trade(1000 + 60)], events)
        self.assertEqual(rows[0]["recent_release"], "after_release")
        self.assertEqual(rows[0]["label"], "WIN")

    def test_link_window_respects_coverage(self):
        events = [event(at=1000)]
        rows = lk.link([trade(1000), trade(1000 - 10 * 86400)], events,
                       window_days=1)
        self.assertEqual(len(rows), 1)

    def test_scored_excludes_timeouts(self):
        events = []
        rows = lk.link([trade(1000), trade(2000, outcome="TIMEOUT")], events)
        self.assertEqual(len(lk.scored(rows)), 1)


if __name__ == "__main__":
    unittest.main()
