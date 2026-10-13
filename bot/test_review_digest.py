"""Tests for the self-review digest: fetch shaping and message, no network."""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import review_digest as rd  # noqa: E402


class FetchReviewTests(unittest.TestCase):
    def test_shapes_payload(self):
        import urllib.request

        class Fake:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def read(self_):
                return b'{"review": {"summary": {"scored": 3}, "message": "hi"}}'

        orig = urllib.request.urlopen
        urllib.request.urlopen = lambda req, timeout=None: Fake()
        try:
            got = rd.fetch_review("http://example/v1/review")
        finally:
            urllib.request.urlopen = orig
        self.assertEqual(got["summary"]["scored"], 3)
        self.assertEqual(got["message"], "hi")

    def test_unreachable_returns_none(self):
        import urllib.request
        orig = urllib.request.urlopen

        def boom(req, timeout=None):
            raise OSError("down")

        urllib.request.urlopen = boom
        try:
            self.assertIsNone(rd.fetch_review("http://example/v1/review"))
        finally:
            urllib.request.urlopen = orig

    def test_missing_review_key_returns_none(self):
        import urllib.request

        class Fake:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def read(self_):
                return b'{"other": 1}'

        orig = urllib.request.urlopen
        urllib.request.urlopen = lambda req, timeout=None: Fake()
        try:
            self.assertIsNone(rd.fetch_review("http://example/v1/review"))
        finally:
            urllib.request.urlopen = orig


if __name__ == "__main__":
    unittest.main()
