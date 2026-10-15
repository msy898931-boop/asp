"""Tests for the news-ledger builder and the news study.

Network fetching is not exercised here — the ledger builder's pure pieces are:
its stable event key, its record shape, and its month windows. The study's
joins are tested on synthetic trades and events so a regression in the causal
window or the news-state labels is caught without touching the network.

Run: python3 -m unittest discover -s research -p "test_*.py"
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FEATURES_REAL = os.path.join(HERE, "features_real")
REPO = os.path.dirname(HERE)
for extra in (FEATURES_REAL, os.path.join(REPO, "tracker")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import build_news_ledger as ledger  # noqa: E402
import news_study  # noqa: E402
import rule_test  # noqa: E402


class EventKeyTests(unittest.TestCase):
    def test_key_is_stable_and_case_folded(self):
        a = {"at_utc": 100, "title": "CPI m/m", "country": "us"}
        b = {"at_utc": 100, "title": "  cpi m/m ", "country": "US"}
        self.assertEqual(ledger.event_key(a), ledger.event_key(b))

    def test_key_differs_on_time(self):
        a = {"at_utc": 100, "title": "CPI", "country": "US"}
        b = {"at_utc": 101, "title": "CPI", "country": "US"}
        self.assertNotEqual(ledger.event_key(a), ledger.event_key(b))

    def test_record_keeps_null_actual(self):
        rec = ledger._record({"at_utc": 100, "title": "CPI", "country": "US",
                              "importance": 1, "actual": None})
        self.assertIsNone(rec["actual"])
        self.assertEqual(rec["source"], "tradingview")


class MonthWindowTests(unittest.TestCase):
    def test_twelve_windows_per_year(self):
        self.assertEqual(len(ledger.month_windows(2023)), 12)

    def test_windows_are_contiguous(self):
        w = ledger.month_windows(2023)
        for (a1, b1), (a2, _) in zip(w, w[1:]):
            self.assertEqual(b1, a2)

    def test_december_ends_at_next_year(self):
        w = ledger.month_windows(2023)
        self.assertGreater(w[-1][1], w[-1][0])
        self.assertEqual(w[-1][1] - w[0][0], 365 * 86400)


class LoaderTests(unittest.TestCase):
    def test_gzip_and_plain_agree(self):
        rows = [{"a": 1}, {"b": 2}]
        with tempfile.TemporaryDirectory() as d:
            plain = os.path.join(d, "x.jsonl")
            with open(plain, "w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r) + "\n")
            import gzip
            gz = plain + ".gz"
            with open(plain, "rb") as src, gzip.open(gz, "wb") as dst:
                dst.write(src.read())
            self.assertEqual(news_study.load_jsonl(plain), rows)
            self.assertEqual(news_study.load_jsonl(gz), rows)


class StampTests(unittest.TestCase):
    def _event(self, at, importance=1, actual=1.0, forecast=0.5, title="CPI"):
        return {"at_utc": at, "title": title, "title_ar": title, "country": "US",
                "importance": importance, "forecast": forecast,
                "previous": None, "actual": actual}

    def test_trade_after_a_release_is_stamped(self):
        events = [self._event(1_000_000)]
        store = [{"time": 1_000_000 + 1800, "direction": "LONG"}]
        news_study.stamp(store, events)
        self.assertTrue(store[0]["recent_release"])
        self.assertTrue(store[0]["news_in_play"])

    def test_trade_far_from_news_is_quiet(self):
        events = [self._event(1_000_000)]
        store = [{"time": 1_000_000 + 10 * 86400, "direction": "LONG"}]
        news_study.stamp(store, events)
        self.assertFalse(store[0]["news_in_play"])
        self.assertFalse(store[0]["recent_release"])

    def test_no_lookahead_before_the_event(self):
        # A trade an hour before the release must not see it as a recent print.
        events = [self._event(1_000_000)]
        store = [{"time": 1_000_000 - 3600, "direction": "LONG"}]
        news_study.stamp(store, events)
        self.assertFalse(store[0]["recent_release"])


class CandidateShapeTests(unittest.TestCase):
    def test_candidates_and_combinations_have_unique_names(self):
        for group in (news_study.candidates(), news_study.combinations()):
            names = [n for n, _ in group]
            self.assertEqual(len(names), len(set(names)))

    def test_after_release_predicate(self):
        pred = dict(news_study.candidates())["after a release"]
        self.assertTrue(pred({"recent_release": True}))
        self.assertFalse(pred({"recent_release": False}))


class RuleTests(unittest.TestCase):
    def test_the_rule_needs_both_conditions(self):
        self.assertTrue(rule_test.the_rule({"h4_align": "ALIGNED",
                                            "recent_release": True}))
        self.assertFalse(rule_test.the_rule({"h4_align": "AGAINST",
                                             "recent_release": True}))
        self.assertFalse(rule_test.the_rule({"h4_align": "ALIGNED",
                                             "recent_release": False}))

    def test_permutation_is_deterministic_for_a_seed(self):
        rows = [{"r_net": 1.0, "hit": True} for _ in range(20)]
        rows += [{"r_net": -1.0, "hit": False} for _ in range(20)]
        p1, d1 = rule_test.permutation_p(rows, lambda r: r["hit"], n_perm=500)
        p2, d2 = rule_test.permutation_p(rows, lambda r: r["hit"], n_perm=500)
        self.assertEqual((p1, d1), (p2, d2))

    def test_permutation_finds_a_clean_separation(self):
        # Perfectly separated groups: the gap cannot be produced by shuffling.
        rows = [{"r_net": 1.0, "hit": True} for _ in range(30)]
        rows += [{"r_net": -1.0, "hit": False} for _ in range(30)]
        p, diff = rule_test.permutation_p(rows, lambda r: r["hit"], n_perm=2000)
        self.assertLess(p, 0.01)
        self.assertAlmostEqual(diff, 2.0)

    def test_permutation_is_null_when_groups_are_identical(self):
        # Each group has the same 25 wins and 25 losses, so no real difference.
        rows = []
        for _ in range(25):
            rows.append({"r_net": 1.0, "hit": True})
            rows.append({"r_net": -1.0, "hit": False})
            rows.append({"r_net": 1.0, "hit": False})
            rows.append({"r_net": -1.0, "hit": True})
        p, _ = rule_test.permutation_p(rows, lambda r: r["hit"], n_perm=2000)
        self.assertGreater(p, 0.05)

    def test_empty_subset_returns_p_one(self):
        rows = [{"r_net": 1.0, "hit": False}]
        p, diff = rule_test.permutation_p(rows, lambda r: r["hit"], n_perm=100)
        self.assertEqual(p, 1.0)
        self.assertEqual(diff, 0.0)


if __name__ == "__main__":
    unittest.main()
