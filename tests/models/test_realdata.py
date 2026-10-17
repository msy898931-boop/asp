"""Deterministic tests for the T27 real-data calibration runner.

The corpus here is synthetic (the real corpus gates on E05) but it exercises the
exact real-data code path: load -> label -> causal partition -> calibrated run ->
RULE C verdict. Nothing reaches the network.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from src.models import realdata
from src.models.calibration import ECE_FAILURE, ECE_TARGET
from src.models.features import (
    CROSS_BOUNDS,
    CROSS_DISCRETE,
    PER_TIMEFRAME_BOUNDS,
    PER_TIMEFRAME_DISCRETE,
)
from src.models.splits import SplitError


def _wiggle(seed):
    return (seed % 7) * 0.01


def _tf_values(seed):
    w = _wiggle(seed)
    values = {
        name: (low + high) / 2 + w
        for name, (low, high) in PER_TIMEFRAME_BOUNDS.items()
    }
    for name in PER_TIMEFRAME_DISCRETE:
        values[name] = 0.0
    return values


def _cross_values(seed):
    w = _wiggle(seed)
    values = {
        name: (low + high) / 2 + w
        for name, (low, high) in CROSS_BOUNDS.items()
    }
    for name in CROSS_DISCRETE:
        values[name] = 0.0
    return values


def _row(asof, close, seed=0.0, valid=True):
    return {
        "asOfBarOpenSec": asof,
        "close": close,
        "quality": "VALID",
        "valid": valid,
        "perTimeframe": [
            {
                "timeframe": "M1",
                "asOfBarOpenSec": asof,
                "values": _tf_values(seed),
                "quality": "VALID",
                "valid": True,
            },
            {
                "timeframe": "M15",
                "asOfBarOpenSec": asof,
                "values": _tf_values(seed + 1),
                "quality": "VALID",
                "valid": True,
            },
        ],
        "cross": {
            "asOfBarOpenSec": asof,
            "values": _cross_values(seed + 2),
            "quality": "VALID",
            "valid": True,
            "m15Available": True,
            "h4Available": True,
        },
    }


def _corpus_rows(per_year=30):
    """Weekly instants across 2021..2025 with oscillating closes (both labels)."""
    rows = []
    seed = 0.0
    for year in range(2021, 2026):
        start = datetime(year, 1, 1, tzinfo=timezone.utc)
        for i in range(per_year):
            ts = int((start + timedelta(days=7 * i)).timestamp())
            close = 1800.0 + (10.0 if i % 2 == 0 else -10.0) + seed * 0.1
            rows.append(_row(ts, close, seed))
            seed += 1.0
    return rows


def _single_window_rows(n=200, start=None):
    """M15-close instants inside one rolling window (2026) — no year partitions."""
    rows = []
    start = start or datetime(2026, 6, 24, tzinfo=timezone.utc)
    for i in range(n):
        ts = int((start + timedelta(minutes=15 * i)).timestamp())
        close = 4000.0 + (5.0 if i % 2 == 0 else -5.0)
        rows.append(_row(ts, close, float(i)))
    return rows


def write_corpus(rows, tmpdir, fname="features.json"):
    path = os.path.join(tmpdir, fname)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(rows, handle)
    return path


class RuleCVerdictTest(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(realdata.rule_c_verdict(0.0)[0], realdata.PUBLISH_PROBABILITY)
        self.assertEqual(
            realdata.rule_c_verdict(ECE_TARGET - 0.001)[0], realdata.PUBLISH_PROBABILITY
        )
        self.assertEqual(realdata.rule_c_verdict(ECE_TARGET)[0], realdata.PUBLISH_SCORE)
        self.assertEqual(realdata.rule_c_verdict(ECE_FAILURE)[0], realdata.PUBLISH_SCORE)
        self.assertEqual(
            realdata.rule_c_verdict(ECE_FAILURE + 0.001)[0], realdata.REPORT_AND_PIVOT
        )


class LoadCorpusTest(unittest.TestCase):
    def test_missing_dir(self):
        with self.assertRaises(SplitError):
            realdata.load_corpus("/no/such/dir")

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(SplitError):
            realdata.load_corpus(d)

    def test_missing_close_is_an_error_not_interpolation(self):
        rows = _corpus_rows(per_year=2)
        del rows[0]["close"]
        with tempfile.TemporaryDirectory() as d:
            write_corpus(rows, d)
            with self.assertRaises(SplitError) as ctx:
                realdata.load_corpus(d)
            self.assertIn("no interpolation", str(ctx.exception))

    def test_non_finite_close_rejected(self):
        rows = _corpus_rows(per_year=2)
        rows[0]["close"] = float("inf")
        with tempfile.TemporaryDirectory() as d:
            write_corpus(rows, d)
            with self.assertRaises(SplitError):
                realdata.load_corpus(d)

    def test_duplicate_instant_rejected(self):
        rows = _corpus_rows(per_year=2)
        rows[1]["asOfBarOpenSec"] = rows[0]["asOfBarOpenSec"]
        with tempfile.TemporaryDirectory() as d:
            write_corpus(rows, d)
            with self.assertRaises(SplitError):
                realdata.load_corpus(d)

    def test_unsorted_files_are_ordered(self):
        rows = _corpus_rows(per_year=3)
        with tempfile.TemporaryDirectory() as d:
            # split across two files, inserted out of order
            write_corpus(rows[3:], d, "b.json")
            write_corpus(rows[:3], d, "a.json")
            _, closes = realdata.load_corpus(d)
            self.assertEqual(closes, [r["close"] for r in rows])

    def test_custom_close_key(self):
        rows = _corpus_rows(per_year=2)
        for r in rows:
            r["px"] = r.pop("close")
        with tempfile.TemporaryDirectory() as d:
            write_corpus(rows, d)
            _, closes = realdata.load_corpus(d, close_key="px")
            self.assertEqual(len(closes), len(rows))


class RunRealCalibrationTest(unittest.TestCase):
    def test_end_to_end_shape_and_verdict(self):
        rows = _corpus_rows(per_year=30)
        with tempfile.TemporaryDirectory() as d:
            write_corpus(rows, d)
            report = realdata.run_real_calibration(d)
        self.assertEqual(report.verdict,
                         realdata.rule_c_verdict(report.ece_oos)[0])
        self.assertEqual(sum(report.partition_sizes.values()),
                         report.n_examples)
        self.assertTrue(3 <= len(report.coverage) == 3)
        # RULE D: every tier reported (incl. empty ones)
        self.assertEqual({t.tier for t in report.coverage}, {"low", "medium", "high"})
        # to_dict is JSON-serialisable
        json.dumps(report.to_dict())
        self.assertIn("VERDICT", report.summary())

    def test_empty_validation_partition_cannot_publish(self):
        rows = _corpus_rows(per_year=30)
        with tempfile.TemporaryDirectory() as d:
            write_corpus(rows, d)
            report = realdata.run_real_calibration(
                d, development_years=(2021, 2022, 2023, 2024, 2025),
                validation_years=(1900,), oos_years=(1901,),
            )
        self.assertEqual(report.verdict, realdata.CANNOT_PUBLISH)
        self.assertEqual(report.calibrated_oos, None)

    def test_empty_oos_partition_cannot_publish(self):
        rows = _corpus_rows(per_year=30)
        with tempfile.TemporaryDirectory() as d:
            write_corpus(rows, d)
            report = realdata.run_real_calibration(
                d, development_years=(2021, 2022),
                validation_years=(2023, 2024, 2025), oos_years=(1901,),
            )
        self.assertEqual(report.verdict, realdata.CANNOT_PUBLISH)

    def test_report_is_deterministic(self):
        rows = _corpus_rows(per_year=20)
        with tempfile.TemporaryDirectory() as d:
            write_corpus(rows, d)
            a = realdata.run_real_calibration(d).to_dict()
            b = realdata.run_real_calibration(d).to_dict()
        self.assertEqual(a, b)


class WalkForwardTest(unittest.TestCase):
    def _corpus(self, per_year=40):
        rows = _corpus_rows(per_year=per_year)
        return rows

    def test_walk_forward_runs_and_is_deterministic(self):
        rows = self._corpus(per_year=40)
        with tempfile.TemporaryDirectory() as d:
            write_corpus(rows, d)
            wf = realdata.walk_forward_calibration(
                *realdata.load_corpus(d), train_size=60, test_size=20
            )
        self.assertGreaterEqual(len(wf.folds), 1)
        self.assertFalse(wf.overlap)
        self.assertEqual(wf.n_pooled, sum(f.n_test for f in wf.folds))
        self.assertEqual(wf.verdict, realdata.rule_c_verdict(wf.pooled_ece)[0])
        json.dumps(wf.to_dict())
        with tempfile.TemporaryDirectory() as d:
            write_corpus(rows, d)
            wf2 = realdata.walk_forward_calibration(
                *realdata.load_corpus(d), train_size=60, test_size=20
            )
        self.assertEqual(wf.to_dict(), wf2.to_dict())

    def test_walk_forward_no_leakage(self):
        rows = self._corpus(per_year=40)
        with tempfile.TemporaryDirectory() as d:
            write_corpus(rows, d)
            wf = realdata.walk_forward_calibration(
                *realdata.load_corpus(d), train_size=40, test_size=20
            )
        for f in wf.folds:
            self.assertLess(f.test_start, f.test_end)

    def test_overlapping_step_is_reported(self):
        rows = self._corpus(per_year=40)
        with tempfile.TemporaryDirectory() as d:
            write_corpus(rows, d)
            wf = realdata.walk_forward_calibration(
                *realdata.load_corpus(d), train_size=40, test_size=20, step=5
            )
        self.assertTrue(wf.overlap)
        self.assertIn("overlap", wf.note.lower())

    def test_too_short_corpus_raises(self):
        rows = _corpus_rows(per_year=1)  # 5 instants
        with tempfile.TemporaryDirectory() as d:
            write_corpus(rows, d)
            with self.assertRaises(SplitError):
                realdata.walk_forward_calibration(
                    *realdata.load_corpus(d), train_size=100, test_size=20
                )

    def test_partial_corpus_cannot_publish_but_still_walk_forwards(self):
        """Year-split OOS empty (corpus stops 2023) but walk-forward still runs."""
        rows = _corpus_rows(per_year=40)
        rows = [r for r in rows if datetime.fromtimestamp(
            r["asOfBarOpenSec"], tz=timezone.utc).year <= 2024]
        with tempfile.TemporaryDirectory() as d:
            write_corpus(rows, d)
            report = realdata.run_real_calibration(
                d, walk_forward_train=60, walk_forward_test=20
            )
        self.assertEqual(report.verdict, realdata.CANNOT_PUBLISH)  # no 2025 OOS
        self.assertIsNotNone(report.walk_forward)  # rolling signal still available
        self.assertGreaterEqual(len(report.walk_forward.folds), 1)

    def test_short_corpus_records_walk_forward_unavailable(self):
        rows = _corpus_rows(per_year=1)  # 5 instants
        with tempfile.TemporaryDirectory() as d:
            write_corpus(rows, d)
            report = realdata.run_real_calibration(
                d, walk_forward_train=100, walk_forward_test=20
            )
        self.assertIsNone(report.walk_forward)
        self.assertTrue(any("walk-forward not run" in n for n in report.notes))

    def test_run_real_calibration_includes_walk_forward(self):
        rows = _corpus_rows(per_year=40)
        with tempfile.TemporaryDirectory() as d:
            write_corpus(rows, d)
            report = realdata.run_real_calibration(
                d, walk_forward_train=60, walk_forward_test=20
            )
        self.assertIsNotNone(report.walk_forward)
        self.assertIn("walk-forward", report.summary())
        self.assertIn("walk_forward", report.to_dict())


class SingleWindowPartitionTest(unittest.TestCase):
    def test_auto_falls_back_to_fraction_and_is_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            write_corpus(_single_window_rows(200), d)
            report = realdata.run_real_calibration(
                d, walk_forward_train=40, walk_forward_test=20
            )
        # 2026 is not in 2021-25, so year split cannot apply -> fraction fallback.
        self.assertTrue(
            any("year partition not applicable" in n for n in report.notes)
        )
        self.assertTrue(all(report.partition_sizes[k] > 0 for k in
                            ("development", "validation", "oos")))
        self.assertNotEqual(report.verdict, realdata.CANNOT_PUBLISH)

    def test_explicit_year_mode_still_raises(self):
        with tempfile.TemporaryDirectory() as d:
            write_corpus(_single_window_rows(200), d)
            with self.assertRaises(SplitError):
                realdata.run_real_calibration(d, partition_mode="year")

    def test_explicit_fraction_mode(self):
        with tempfile.TemporaryDirectory() as d:
            write_corpus(_single_window_rows(200), d)
            report = realdata.run_real_calibration(
                d, partition_mode="fraction", development_fraction=0.5,
                validation_fraction=0.25,
            )
        # 200 instants -> 199 labeled examples (horizon drops the last). Fractions
        # are applied to the observed span, so sizes are span-relative, not n*k.
        s = report.partition_sizes
        self.assertEqual(s["development"] + s["validation"] + s["oos"], 199)
        self.assertAlmostEqual(s["development"] / 199, 0.5, delta=0.02)
        self.assertAlmostEqual(s["validation"] / 199, 0.25, delta=0.02)
        self.assertAlmostEqual(s["oos"] / 199, 0.25, delta=0.02)


class GzipCorpusTest(unittest.TestCase):
    """The committed `.json.gz` corpus must load (Lead directive, T27)."""

    def test_loads_single_json_gz_file(self):
        import gzip

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "corpus.json.gz")
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                json.dump(_single_window_rows(50), handle)
            fs, closes = realdata.load_corpus(path)
        self.assertEqual(len(fs), 50)
        self.assertEqual(len(closes), 50)

    def test_directory_with_multiple_corpora_is_refused(self):
        """Agent-D F(2): a dir holding >1 corpus doc must not silently mix."""
        import gzip

        with tempfile.TemporaryDirectory() as d:
            write_corpus(_single_window_rows(20), d, "a.json")
            with gzip.open(os.path.join(d, "b.json.gz"), "wt", encoding="utf-8") as h:
                json.dump(_single_window_rows(10), h)
            with self.assertRaises(realdata.SplitError):
                realdata.load_corpus(d)

    def test_directory_with_single_corpus_still_loads(self):
        import gzip

        with tempfile.TemporaryDirectory() as d:
            with gzip.open(os.path.join(d, "only.json.gz"), "wt", encoding="utf-8") as h:
                json.dump(_single_window_rows(15), h)
            fs, _ = realdata.load_corpus(d)
        self.assertEqual(len(fs), 15)

    def test_missing_path_is_an_error(self):
        with self.assertRaises(realdata.SplitError):
            realdata.load_corpus("/no/such/file.json.gz")


class L2KnobTest(unittest.TestCase):
    def test_factory_fits_and_converges(self):
        import random

        rng = random.Random(0)
        rows = [
            [rng.gauss(0, 1) for _ in range(20)] for _ in range(40)
        ]
        y = [1 if sum(r) > 0 else 0 for r in rows]
        factory = realdata._logistic_factory(0.1)
        model = factory(rows, y)
        self.assertTrue(model.converged)
        self.assertLessEqual(model.iterations, 60)

    def test_runner_accepts_l2(self):
        with tempfile.TemporaryDirectory() as d:
            write_corpus(_single_window_rows(120), d)
            report = realdata.run_real_calibration(
                d, partition_mode="fraction", l2=0.05,
            )
        self.assertNotEqual(report.verdict, realdata.CANNOT_PUBLISH)

    def test_cli_accepts_l2(self):
        with tempfile.TemporaryDirectory() as d:
            write_corpus(_single_window_rows(120), d)
            rc = realdata.main([
                "--corpus", d, "--partition-mode", "fraction", "--l2", "0.05",
                "--out", os.path.join(d, "r.json"),
            ])
            self.assertEqual(rc, 0)
            with open(os.path.join(d, "r.json"), encoding="utf-8") as h:
                self.assertIn("verdict", json.load(h))


class ResolveCorpusDirTest(unittest.TestCase):
    def test_explicit_wins(self):
        self.assertEqual(
            realdata.resolve_corpus_dir("/tmp/xyz", environ={"AURA_FEATURES_DIR": "/tmp/a"}),
            os.path.abspath("/tmp/xyz"),
        )

    def test_aura_is_canonical_over_alias(self):
        got = realdata.resolve_corpus_dir(
            "", environ={"AURA_FEATURES_DIR": "/tmp/a", "ASTRA_FEATURE_CORPUS": "/tmp/b"}
        )
        self.assertEqual(got, os.path.abspath("/tmp/a"))

    def test_alias_honoured_when_canonical_absent(self):
        got = realdata.resolve_corpus_dir("", environ={"ASTRA_FEATURE_CORPUS": "/tmp/b"})
        self.assertEqual(got, os.path.abspath("/tmp/b"))

    def test_default_is_repo_relative(self):
        self.assertEqual(realdata.resolve_corpus_dir("", environ={}), realdata.DEFAULT_FEATURES_DIR)


class CliTest(unittest.TestCase):
    def test_no_corpus_configured_exits_2(self):
        rc = realdata.main(["--corpus", "/no/such/corpus/xyz"])
        self.assertEqual(rc, 2)

    def test_runs_and_writes_report(self):
        rows = _corpus_rows(per_year=20)
        with tempfile.TemporaryDirectory() as d:
            write_corpus(rows, d)
            out = os.path.join(d, "report.json")
            rc = realdata.main(
                ["--corpus", d, "--out", out, "--wf-train", "40", "--wf-test", "20"]
            )
            self.assertEqual(rc, 0)
            with open(out, "r", encoding="utf-8") as handle:
                doc = json.load(handle)
            self.assertIn(doc["verdict"], {
                realdata.PUBLISH_PROBABILITY, realdata.PUBLISH_SCORE,
                realdata.REPORT_AND_PIVOT, realdata.CANNOT_PUBLISH,
            })


class ValidFilterTest(unittest.TestCase):
    """F-T27-1: INCOMPLETE warm-up sets must be disclosed, and filterable."""

    def _mixed_corpus(self, n=200, warmup=70):
        rows = []
        start = datetime(2026, 6, 24, tzinfo=timezone.utc)
        for i in range(n):
            ts = int((start + timedelta(minutes=15 * i)).timestamp())
            close = 4000.0 + (5.0 if i % 2 == 0 else -5.0)
            rows.append(_row(ts, close, float(i), valid=(i >= warmup)))
        return rows

    def test_counts_are_recorded_full_run(self):
        with tempfile.TemporaryDirectory() as d:
            write_corpus(self._mixed_corpus(), d)
            rep = realdata.run_real_calibration(
                d, partition_mode="fraction", l2=1e-6, walk_forward_train=0,
            )
            self.assertEqual(rep.n_valid_sets, 130)
            self.assertEqual(rep.n_invalid_sets, 70)
            self.assertFalse(rep.valid_only)
            self.assertTrue(any("INCOMPLETE" in n for n in rep.notes))
            self.assertEqual(rep.to_dict()["n_invalid_sets"], 70)

    def test_valid_only_drops_incomplete_rows(self):
        with tempfile.TemporaryDirectory() as d:
            write_corpus(self._mixed_corpus(), d)
            rep = realdata.run_real_calibration(
                d, partition_mode="fraction", l2=1e-6, walk_forward_train=0,
                valid_filter=True,
            )
            self.assertEqual(rep.n_valid_sets, 130)
            self.assertEqual(rep.n_invalid_sets, 70)
            self.assertTrue(rep.valid_only)
            self.assertEqual(rep.n_instants, 130)
            self.assertTrue(any("dropped by valid_filter" in n for n in rep.notes))

    def test_cli_valid_only_flag(self):
        with tempfile.TemporaryDirectory() as d:
            write_corpus(self._mixed_corpus(), d)
            out = os.path.join(d, "report.json")
            rc = realdata.main(["--corpus", d, "--valid-only", "--out", out])
            self.assertEqual(rc, 0)
            with open(out, "r", encoding="utf-8") as handle:
                doc = json.load(handle)
            self.assertTrue(doc["valid_only"])
            self.assertEqual(doc["n_instants"], 130)


if __name__ == "__main__":
    unittest.main()
