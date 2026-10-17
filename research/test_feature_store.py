"""Tests for the feature-store builder and the study scripts.

The heavy work (a full replay over the corpus) is exercised end to end in CI by
running the scripts; these tests pin the parts that are easy to get subtly
wrong: the resampler's bucketing, the store's row shape, and the study's
candidate predicates.

Run: python3 -m unittest discover -s research -p "test_*.py"
"""

from __future__ import annotations

import gzip
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FEATURES_REAL = os.path.join(HERE, "features_real")
REPO = os.path.dirname(HERE)
for extra in (os.path.join(REPO, "tracker"), FEATURES_REAL):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import build_store  # noqa: E402
import study  # noqa: E402

import features  # noqa: E402


def write_m1_csv(path: str, start: int, count: int, step: int = 60) -> None:
    """A tiny Dukascopy-shaped M1 CSV: header + timestamp,o,h,l,c,volume."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("timestamp,open,high,low,close,volume\n")
        price = 2000.0
        for i in range(count):
            t = start + i * step
            o = price
            c = price + 0.5
            fh.write("%d,%.2f,%.2f,%.2f,%.2f,1.0\n"
                     % (t, o, max(o, c) + 0.1, min(o, c) - 0.1, c))
            price = c


class ResampleTests(unittest.TestCase):
    def test_m1_bars_fold_into_m15_buckets(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m1.csv")
            # Start on an M15 boundary so 15 one-minute bars form one bucket.
            write_m1_csv(path, start=1_699_948_800, count=15)
            bars = build_store.resample(path, 900)
            self.assertEqual(len(bars), 1)
            self.assertEqual(bars[0]["time"], 1_699_948_800)
            self.assertEqual(bars[0]["open"], 2000.0)

    def test_gzip_input_is_read(self):
        with tempfile.TemporaryDirectory() as d:
            raw = os.path.join(d, "m1.csv")
            write_m1_csv(raw, start=1_699_948_800, count=30)
            gz = raw + ".gz"
            with open(raw, "rb") as src, gzip.open(gz, "wb") as dst:
                dst.write(src.read())
            bars = build_store.resample(gz, 900)
            self.assertEqual(len(bars), 2)

    def test_larger_tf_yields_fewer_bars(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m1.csv")
            write_m1_csv(path, start=1_699_948_800, count=240)
            self.assertGreater(len(build_store.resample(path, 900)),
                               len(build_store.resample(path, 3600)))


class StoreRowTests(unittest.TestCase):
    def test_rows_for_signals_skips_open_trades(self):
        bars = [{"time": 1_699_948_800 + i * 900, "open": 1.0, "high": 1.1,
                 "low": 0.9, "close": 1.0} for i in range(3)]

        class S:
            def __init__(self, resolved):
                self.index = 0
                self.direction = "LONG"
                self.entry = 1.0
                self.stop = 0.9
                self.time = bars[0]["time"]
                self.regime = "QUIET"
                self.bias = "BULLISH"
                self.rsi = 55.0
                self.resolved = resolved
                self.outcome = "TARGET"
                self.r_net = 1.5

        rows = features.rows_for_signals(bars, [S(True), S(False)])
        self.assertEqual(len(rows), 1)


class StudyCandidateTests(unittest.TestCase):
    def test_candidates_have_unique_names(self):
        names = [n for n, _ in study.candidates()]
        self.assertEqual(len(names), len(set(names)))

    def test_h4_aligned_predicate(self):
        pred = dict(study.candidates())["H4 aligned"]
        self.assertTrue(pred({"h4_align": "ALIGNED"}))
        self.assertFalse(pred({"h4_align": "AGAINST"}))

    def test_median_handles_even_and_odd(self):
        self.assertEqual(study.median([1, 3]), 2.0)
        self.assertEqual(study.median([1, 2, 3]), 2)
        self.assertEqual(study.median([]), 0.0)


if __name__ == "__main__":
    unittest.main()
