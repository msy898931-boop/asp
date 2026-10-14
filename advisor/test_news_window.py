#!/usr/bin/env python3
"""Unit tests for the news-quiet window (pure logic, no network)."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import news_window as nw  # noqa: E402

NOW = 1_800_000_000.0


def _event(offset_min, importance=1, title="US CPI", title_ar="مؤشر أسعار المستهلك (CPI)"):
    return {"title": title, "title_ar": title_ar, "country": "US",
            "importance": importance, "at_utc": int(NOW + offset_min * 60),
            "actual": None, "forecast": None, "previous": None}


class RelevanceTest(unittest.TestCase):
    def test_high_impact_is_relevant(self):
        self.assertTrue(nw.is_relevant(_event(0, importance=1)))

    def test_medium_and_low_are_not(self):
        self.assertFalse(nw.is_relevant(_event(0, importance=0)))
        self.assertFalse(nw.is_relevant(_event(0, importance=-1)))

    def test_missing_importance_is_not_relevant(self):
        self.assertFalse(nw.is_relevant({"title": "x"}))


class BlackoutTest(unittest.TestCase):
    def test_inside_the_before_window(self):
        ev = _event(5)  # prints in 5 minutes, window is 15
        self.assertIsNotNone(nw.blackout_event([ev], NOW))
        self.assertIn("بعد", nw.blackout_reason([ev], NOW))

    def test_inside_the_after_window(self):
        ev = _event(-5)
        self.assertIsNotNone(nw.blackout_event([ev], NOW))
        self.assertIn("قبل", nw.blackout_reason([ev], NOW))

    def test_just_outside_the_window_is_clear(self):
        self.assertIsNone(nw.blackout_event([_event(20)], NOW))
        self.assertIsNone(nw.blackout_event([_event(-20)], NOW))

    def test_boundaries_are_inclusive(self):
        self.assertIsNotNone(nw.blackout_event([_event(15)], NOW))
        self.assertIsNotNone(nw.blackout_event([_event(-15)], NOW))

    def test_most_recent_event_wins_on_overlap(self):
        older, newer = _event(-10, title="old"), _event(-2, title="new")
        hit = nw.blackout_event([older, newer], NOW)
        self.assertEqual(hit["title"], "new")

    def test_medium_events_do_not_trigger(self):
        self.assertIsNone(nw.blackout_event([_event(1, importance=0)], NOW))


class UpcomingTest(unittest.TestCase):
    def test_event_within_horizon(self):
        soon = nw.upcoming_event([_event(30)], NOW)
        self.assertIsNotNone(soon)
        self.assertIn("قريب", nw.blackout_reason([_event(30)], NOW))

    def test_event_beyond_horizon_is_not_upcoming(self):
        self.assertIsNone(nw.upcoming_event([_event(90)], NOW))

    def test_nearest_future_event_wins(self):
        far, near = _event(40, title="far"), _event(10, title="near")
        self.assertEqual(nw.upcoming_event([far, near], NOW)["title"], "near")

    def test_past_event_is_not_upcoming(self):
        self.assertIsNone(nw.upcoming_event([_event(-1)], NOW))


class ReasonTest(unittest.TestCase):
    def test_active_window_takes_precedence_over_upcoming(self):
        active, soon = _event(-2), _event(40)
        reason = nw.blackout_reason([active, soon], NOW)
        self.assertIn("نافذة خبر", reason)

    def test_clear_market_has_no_reason(self):
        self.assertIsNone(nw.blackout_reason([_event(200)], NOW))
        self.assertIsNone(nw.blackout_reason([], NOW))

    def test_reason_never_claims_direction(self):
        for off in (-5, 5, 30):
            reason = nw.blackout_reason([_event(off)], NOW)
            self.assertNotIn("صعود", reason)
            self.assertNotIn("هبوط", reason)
            self.assertNotIn("سيهبط", reason)


def _released(offset_min, actual, forecast=None, previous=None,
              country="US", title="US CPI", title_ar="مؤشر أسعار المستهلك (CPI)"):
    return {"title": title, "title_ar": title_ar,
            "country": country, "importance": 1,
            "at_utc": int(NOW + offset_min * 60),
            "actual": actual, "forecast": forecast, "previous": previous}


class RecentContextTest(unittest.TestCase):
    def test_nothing_recent_yields_none(self):
        self.assertIsNone(nw.recent_context([_released(-300, 3.1, 3.0)], NOW))
        self.assertIsNone(nw.recent_context([], NOW))

    def test_us_surprise_above_names_gold_pressure(self):
        ctx = nw.recent_context([_released(-8, 3.3, 3.0)], NOW)
        self.assertIn("أعلى", ctx)
        self.assertIn("ضغط على الذهب", ctx)

    def test_us_surprise_below_names_gold_support(self):
        ctx = nw.recent_context([_released(-8, 2.7, 3.0)], NOW)
        self.assertIn("أدنى", ctx)
        self.assertIn("دعم للذهب", ctx)

    def test_inline_print_has_no_surprise(self):
        ctx = nw.recent_context([_released(-8, 3.0, 3.0)], NOW)
        self.assertIn("بلا مفاجأة", ctx)

    def test_non_us_event_says_gold_link_is_indirect(self):
        ctx = nw.recent_context([_released(-8, 3.3, 3.0, country="EU",
                                           title="EU CPI")], NOW)
        self.assertIn("غير مباشر", ctx)

    def test_newest_relevant_event_wins(self):
        older = _released(-40, 3.3, 3.0, title="old", title_ar="الخبر القديم")
        newer = _released(-8, 3.3, 3.0, title="new", title_ar="الخبر الأحدث")
        ctx = nw.recent_context([older, newer], NOW)
        self.assertIn("الخبر الأحدث", ctx)

    def test_future_event_is_not_context(self):
        self.assertIsNone(nw.recent_context([_released(5, 3.3, 3.0)], NOW))

    def test_missing_actual_falls_back_to_a_plain_note(self):
        ctx = nw.recent_context([_released(-8, None, 3.0)], NOW)
        self.assertIn("آخر خبر مهم", ctx)
        self.assertNotIn("ضغط", ctx)


if __name__ == "__main__":
    unittest.main()
