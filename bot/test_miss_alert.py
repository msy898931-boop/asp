"""Tests for the miss alert: fresh-miss selection, format, fetch. No network."""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for extra in (os.path.join(ROOT, "advisor"), HERE):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import miss_alert as ma  # noqa: E402
import self_review  # noqa: E402


def miss(stance="SHORT", at=1000, price=4100.0, after=4130.0, move=0.73):
    return {"generated_at_utc": at, "symbol": "XAUUSD", "stance": stance,
            "price": price, "price_after": after, "move_pct": move,
            "correct": False}


class NewMissTests(unittest.TestCase):
    def test_only_unalerted_misses(self):
        m = miss()
        self.assertEqual(self_review.new_misses([m], set()), [m])
        key = self_review.outcome_key(m)
        self.assertEqual(self_review.new_misses([m], {key}), [])

    def test_correct_calls_are_not_misses(self):
        good = dict(miss(), correct=True)
        self.assertEqual(self_review.new_misses([good], set()), [])

    def test_oldest_first(self):
        a, b = miss(at=200), miss(at=100)
        self.assertEqual([m["generated_at_utc"]
                          for m in self_review.new_misses([a, b], set())],
                         [100, 200])

    def test_key_is_stable(self):
        m = miss()
        self.assertEqual(self_review.outcome_key(m), self_review.outcome_key(dict(m)))


class FormatMissTests(unittest.TestCase):
    def test_message_is_arabic_and_names_the_lesson(self):
        text = self_review.format_miss(miss())
        self.assertIn("خطأ جديد", text)
        self.assertIn("قلت بيع", text)
        self.assertIn("4130.0", text)
        self.assertIn("لا تكرّر", text)


class FetchWrongTests(unittest.TestCase):
    def test_shapes_the_wrong_list(self):
        import urllib.request

        class Fake:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def read(self_):
                return (b'{"review": {"summary": {"wrong": '
                        b'[{"stance": "LONG", "correct": false}]}}}')

        orig = urllib.request.urlopen
        urllib.request.urlopen = lambda req, timeout=None: Fake()
        try:
            wrong = ma.fetch_wrong("http://example/v1/review")
        finally:
            urllib.request.urlopen = orig
        self.assertEqual(len(wrong), 1)
        self.assertEqual(wrong[0]["stance"], "LONG")

    def test_unreachable_returns_none(self):
        import urllib.request
        orig = urllib.request.urlopen

        def boom(req, timeout=None):
            raise OSError("down")

        urllib.request.urlopen = boom
        try:
            self.assertIsNone(ma.fetch_wrong("http://example/v1/review"))
        finally:
            urllib.request.urlopen = orig

    def test_missing_wrong_key_is_empty_list(self):
        import urllib.request

        class Fake:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def read(self_):
                return b'{"review": {"summary": {}}}'

        orig = urllib.request.urlopen
        urllib.request.urlopen = lambda req, timeout=None: Fake()
        try:
            self.assertEqual(ma.fetch_wrong("http://example/v1/review"), [])
        finally:
            urllib.request.urlopen = orig


if __name__ == "__main__":
    unittest.main()
