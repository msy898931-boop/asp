"""Tests for the one-shot cloud scanner's event selection.

Pure logic over synthetic signals: the point is that a stateless run turns each
bar into at most one idea and one result, and never both for the same signal.

Run: python3 -m unittest discover -s bot -p "test_*.py"
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
for extra in (os.path.join(os.path.dirname(HERE), "tracker"),):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import scan_once  # noqa: E402
from replay import Signal  # noqa: E402


def sig(time_value: int, resolved: bool = False, resolved_time: int | None = None):
    s = Signal(index=0, time=time_value, direction="LONG", entry=4000.0,
               stop=3990.0, target=4020.0, bias="BULLISH", regime="QUIET",
               rsi=55.0)
    s.resolved = resolved
    s.resolved_time = resolved_time
    return s


class PendingTests(unittest.TestCase):
    def test_first_run_primes_silently(self):
        ideas, results, priming = scan_once.pending([sig(900)], {})
        self.assertTrue(priming)
        self.assertEqual((ideas, results), ([], []))

    def test_new_idea_on_new_bar(self):
        ideas, results, priming = scan_once.pending(
            [sig(900)], {"primed": True, "last_idea_bar": 600, "last_result_bar": 600})
        self.assertFalse(priming)
        self.assertEqual(len(ideas), 1)
        self.assertEqual(results, [])

    def test_result_on_new_bar(self):
        ideas, results, _ = scan_once.pending(
            [sig(300, resolved=True, resolved_time=900)],
            {"primed": True, "last_idea_bar": 600, "last_result_bar": 600})
        self.assertEqual(ideas, [])
        self.assertEqual(len(results), 1)

    def test_already_alerted_bar_is_not_repeated(self):
        state = {"primed": True, "last_idea_bar": 900, "last_result_bar": 900}
        ideas, results, _ = scan_once.pending(
            [sig(900), sig(300, resolved=True, resolved_time=900)], state)
        self.assertEqual((ideas, results), ([], []))

    def test_sleeping_through_bars_still_catches_them_all(self):
        # A run every 30 minutes must still surface both M15 bars it slept
        # through: selection is by delta, not by "the newest bar only".
        signals = [sig(900), sig(1200), sig(1500)]
        ideas, _, _ = scan_once.pending(
            signals, {"primed": True, "last_idea_bar": 600, "last_result_bar": 600})
        self.assertEqual([s.time for s in ideas], [900, 1200, 1500])

    def test_idea_and_result_categories_are_independent(self):
        ideas, results, _ = scan_once.pending(
            [sig(1200), sig(300, resolved=True, resolved_time=1200)],
            {"primed": True, "last_idea_bar": 900, "last_result_bar": 1200})
        self.assertEqual([s.time for s in ideas], [1200])
        self.assertEqual(results, [])


class HeartbeatTests(unittest.TestCase):
    def test_a_trade_always_speaks(self):
        self.assertTrue(scan_once.should_heartbeat(
            {"decision": "TRADE", "signal": {}}))

    def test_holding_an_open_trade_stays_quiet(self):
        self.assertFalse(scan_once.should_heartbeat(
            {"decision": "NO_TRADE", "reason": "IN_POSITION", "open": {}}))

    def test_cooldown_stays_quiet(self):
        self.assertFalse(scan_once.should_heartbeat(
            {"decision": "NO_TRADE", "reason": "COOLDOWN"}))

    def test_a_real_no_edge_read_still_speaks(self):
        self.assertTrue(scan_once.should_heartbeat(
            {"decision": "NO_TRADE", "reason": "NO_EDGE"}))

    def test_a_resolution_still_speaks(self):
        self.assertTrue(scan_once.should_heartbeat(
            {"decision": "NO_TRADE", "reason": "JUST_RESOLVED", "resolved": {}}))


class StaleFeedTests(unittest.TestCase):
    def test_a_fresh_bar_is_not_stale(self):
        # A just-closed M15 bar opens up to 15 minutes ago: never stale.
        self.assertIsNone(scan_once.staleness(1000, now=1000 + 14 * 60))

    def test_a_quiet_feed_is_stale_and_reports_the_age(self):
        age = scan_once.staleness(1000, now=1000 + 90 * 60, threshold_min=45)
        self.assertAlmostEqual(age, 90.0, places=1)

    def test_missing_timestamp_is_not_stale(self):
        self.assertIsNone(scan_once.staleness(0, now=10_000_000))

    def test_threshold_is_configurable(self):
        self.assertIsNone(scan_once.staleness(1000, now=1000 + 40 * 60, threshold_min=45))
        self.assertIsNotNone(scan_once.staleness(1000, now=1000 + 40 * 60, threshold_min=10))


class DailyDigestTests(unittest.TestCase):
    def test_not_due_before_the_hour(self):
        now = scan_once.datetime(2026, 10, 13, 6, 45, tzinfo=scan_once.timezone.utc)
        self.assertFalse(scan_once.due_daily_digest({}, now, 7))

    def test_due_on_first_run_at_or_after_the_hour(self):
        now = scan_once.datetime(2026, 10, 13, 7, 0, tzinfo=scan_once.timezone.utc)
        self.assertTrue(scan_once.due_daily_digest({}, now, 7))

    def test_sent_once_per_day(self):
        now = scan_once.datetime(2026, 10, 13, 7, 30, tzinfo=scan_once.timezone.utc)
        state = {"last_digest_day": "2026-10-13"}
        self.assertFalse(scan_once.due_daily_digest(state, now, 7))
        tomorrow = scan_once.datetime(2026, 10, 14, 7, 0, tzinfo=scan_once.timezone.utc)
        self.assertTrue(scan_once.due_daily_digest(state, tomorrow, 7))

    def test_a_missed_hour_still_sends_later(self):
        # No run at 07:xx (host asleep); the first run after must still send.
        now = scan_once.datetime(2026, 10, 13, 11, 20, tzinfo=scan_once.timezone.utc)
        self.assertTrue(scan_once.due_daily_digest({}, now, 7))

    def test_digest_withholds_win_rate_on_small_sample(self):
        text = scan_once.digest_text(
            {"total_signals": 10, "wins": 2, "losses": 6, "min_scored": 30,
             "total_r_net": -3.03, "total_r_gross": -2.5, "sufficient": False})
        self.assertIn("—", text)
        self.assertNotIn("%", text.split("نسبة الإصابة")[1].split("\n")[0])

    def test_digest_states_win_rate_when_sufficient(self):
        text = scan_once.digest_text(
            {"total_signals": 40, "wins": 22, "losses": 18, "min_scored": 30,
             "total_r_net": 4.0, "total_r_gross": 5.0, "sufficient": True,
             "win_rate": 55.0})
        self.assertIn("55.0%", text)


if __name__ == "__main__":
    unittest.main()
