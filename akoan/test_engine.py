#!/usr/bin/env python3
"""Tests for the akoan engine and its report.

The engine's contract is that it defaults to "no edge" and only ever says
otherwise when the data clears every bar. These tests hold it to that: thin
data yields no rate, one-sided dimensions are called out as untestable, and a
finding must survive FDR correction before it is labelled.

Run: python3 -m unittest discover -s akoan -p "test_*.py"
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import engine as eng  # noqa: E402
import report as rep  # noqa: E402


def row(win, **kw):
    base = {"win": win, "r_net": 1.6 if win else -1.0, "label":
            "WIN" if win else "LOSS"}
    base.update(kw)
    return base


def noisy(n, key, value, wins):
    return [row(i < wins, **{key: value}) for i in range(n)]


class EngineTests(unittest.TestCase):
    def test_empty_input_is_insufficient(self):
        result = eng.analyse([])
        self.assertEqual(result["verdict"], "INSUFFICIENT_DATA")
        self.assertEqual(result["scored"], 0)

    def test_thin_data_states_no_rate(self):
        rows = [row(True, blackout="quiet"), row(False, blackout="quiet")]
        dims = eng.build_dimensions(rows, min_n=8)
        bucket = dims[0].buckets[0]
        self.assertIsNone(bucket.win_rate)
        self.assertIn("insufficient_data", bucket.note)

    def test_one_sided_dimension_is_not_comparable(self):
        rows = noisy(20, "blackout", "quiet", 6)
        dims = {d.name: d for d in eng.build_dimensions(rows, min_n=8)}
        self.assertFalse(dims["blackout"].comparable)

    def test_two_sided_dimension_is_comparable(self):
        rows = noisy(20, "blackout", "quiet", 6) + noisy(20, "blackout", "blackout", 6)
        dims = {d.name: d for d in eng.build_dimensions(rows, min_n=8)}
        self.assertTrue(dims["blackout"].comparable)

    def test_no_difference_yields_no_edge_measured(self):
        rows = noisy(40, "blackout", "quiet", 14) + noisy(40, "blackout", "blackout", 14)
        result = eng.analyse(rows)
        self.assertEqual(result["verdict"], "NO_EDGE_MEASURED")

    def test_a_real_split_is_only_a_candidate_after_correction(self):
        # 2/20 vs 18/20 is a large, clean difference.
        rows = noisy(20, "blackout", "quiet", 2) + noisy(20, "blackout", "blackout", 18)
        result = eng.analyse(rows)
        self.assertEqual(result["verdict"], "EDGE_CANDIDATE")
        self.assertGreaterEqual(result["survivors"], 1)

    def test_fdr_clears_marginal_results(self):
        # Many near-identical buckets: none should survive correction.
        rows = []
        for i in range(12):
            rows += noisy(10, "alignment", "v%02d" % i, 5)
        result = eng.analyse(rows)
        self.assertEqual(result["survivors"], 0)

    def test_base_rate_is_reported_so_every_comparison_has_one(self):
        rows = noisy(30, "blackout", "quiet", 9) + noisy(30, "blackout", "blackout", 9)
        result = eng.analyse(rows)
        self.assertAlmostEqual(result["base_rate"], 0.3, places=4)

    def test_result_names_the_engine_and_the_question(self):
        result = eng.analyse([])
        self.assertEqual(result["engine"], "akoan")
        self.assertIn("الخبر", result["question"])

    def test_timeouts_are_not_scored(self):
        rows = noisy(10, "blackout", "quiet", 4) + [row(None, blackout="quiet")]
        result = eng.analyse(rows)
        self.assertEqual(result["scored"], 10)
        self.assertEqual(result["unscored"], 1)


class ReportTests(unittest.TestCase):
    def test_report_names_its_baseline(self):
        rows = noisy(20, "blackout", "quiet", 6) + noisy(20, "blackout", "blackout", 6)
        text = rep.format_report(eng.analyse(rows))
        self.assertIn("خط الأساس", text)

    def test_report_says_when_a_dimension_cannot_be_tested(self):
        rows = noisy(20, "blackout", "quiet", 6)
        text = rep.format_report(eng.analyse(rows))
        self.assertIn("ما بينقاس", text)

    def test_report_always_disclaims(self):
        text = rep.format_report(eng.analyse([]))
        self.assertIn("ليس نصيحة مالية", text)

    def test_report_states_coverage_when_given(self):
        cov = {"events": 10, "released": 8, "span_days": 30}
        text = rep.format_report(eng.analyse([]), cov)
        self.assertIn("تغطية", text)
        self.assertIn("جزئية", text)

    def test_compact_keeps_the_verdict_and_counts(self):
        rows = noisy(20, "blackout", "quiet", 6) + noisy(20, "blackout", "blackout", 6)
        small = rep.compact(eng.analyse(rows))
        self.assertEqual(small["engine"], "akoan")
        self.assertIn("verdict", small)


if __name__ == "__main__":
    unittest.main()
