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


if __name__ == "__main__":
    unittest.main()
