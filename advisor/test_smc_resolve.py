"""Tests for the SMC plan resolver (advisor/smc_resolve.py).

These tests pin the *accounting*, not a market view: a plan is a limit order
that must first be reached, the stop wins a tie, the spread is charged on
resolution, an unreached plan EXPIRES and is excluded from the rate, and the
Wilson interval agrees with the tracker's canonical one. Every case is a
hand-built bar series whose answer is known by construction.
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import smc_resolve  # noqa: E402

# The tracker's canonical statistics, imported only to prove the local copies
# agree. The resolver itself never imports from tracker — advisor must stay
# importable on its own. tracker/ has no __init__.py, so its directory is added
# directly.
_ROOT = os.path.dirname(HERE)
_TRACKER = os.path.join(_ROOT, "tracker")
if _TRACKER not in sys.path:
    sys.path.insert(0, _TRACKER)


def bars(rows) -> list:
    """Bars from (open, high, low, close) tuples, one 900s step apart."""
    return [{"time": 1760000000 + i * 900, "open": o, "high": h, "low": l,
             "close": c} for i, (o, h, l, c) in enumerate(rows)]


def long_plan(entry=2000.0, stop=1990.0, targets=None):
    return {"direction": "LONG", "entry": entry, "stop": stop,
            "targets": targets or [2020.0, 2030.0], "confidence": 0.6}


def short_plan(entry=2000.0, stop=2010.0, targets=None):
    return {"direction": "SHORT", "entry": entry, "stop": stop,
            "targets": targets or [1980.0, 1970.0], "confidence": 0.6}


class TriggerTests(unittest.TestCase):
    def test_a_plan_price_never_returns_to_expires(self):
        # Price stays well above the entry: the limit order is never filled.
        series = bars([(2005, 2006, 2004, 2005)] * 40)
        out = smc_resolve.resolve_plan(long_plan(), series)
        self.assertEqual(out["outcome"], "EXPIRED")
        self.assertFalse(out["resolved"])
        self.assertFalse(out["triggered"])
        self.assertIsNone(out["r_net"])
        self.assertIn("never reached", out["reason"])

    def test_the_entry_must_be_reached_before_the_trade_exists(self):
        # Bar 0 fills the entry, bar 1 hits the final target.
        series = bars([(2005, 2005, 1999, 2001), (2001, 2031, 2000, 2030)])
        out = smc_resolve.resolve_plan(long_plan(), series)
        self.assertTrue(out["triggered"])
        self.assertEqual(out["outcome"], "TARGET")
        self.assertEqual(out["bars_waited"], 0)

    def test_pending_bars_bound_the_wait(self):
        # The entry is only reached on bar 3; a 2-bar window must not see it.
        series = bars([(2005, 2006, 2004, 2005)] * 3
                      + [(2005, 2005, 1999, 2001), (2001, 2021, 2000, 2020)])
        out = smc_resolve.resolve_plan(long_plan(), series, pending_bars=2)
        self.assertEqual(out["outcome"], "EXPIRED")


class ResolutionTests(unittest.TestCase):
    def test_a_long_target_is_a_win(self):
        series = bars([(2001, 2002, 1999, 2000), (2000, 2031, 2000, 2030)])
        out = smc_resolve.resolve_plan(long_plan(), series, cost_usd=0.0)
        self.assertEqual(out["outcome"], "TARGET")
        self.assertAlmostEqual(out["r_gross"], 3.0, places=3)
        self.assertAlmostEqual(out["r_net"], 3.0, places=3)

    def test_a_long_stop_is_a_loss(self):
        series = bars([(2001, 2002, 1999, 2000), (2000, 2000, 1989, 1990)])
        out = smc_resolve.resolve_plan(long_plan(), series, cost_usd=0.0)
        self.assertEqual(out["outcome"], "STOP")
        self.assertAlmostEqual(out["r_gross"], -1.0, places=3)

    def test_a_short_target_is_a_win(self):
        series = bars([(1999, 2000, 1998, 2000), (2000, 2000, 1969, 1970)])
        out = smc_resolve.resolve_plan(short_plan(), series, cost_usd=0.0)
        self.assertEqual(out["outcome"], "TARGET")
        self.assertAlmostEqual(out["r_gross"], 3.0, places=3)

    def test_a_short_stop_is_a_loss(self):
        series = bars([(1999, 2000, 1998, 2000), (2000, 2011, 2000, 2010)])
        out = smc_resolve.resolve_plan(short_plan(), series, cost_usd=0.0)
        self.assertEqual(out["outcome"], "STOP")
        self.assertAlmostEqual(out["r_gross"], -1.0, places=3)

    def test_the_stop_wins_when_one_bar_touches_both(self):
        # A single bar spans both the stop and the final target. Intra-bar order
        # is unknowable, so the pessimistic reading (STOP) is the only honest one.
        series = bars([(2001, 2002, 1999, 2000), (2000, 2035, 1989, 2020)])
        out = smc_resolve.resolve_plan(long_plan(), series, cost_usd=0.0)
        self.assertEqual(out["outcome"], "STOP")

    def test_a_tie_on_the_trigger_bar_still_loses(self):
        # The trigger bar itself spans the stop: filled and stopped on one bar.
        series = bars([(2005, 2005, 1989, 1990)])
        out = smc_resolve.resolve_plan(long_plan(), series, cost_usd=0.0)
        self.assertTrue(out["triggered"])
        self.assertEqual(out["outcome"], "STOP")

    def test_neither_level_within_the_horizon_times_out(self):
        series = bars([(2001, 2002, 1999, 2000)] + [(2000, 2005, 1996, 2001)] * 5)
        out = smc_resolve.resolve_plan(long_plan(), series, cost_usd=0.0,
                                       horizon_bars=3)
        self.assertEqual(out["outcome"], "TIMEOUT")
        self.assertTrue(out["resolved"])
        self.assertIsNotNone(out["r_net"])

    def test_targets_reached_counts_the_rungs_touched(self):
        # First target (2020) is touched, then price reverses into the stop.
        series = bars([(2001, 2002, 1999, 2000), (2000, 2021, 2000, 2020),
                       (2020, 2020, 1989, 1990)])
        out = smc_resolve.resolve_plan(long_plan(), series, cost_usd=0.0)
        self.assertEqual(out["outcome"], "STOP")
        self.assertEqual(out["targets_reached"], 1)


class CostTests(unittest.TestCase):
    def test_the_spread_is_charged_on_resolution(self):
        series = bars([(2001, 2002, 1999, 2000), (2000, 2031, 2000, 2030)])
        free = smc_resolve.resolve_plan(long_plan(), series, cost_usd=0.0)
        paid = smc_resolve.resolve_plan(long_plan(), series, cost_usd=2.0)
        self.assertAlmostEqual(free["r_gross"], paid["r_gross"], places=3)
        self.assertLess(paid["r_net"], free["r_net"])
        self.assertAlmostEqual(free["r_net"] - paid["r_net"], 0.2, places=3)

    def test_a_gross_winner_can_be_a_net_loser(self):
        # Risk is 10; a 5-point win is +0.5R gross, and a 6 spread flips it.
        series = bars([(2001, 2002, 1999, 2000), (2000, 2006, 2000, 2005)])
        out = smc_resolve.resolve_plan(
            long_plan(entry=2000.0, stop=1990.0, targets=[2005.0]),
            series, cost_usd=6.0)
        self.assertEqual(out["outcome"], "TARGET")
        self.assertGreater(out["r_gross"], 0.0)
        self.assertLess(out["r_net"], 0.0)


class CausalTests(unittest.TestCase):
    def test_bars_at_or_before_the_timestamp_are_not_used(self):
        series = bars([(2001, 2002, 1999, 2000), (2000, 2021, 2000, 2020)])
        # generated_at is bar 1's time: only bars strictly after it may resolve.
        out = smc_resolve.resolve_plan(long_plan(), series,
                                       generated_at=series[1]["time"])
        self.assertEqual(out["outcome"], "EXPIRED")

    def test_no_forward_bars_is_reported_not_guessed(self):
        out = smc_resolve.resolve_plan(long_plan(), [])
        self.assertEqual(out["outcome"], "EXPIRED")
        self.assertIn("no forward bars", out["reason"])


class BadPlanTests(unittest.TestCase):
    def test_a_bad_direction_is_rejected(self):
        series = bars([(2000, 2001, 1999, 2000)])
        out = smc_resolve.resolve_plan({"direction": "SIDEWAYS", "entry": 2000,
                                        "stop": 1990, "targets": [2010]}, series)
        self.assertFalse(out["resolved"])
        self.assertIn("direction", out["reason"])

    def test_a_zero_risk_plan_is_rejected(self):
        series = bars([(2000, 2001, 1999, 2000)])
        out = smc_resolve.resolve_plan({"direction": "LONG", "entry": 2000,
                                        "stop": 2000, "targets": [2010]}, series)
        self.assertFalse(out["resolved"])
        self.assertIn("risk", out["reason"])

    def test_a_quoted_number_is_not_a_number(self):
        series = bars([(2000, 2001, 1999, 2000)])
        out = smc_resolve.resolve_plan({"direction": "LONG", "entry": "2000",
                                        "stop": 1990, "targets": [2010]}, series)
        self.assertFalse(out["resolved"])

    def test_a_non_dict_plan_is_rejected(self):
        out = smc_resolve.resolve_plan("not a plan", bars([(1, 1, 1, 1)]))
        self.assertFalse(out["resolved"])
        self.assertIn("direction", out["reason"])

    def test_a_scalar_target_is_read_as_one_rung(self):
        series = bars([(2001, 2002, 1999, 2000), (2000, 2031, 2000, 2030)])
        out = smc_resolve.resolve_plan(
            {"direction": "LONG", "entry": 2000, "stop": 1990,
             "targets": 2030.0}, series, cost_usd=0.0)
        self.assertEqual(out["outcome"], "TARGET")

    def test_a_non_list_targets_is_rejected(self):
        series = bars([(2000, 2001, 1999, 2000)])
        out = smc_resolve.resolve_plan({"direction": "LONG", "entry": 2000,
                                        "stop": 1990, "targets": "x"}, series)
        self.assertIn("list", out["reason"])

    def test_a_plan_without_numeric_targets_is_rejected(self):
        series = bars([(2000, 2001, 1999, 2000)])
        out = smc_resolve.resolve_plan({"direction": "LONG", "entry": 2000,
                                        "stop": 1990, "targets": ["a", None]},
                                       series)
        self.assertIn("no numeric targets", out["reason"])


class StatisticsTests(unittest.TestCase):
    def test_only_target_and_stop_are_scored(self):
        rows = [
            {"outcome": "TARGET", "r_net": 2.0, "resolved": True},
            {"outcome": "STOP", "r_net": -1.0, "resolved": True},
            {"outcome": "TIMEOUT", "r_net": 0.3, "resolved": True},
            {"outcome": "EXPIRED", "r_net": None, "resolved": False},
        ]
        s = smc_resolve.summarize(rows, min_scored=1)
        self.assertEqual(s["scored"], 2)
        self.assertEqual(s["wins"], 1)
        self.assertEqual(s["losses"], 1)
        self.assertEqual(s["timeouts"], 1)
        self.assertEqual(s["expired"], 1)
        self.assertAlmostEqual(s["win_rate"], 0.5)

    def test_a_thin_sample_states_no_rate(self):
        rows = [{"outcome": "TARGET", "r_net": 2.0, "resolved": True}]
        s = smc_resolve.summarize(rows, min_scored=30)
        self.assertFalse(s["rate_stated"])
        self.assertEqual(s["scored"], 1)

    def test_the_wilson_matches_the_trackers_canonical_one(self):
        try:
            import validation_gate  # tracker/validation_gate.py
        except ImportError:
            self.skipTest("tracker.validation_gate not importable here")
        for k, n in ((0, 1), (1, 1), (3, 10), (36, 100), (50, 50)):
            with self.subTest(k=k, n=n):
                self.assertEqual(smc_resolve.wilson(k, n),
                                 validation_gate.wilson(k, n))

    def test_the_break_even_matches_the_trackers(self):
        try:
            import validation_gate
        except ImportError:
            self.skipTest("tracker.validation_gate not importable here")
        for win, loss in ((2.0, -1.0), (1.5, -1.0), (3.0, -1.0)):
            with self.subTest(win=win, loss=loss):
                self.assertAlmostEqual(
                    smc_resolve.break_even_win_rate(win, loss),
                    validation_gate.break_even_win_rate(win, loss), places=9)

    def test_the_summary_never_claims_a_measured_edge(self):
        s = smc_resolve.summarize([], min_scored=1)
        self.assertIn("not a promise", s["note"])


class LogTests(unittest.TestCase):
    def test_a_log_record_resolves_every_plan(self):
        series = bars([(2001, 2002, 1999, 2000), (2000, 2021, 2000, 2020)])
        record = {"generated_at_utc": series[0]["time"],
                  "plans": [long_plan(), short_plan(entry=1999.0, stop=2010.0,
                                                    targets=[1990.0])]}
        rows = smc_resolve.resolve_log([record], {"M15": series})
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["generated_at_utc"] == series[0]["time"]
                            for r in rows))


class CliTests(unittest.TestCase):
    """The CLI reads a real plan log and a real bar file. Both are written to a
    temp directory here so the end-to-end path is exercised, not mocked."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="smc_cli_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_load_bars_reads_epoch_ms_and_converts_to_seconds(self):
        path = self._write("bars.csv",
                           "timestamp,open,high,low,close,volume\n"
                           "1760000000000,2000,2010,1990,2005,1.0\n")
        got = smc_resolve._load_bars_csv(path)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["time"], 1760000000)
        self.assertEqual(got[0]["close"], 2005.0)

    def test_load_bars_skips_unreadable_rows(self):
        path = self._write("bars.csv",
                           "timestamp,open,high,low,close,volume\n"
                           "bad,2000,2010,1990,2005,1.0\n"
                           "1760000000000,2000,2010,1990,2005,1.0\n")
        got = smc_resolve._load_bars_csv(path)
        self.assertEqual(len(got), 1)

    def test_load_bars_reads_a_gzip_file(self):
        import gzip
        path = os.path.join(self.tmp, "bars.csv.gz")
        with gzip.open(path, "wt") as fh:
            fh.write("timestamp,open,high,low,close,volume\n"
                     "1760000000000,2000,2010,1990,2005,1.0\n")
        self.assertEqual(len(smc_resolve._load_bars_csv(path)), 1)

    def test_load_log_skips_blank_and_broken_lines(self):
        path = self._write("log.jsonl",
                           '{"generated_at_utc": 1, "plans": []}\n'
                           "\n"
                           "not json\n"
                           '{"generated_at_utc": 2, "plans": []}\n')
        got = smc_resolve._load_log(path)
        self.assertEqual(len(got), 2)

    def test_load_log_missing_file_is_empty_not_an_error(self):
        self.assertEqual(smc_resolve._load_log("/nonexistent/x.jsonl"), [])

    def test_main_resolves_a_real_log_and_prints_a_summary(self):
        import json
        log = self._write("log.jsonl", json.dumps({
            "generated_at_utc": 1760000000,
            "plans": [{"direction": "LONG", "entry": 2000, "stop": 1990,
                       "targets": [2030], "confidence": 0.6}]}) + "\n")
        rows = ["timestamp,open,high,low,close,volume",
                "1760000000000,2000,2005,1999,2001,1.0",
                "1760000900000,2001,2031,2000,2030,1.0"]
        bar_path = self._write("bars.csv", "\n".join(rows) + "\n")
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = smc_resolve.main(["--log", log, "--bars", bar_path,
                                     "--tf", "M15"])
        self.assertEqual(code, 0)
        self.assertIn("SMC plan resolution", buf.getvalue())
        self.assertIn("win_rate", buf.getvalue())

    def test_main_reports_an_empty_log_without_failing(self):
        log = self._write("log.jsonl", "")
        bar_path = self._write("bars.csv",
                               "timestamp,open,high,low,close,volume\n"
                               "1760000000000,2000,2005,1999,2001,1.0\n")
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = smc_resolve.main(["--log", log, "--bars", bar_path])
        self.assertEqual(code, 0)
        self.assertIn("no plan records", buf.getvalue())

    def test_main_fails_when_the_bar_file_is_empty(self):
        import json
        log = self._write("log.jsonl", json.dumps({
            "generated_at_utc": 1, "plans": []}) + "\n")
        bar_path = self._write("bars.csv",
                               "timestamp,open,high,low,close,volume\n")
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = smc_resolve.main(["--log", log, "--bars", bar_path])
        self.assertEqual(code, 1)
        self.assertIn("no bars", buf.getvalue())


class IsolationTests(unittest.TestCase):
    def test_the_resolver_reaches_outside_only_to_the_bars(self):
        # Pure arithmetic over the bars it is handed: no network, no model, and
        # no wall clock (the CLI's imports are local to its own functions).
        with open(os.path.join(HERE, "smc_resolve.py"), encoding="utf-8") as fh:
            source = fh.read()
        head = source.split("# ── CLI")[0]
        for forbidden in ("urllib", "requests", "socket", "time.time",
                          "datetime.now", "import random"):
            self.assertNotIn(forbidden, head,
                             "the resolver must not reach outside the bars")

    def test_the_resolver_does_not_import_the_tracker(self):
        with open(os.path.join(HERE, "smc_resolve.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertNotIn("import validation_gate", source)
        self.assertNotIn("from tracker", source)
        self.assertNotIn("import tracker", source)


if __name__ == "__main__":
    unittest.main()
