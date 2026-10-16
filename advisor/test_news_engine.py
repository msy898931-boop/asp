#!/usr/bin/env python3
"""Tests for the news engine.

No network: `digest` takes an injectable event list, so the window logic, the
gold read, the baseline honesty and the ordering are all pinned offline.
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for extra in (HERE, os.path.join(REPO, "akoan")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import news_engine as ne  # noqa: E402


def _ev(at, title, country="US", imp=1, actual=None, forecast=None,
        previous=None, title_ar=None):
    return {"at_utc": at, "title": title, "title_ar": title_ar or title,
            "country": country, "importance": imp, "actual": actual,
            "forecast": forecast, "previous": previous}


class WindowTests(unittest.TestCase):
    NOW = 1_000_000.0

    def test_active_when_inside_the_blackout(self):
        ev = [_ev(self.NOW - 60, "CPI", actual=3.1, forecast=3.0)]
        w = ne._window(ev, self.NOW)
        self.assertEqual(w["state"], "active")
        self.assertIsNotNone(w["reason"])
        self.assertEqual(w["event"]["title"], "CPI")

    def test_upcoming_when_a_release_is_near(self):
        ev = [_ev(self.NOW + 20 * 60, "NFP")]
        w = ne._window(ev, self.NOW)
        self.assertEqual(w["state"], "upcoming")
        self.assertEqual(w["event"]["in_minutes"], 20)

    def test_clear_when_nothing_is_near(self):
        ev = [_ev(self.NOW + 5 * 3600, "NFP")]
        w = ne._window(ev, self.NOW)
        self.assertEqual(w["state"], "clear")
        self.assertIsNone(w["event"])
        self.assertIsNone(w["reason"])

    def test_low_impact_does_not_open_a_window(self):
        ev = [_ev(self.NOW + 60, "Minor", imp=-1)]
        self.assertEqual(ne._window(ev, self.NOW)["state"], "clear")


class UpcomingTests(unittest.TestCase):
    NOW = 2_000_000.0

    def test_only_inside_the_lookahead_and_medium_plus(self):
        ev = [
            _ev(self.NOW + 60, "soon", imp=0),
            _ev(self.NOW + 2 * 3600, "later", imp=1),
            _ev(self.NOW + 100 * 3600, "too far", imp=1),
            _ev(self.NOW - 60, "past", imp=1),
            _ev(self.NOW + 90, "low", imp=-1),
        ]
        out = ne._upcoming(ev, self.NOW)
        self.assertEqual([e["title"] for e in out], ["soon", "later"])

    def test_sorted_soonest_first(self):
        ev = [_ev(self.NOW + 3600, "b"), _ev(self.NOW + 600, "a")]
        out = ne._upcoming(ev, self.NOW)
        self.assertEqual([e["title"] for e in out], ["a", "b"])


class ReleasedTests(unittest.TestCase):
    NOW = 3_000_000.0

    def test_gold_read_is_inverted_from_the_dollar_for_a_us_release(self):
        # Actual above forecast -> dollar stronger -> pressure on gold.
        ev = [_ev(self.NOW - 600, "CPI", actual=3.2, forecast=3.0)]
        row = ne._released(ev, self.NOW)[0]
        self.assertEqual(row["surprise"], "above")
        self.assertEqual(row["currency"], "USD")
        self.assertEqual(row["currency_dir"], 1)
        self.assertEqual(row["gold_dir"], -1)
        self.assertTrue(row["certain"])
        self.assertEqual(row["minutes_ago"], 10)

    def test_no_baseline_asserts_no_direction(self):
        ev = [_ev(self.NOW - 60, "Mystery", actual=5.0)]
        row = ne._released(ev, self.NOW)[0]
        self.assertEqual(row["surprise"], "unknown")
        self.assertEqual(row["gold_dir"], 0)
        self.assertFalse(row["certain"])

    def test_non_us_link_is_not_asserted(self):
        ev = [_ev(self.NOW - 60, "ECB Rate", country="EU", actual=4.0,
                  forecast=3.5)]
        row = ne._released(ev, self.NOW)[0]
        self.assertEqual(row["currency_dir"], 1)
        self.assertEqual(row["gold_dir"], 0)   # indirect, not asserted
        self.assertFalse(row["certain"])

    def test_jobless_claims_invert_the_currency_read(self):
        # More claims is currency-negative, so a high print weakens the dollar.
        ev = [_ev(self.NOW - 60, "Initial Jobless Claims", actual=250,
                  forecast=220)]
        row = ne._released(ev, self.NOW)[0]
        self.assertEqual(row["currency_dir"], -1)
        self.assertEqual(row["gold_dir"], 1)


class DigestTests(unittest.TestCase):
    NOW = 4_000_000.0

    def test_digest_is_well_formed_without_network(self):
        ev = [_ev(self.NOW - 60, "CPI", actual=3.1, forecast=3.0),
              _ev(self.NOW + 3600, "NFP")]
        d = ne.digest(self.NOW, events=ev)
        self.assertEqual(d["engine"], "news")
        self.assertEqual(d["symbol"], "XAUUSD")
        self.assertEqual(d["counts"]["released"], 1)
        self.assertEqual(d["counts"]["upcoming"], 1)
        self.assertEqual(d["window"]["state"], "active")
        self.assertIn("notes", d)

    def test_empty_feed_is_an_empty_digest_not_an_error(self):
        d = ne.digest(self.NOW, events=[])
        self.assertEqual(d["counts"]["released"], 0)
        self.assertEqual(d["counts"]["upcoming"], 0)
        self.assertEqual(d["window"]["state"], "clear")

    def test_headlines_are_never_invented(self):
        # With no events the digest still carries the headline list (possibly
        # empty) as its own key, so a caller can tell "no news" from "no feed".
        d = ne.digest(self.NOW, events=[])
        self.assertIsInstance(d["headlines"], list)


if __name__ == "__main__":
    unittest.main()
