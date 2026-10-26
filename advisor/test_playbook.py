"""Tests for the trading playbook (advisor/playbook.py).

The playbook is description, not endorsement, so these tests pin the properties
that keep it honest: every school carries a measured record, no school claims to
clear the gate when the research rejected it, and the matcher never raises on a
partial context row.
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import playbook  # noqa: E402


class CatalogueTest(unittest.TestCase):
    def test_every_school_has_a_unique_key_and_a_thesis(self):
        keys = [s.key for s in playbook.SCHOOLS]
        self.assertEqual(len(keys), len(set(keys)))
        for s in playbook.SCHOOLS:
            with self.subTest(school=s.key):
                self.assertTrue(s.name)
                self.assertTrue(s.school)
                self.assertTrue(s.thesis)

    def test_every_school_carries_a_measured_record(self):
        for s in playbook.SCHOOLS:
            with self.subTest(school=s.key):
                self.assertIsNotNone(s.measured)
                for field in ("n", "win_pct", "r_per_trade", "clears_gate",
                              "why_not"):
                    self.assertIn(field, s.measured)

    def test_no_school_falsely_claims_to_clear_the_gate(self):
        # The project's research rejected every school. If a school ever sets
        # clears_gate True it must be because a re-run actually passed — and
        # then it must carry a positive R on both halves, not just in-sample.
        for s in playbook.SCHOOLS:
            if s.measured and s.measured.get("clears_gate"):
                with self.subTest(school=s.key):
                    self.assertGreater(s.measured["r_per_trade"], 0)
                    self.assertGreater(s.measured.get("oos_r_per_trade", 0), 0)
                    self.assertFalse(s.measured.get("why_not"))

    def test_the_measured_table_is_reported_verbatim(self):
        # The catalogue exposes the numbers the research produced, unchanged.
        cat = {s["key"]: s for s in playbook.catalogue()}
        self.assertEqual(cat["trend_following"]["measured"]["n"], 3210)
        self.assertEqual(cat["news_trend_confluence"]["measured"]["win_pct"], 43.8)
        self.assertFalse(cat["news_trend_confluence"]["clears_gate"])

    def test_the_baseline_is_recorded(self):
        self.assertEqual(playbook.BASELINE["n"], 7084)
        self.assertLess(playbook.BASELINE["r_per_trade"], 0)

    def test_every_measured_block_cites_its_source(self):
        for s in playbook.SCHOOLS:
            with self.subTest(school=s.key):
                self.assertIn("source", s.measured)
                self.assertIn("scope", s.measured)

    def test_the_numbers_match_the_research_reports(self):
        # The catalogue quotes the research; this re-reads the reports and fails
        # the moment a number drifts, so the two cannot silently diverge.
        reports = os.path.join(os.path.dirname(HERE), "research", "features_real")
        with open(os.path.join(reports, "rule_report.txt"), encoding="utf-8") as fh:
            rule = fh.read()
        with open(os.path.join(reports, "study_report.txt"), encoding="utf-8") as fh:
            study = fh.read()

        def block(text: str, marker: str) -> str:
            start = text.index(marker)
            # Skip the rule's own underline, then stop at the next separator.
            sep = text.find("\n====", start)
            end = text.find("\n====", sep + 5)
            return text[start:end if end != -1 else len(text)]

        # Rule report slices (all-years rows).
        tf = block(rule, "RULE: H4-aligned only")
        self.assertIn("n=3210 win=36.9%", tf)
        self.assertIn("R/trade=-0.062", tf)
        mom = block(rule, "RULE: post-news only")
        self.assertIn("n=409 win=40.1%", mom)
        self.assertIn("R/trade=+0.056", mom)
        conf = block(rule, "RULE: THE RULE:")
        self.assertIn("n=208 win=43.8%", conf)
        self.assertIn("R/trade=+0.117", conf)

        # Study report slices (in-sample rows).
        ct = block(study, "[fail] H4 against")
        self.assertIn("IS : n=1460 win= 35.0%", ct)
        reg = block(study, "[fail] regime: TRENDING_UP")
        self.assertIn("n=650  win= 33.4%", reg)

        cat = {s["key"]: s["measured"] for s in playbook.catalogue()}
        self.assertEqual(cat["trend_following"]["n"], 3210)
        self.assertEqual(cat["news_momentum"]["n"], 409)
        self.assertEqual(cat["news_trend_confluence"]["r_per_trade"], 0.117)
        self.assertEqual(cat["counter_trend"]["n"], 1460)
        # TRENDING_UP + TRENDING_DOWN, the in-sample halves the study reports.
        self.assertEqual(cat["trend_regime"]["n"], 650 + 656)


class MatcherTest(unittest.TestCase):
    def test_an_h4_aligned_row_matches_trend_following(self):
        hits = playbook.match({"direction": "LONG", "h4_align": "ALIGNED"})
        self.assertIn("trend_following", [s.key for s in hits])

    def test_an_h4_against_row_matches_counter_trend(self):
        hits = playbook.match({"direction": "LONG", "h4_align": "AGAINST"})
        self.assertEqual([s.key for s in hits], ["counter_trend"])

    def test_the_most_specific_school_wins(self):
        # Aligned AND post-release: the broad trend school and the narrow
        # confluence school both fire; primary must pick the narrow one.
        row = {"direction": "LONG", "h4_align": "ALIGNED", "recent_release": True}
        self.assertEqual(playbook.primary(row).key, "news_trend_confluence")

    def test_a_bare_row_matches_nothing(self):
        self.assertEqual(playbook.match({}), [])
        self.assertIsNone(playbook.primary({}))

    def test_a_partial_row_never_raises(self):
        # A missing field, a None and a non-numeric rsi are all tolerated.
        for row in ({"direction": None}, {"rsi": None, "h4_align": None},
                    {"regime": 5, "h4_align": "aligned"}, {"rsi": "abc"}):
            with self.subTest(row=row):
                playbook.match(row)  # must not raise

    def test_by_key_round_trips(self):
        for s in playbook.SCHOOLS:
            self.assertIs(playbook.by_key(s.key), s)
        self.assertIsNone(playbook.by_key("does-not-exist"))


if __name__ == "__main__":
    unittest.main()
