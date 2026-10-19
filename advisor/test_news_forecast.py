#!/usr/bin/env python3
"""Unit tests for the pre-news forecast (pure logic, no network)."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import news_forecast as nf

NOW = 1_800_000_000.0


def _event(offset_min, title="US CPI", importance=1, country="US",
           forecast=3.1, previous=3.0):
    return {"title": title, "title_ar": title, "country": country,
            "importance": importance, "at_utc": int(NOW + offset_min * 60),
            "actual": None, "forecast": forecast, "previous": previous}


class FamilyTest(unittest.TestCase):
    def test_inflation(self):
        self.assertEqual(nf.event_family("Inflation Rate MoM"), "inflation")
        self.assertEqual(nf.event_family("Core CPI y/y"), "inflation")

    def test_jobs(self):
        self.assertEqual(nf.event_family("Nonfarm Payrolls"), "jobs")
        self.assertEqual(nf.event_family("Unemployment Rate"), "jobs")

    def test_rates(self):
        self.assertEqual(nf.event_family("FOMC Statement"), "rates")
        self.assertEqual(nf.event_family("Interest Rate Decision"), "rates")

    def test_housing_and_activity(self):
        self.assertEqual(nf.event_family("Building Permits"), "housing")
        self.assertEqual(nf.event_family("Retail Sales m/m"), "activity")

    def test_unknown_falls_to_other(self):
        self.assertEqual(nf.event_family("Something Nobody Named"), "other")
        self.assertEqual(nf.event_family(""), "other")


class ExpectedMoveTest(unittest.TestCase):
    def test_median_rises_with_horizon(self):
        m15 = nf.expected_move("other", 15)["median"]
        m30 = nf.expected_move("other", 30)["median"]
        m60 = nf.expected_move("other", 60)["median"]
        self.assertLess(m15, m30)
        self.assertLess(m30, m60)

    def test_p90_exceeds_median(self):
        row = nf.expected_move("other", 60)
        self.assertGreater(row["p90"], row["median"])

    def test_nearest_horizon_is_used(self):
        self.assertEqual(nf.expected_move("other", 29)["horizon_min"], 30)
        self.assertEqual(nf.expected_move("other", 61)["horizon_min"], 60)

    def test_hot_family_reads_larger_than_cool_one(self):
        hot = nf.expected_move("jobs", 60)["median"]
        cool = nf.expected_move("housing", 60)["median"]
        self.assertGreater(hot, cool)

    def test_unknown_family_is_flagged(self):
        row = nf.expected_move("nonexistent", 60)
        self.assertFalse(row["family_known"])

    def test_sample_size_is_carried(self):
        self.assertGreater(nf.expected_move("other", 60)["n"], 0)


class NextEventTest(unittest.TestCase):
    def test_picks_the_soonest_inside_the_horizon(self):
        far = _event(80, title="CPI")
        near = _event(20, title="NFP")
        self.assertIs(nf.next_event([far, near], NOW), near)

    def test_ignores_events_beyond_the_horizon(self):
        self.assertIsNone(nf.next_event([_event(200)], NOW))

    def test_ignores_low_impact(self):
        self.assertIsNone(nf.next_event([_event(20, importance=0)], NOW))

    def test_ignores_past_events(self):
        self.assertIsNone(nf.next_event([_event(-20)], NOW))

    def test_empty_is_none(self):
        self.assertIsNone(nf.next_event([], NOW))


class LevelsTest(unittest.TestCase):
    def test_bracket_straddles_price(self):
        lv = nf._levels(4000.0, 5.0)
        self.assertLess(lv["break_down"], lv["price"])
        self.assertGreater(lv["break_up"], lv["price"])

    def test_stop_matches_the_band_and_target_doubles_it(self):
        lv = nf._levels(4000.0, 5.0)
        self.assertEqual(lv["stop_distance"], 5.0)
        self.assertEqual(lv["target_distance"], 10.0)
        self.assertEqual(lv["rr"], 2.0)

    def test_no_price_yields_no_levels(self):
        self.assertIsNone(nf._levels(None, 5.0))
        self.assertIsNone(nf._levels(0, 5.0))
        self.assertIsNone(nf._levels(4000.0, 0))


class PlanTest(unittest.TestCase):
    def test_plan_never_claims_a_direction(self):
        p = nf.plan(_event(30), 4000.0, NOW)
        self.assertEqual(p["direction"], "unknown")
        self.assertIn("غير قابل", p["direction_note"])

    def test_plan_has_an_invalidation(self):
        p = nf.plan(_event(30), 4000.0, NOW)
        self.assertIn("ألغِ", p["invalidation"])

    def test_hot_family_adds_a_sizing_warning(self):
        p = nf.plan(_event(30, title="Nonfarm Payrolls"), 4000.0, NOW)
        self.assertTrue(any("صغّر الحجم" in s for s in p["steps"]))

    def test_cool_family_has_no_sizing_warning(self):
        p = nf.plan(_event(30, title="Building Permits"), 4000.0, NOW)
        self.assertFalse(any("صغّر الحجم" in s for s in p["steps"]))

    def test_levels_present_when_price_known(self):
        p = nf.plan(_event(30), 4000.0, NOW)
        self.assertIsNotNone(p["levels"])

    def test_minutes_until_is_reported(self):
        p = nf.plan(_event(45), 4000.0, NOW)
        self.assertEqual(p["in_minutes"], 45)


class ForecastTest(unittest.TestCase):
    def test_clear_when_no_event(self):
        d = nf.forecast(now=NOW, events=[], price=4000.0)
        self.assertEqual(d["state"], "clear")
        self.assertIsNone(d["event"])
        self.assertIsNone(d["plan"])

    def test_prep_when_event_soon(self):
        d = nf.forecast(now=NOW, events=[_event(30)], price=4000.0)
        self.assertEqual(d["state"], "prep")
        self.assertIsNotNone(d["plan"])

    def test_active_inside_the_blackout(self):
        # 5 minutes after the print is inside the default 15-minute window.
        d = nf.forecast(now=NOW, events=[_event(-5)], price=4000.0)
        self.assertEqual(d["state"], "active")
        self.assertTrue(d["blackout"])

    def test_carries_the_honesty_notes(self):
        d = nf.forecast(now=NOW, events=[], price=4000.0)
        self.assertTrue(any("الاتجاه" in n for n in d["notes"]))


class FormatTest(unittest.TestCase):
    def test_clear_message_mentions_no_plan(self):
        text = nf.format_forecast(nf.forecast(now=NOW, events=[], price=4000.0))
        self.assertIn("ما في إصدار", text)

    def test_prep_message_shows_the_plan(self):
        text = nf.format_forecast(
            nf.forecast(now=NOW, events=[_event(30)], price=4000.0))
        self.assertIn("خطة", text)
        self.assertIn("الاتجاه غير متوقّع", text)

    def test_prep_message_never_says_buy_or_sell_a_side(self):
        text = nf.format_forecast(
            nf.forecast(now=NOW, events=[_event(30)], price=4000.0))
        # The bracket is directional; the *claim* must not be.
        self.assertNotIn("سيصعد", text)
        self.assertNotIn("سينزل", text)


if __name__ == "__main__":
    unittest.main()
