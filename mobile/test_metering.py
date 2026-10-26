#!/usr/bin/env python3
"""Tests for usage metering (mobile/metering.py) and its wiring into the gate.

The meter is a billing boundary, so these run the real store against real temp
files and drive the real server over HTTP with accounts and metering enabled —
no mocks. The properties that matter: a per-account daily count, a refusal once
the allowance is spent, an unlimited tier that never writes, a new day that
resets, and the meter reported (but not moved) by /me.json.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import metering as meter  # noqa: E402
import serve_mobile as sm  # noqa: E402
from mobile_test_kit import _serve, _stop, _StubBridge  # noqa: E402

DAY = 86400


class MeteringStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "metering.json"
        self.store = meter.MeteringStore(self.path)
        self._saved = {k: os.environ.get(k) for k in
                       ("ASTRA_FREE_DAILY", "ASTRA_PRO_DAILY")}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def test_a_free_account_is_counted_up_to_its_limit(self):
        os.environ["ASTRA_FREE_DAILY"] = "3"
        for i in range(3):
            d = self.store.spend("acc_1", meter.TIER_FREE)
            self.assertTrue(d.allowed, "request %d should be allowed" % i)
            self.assertEqual(d.used, i + 1)
        refused = self.store.spend("acc_1", meter.TIER_FREE)
        self.assertFalse(refused.allowed)
        self.assertEqual(refused.used, 3)
        self.assertEqual(refused.remaining, 0)

    def test_a_refused_request_does_not_deepen_the_hole(self):
        os.environ["ASTRA_FREE_DAILY"] = "1"
        self.store.spend("acc_1", meter.TIER_FREE)
        self.store.spend("acc_1", meter.TIER_FREE)
        # The counter must not have grown past the limit on the refusal.
        self.assertEqual(self.store.peek("acc_1", meter.TIER_FREE).used, 1)

    def test_the_count_survives_a_reload(self):
        os.environ["ASTRA_FREE_DAILY"] = "5"
        self.store.spend("acc_1", meter.TIER_FREE)
        self.store.spend("acc_1", meter.TIER_FREE)
        reopened = meter.MeteringStore(self.path)
        self.assertEqual(reopened.peek("acc_1", meter.TIER_FREE).used, 2)

    def test_a_new_day_resets_the_counter(self):
        os.environ["ASTRA_FREE_DAILY"] = "2"
        t0 = 1_800_000_000.0
        self.store.spend("acc_1", meter.TIER_FREE, now=t0)
        self.store.spend("acc_1", meter.TIER_FREE, now=t0)
        self.assertFalse(self.store.spend("acc_1", meter.TIER_FREE, now=t0).allowed)
        # A day later, the same account has its full allowance back.
        later = t0 + DAY
        d = self.store.spend("acc_1", meter.TIER_FREE, now=later)
        self.assertTrue(d.allowed)
        self.assertEqual(d.used, 1)

    def test_an_unlimited_tier_is_never_counted_and_never_writes(self):
        os.environ["ASTRA_PRO_DAILY"] = "0"
        d = self.store.spend("acc_pro", meter.TIER_PRO)
        self.assertTrue(d.allowed)
        self.assertIsNone(d.remaining)
        self.assertEqual(d.limit, 0)
        # Nothing was written: an unlimited tier must not touch the disk.
        self.assertFalse(self.path.exists())

    def test_two_accounts_do_not_share_a_counter(self):
        os.environ["ASTRA_FREE_DAILY"] = "1"
        self.assertTrue(self.store.spend("acc_a", meter.TIER_FREE).allowed)
        self.assertTrue(self.store.spend("acc_b", meter.TIER_FREE).allowed)
        self.assertFalse(self.store.spend("acc_a", meter.TIER_FREE).allowed)

    def test_peek_does_not_spend(self):
        os.environ["ASTRA_FREE_DAILY"] = "5"
        self.store.spend("acc_1", meter.TIER_FREE)
        before = self.store.peek("acc_1", meter.TIER_FREE).used
        self.store.peek("acc_1", meter.TIER_FREE)
        self.assertEqual(self.store.peek("acc_1", meter.TIER_FREE).used, before)

    def test_reset_clears_one_account(self):
        os.environ["ASTRA_FREE_DAILY"] = "1"
        self.store.spend("acc_1", meter.TIER_FREE)
        self.store.spend("acc_2", meter.TIER_FREE)
        self.assertTrue(self.store.reset("acc_1"))
        self.assertEqual(self.store.peek("acc_1", meter.TIER_FREE).used, 0)
        self.assertEqual(self.store.peek("acc_2", meter.TIER_FREE).used, 1)

    def test_reset_at_is_the_next_utc_midnight(self):
        os.environ["ASTRA_FREE_DAILY"] = "5"
        t0 = 1_800_000_000.0
        d = self.store.spend("acc_1", meter.TIER_FREE, now=t0)
        self.assertEqual(d.reset_at % DAY, 0)
        self.assertGreater(d.reset_at, t0)
        self.assertLessEqual(d.reset_at - t0, DAY)

    def test_a_corrupt_store_reads_as_empty_not_as_a_crash(self):
        self.path.write_text("{not json", "utf-8")
        reopened = meter.MeteringStore(self.path)
        os.environ["ASTRA_FREE_DAILY"] = "1"
        self.assertTrue(reopened.spend("acc_1", meter.TIER_FREE).allowed)

    def test_the_counter_is_keyed_by_account_id_not_by_key(self):
        # The meter is keyed by the opaque account id, never by the secret, so a
        # leaked metering file hands over no credential.
        os.environ["ASTRA_FREE_DAILY"] = "5"
        self.store.spend("acc_1", meter.TIER_FREE)
        raw = self.path.read_text("utf-8")
        self.assertIn("acc_1", raw)
        # The only stored fields are the day and the count.
        record = json.loads(raw)["counts"]["acc_1"]
        self.assertEqual(set(record), {"day", "count"})


class MeteringEnabledTest(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("ASTRA_METERING", "ASTRA_METERING_FILE",
                        "ASTRA_RUN_DIR")}
        meter.reset_store()

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        meter.reset_store()

    def test_metering_is_off_by_default(self):
        os.environ.pop("ASTRA_METERING", None)
        self.assertFalse(meter.enabled())

    def test_the_flag_turns_it_on(self):
        for flag in ("1", "true", "yes", "on", "ON"):
            os.environ["ASTRA_METERING"] = flag
            self.assertTrue(meter.enabled(), flag)

    def test_an_unset_or_false_flag_is_off(self):
        for flag in ("", "0", "no", "off", "maybe"):
            os.environ["ASTRA_METERING"] = flag
            self.assertFalse(meter.enabled(), flag)

    def test_path_comes_from_env(self):
        os.environ["ASTRA_METERING_FILE"] = "/tmp/custom-metering.json"
        self.assertEqual(str(meter.metering_path()), "/tmp/custom-metering.json")

    def test_path_defaults_under_run_dir(self):
        os.environ.pop("ASTRA_METERING_FILE", None)
        os.environ["ASTRA_RUN_DIR"] = "/tmp/astra-run"
        self.assertEqual(str(meter.metering_path()),
                         "/tmp/astra-run/metering.json")


class QuotaTuningTest(unittest.TestCase):
    """The tier allowances are env-tunable and an invalid value is ignored."""

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("ASTRA_FREE_DAILY", "ASTRA_PRO_DAILY")}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_defaults_are_used_when_unset(self):
        os.environ.pop("ASTRA_FREE_DAILY", None)
        os.environ.pop("ASTRA_PRO_DAILY", None)
        self.assertEqual(meter.quota_for(meter.TIER_FREE),
                         meter.DEFAULT_FREE_DAILY)
        self.assertEqual(meter.quota_for(meter.TIER_PRO),
                         meter.DEFAULT_PRO_DAILY)

    def test_env_overrides_the_defaults(self):
        os.environ["ASTRA_FREE_DAILY"] = "7"
        os.environ["ASTRA_PRO_DAILY"] = "0"
        self.assertEqual(meter.quota_for(meter.TIER_FREE), 7)
        self.assertEqual(meter.quota_for(meter.TIER_PRO), 0)

    def test_a_non_numeric_value_falls_back_to_the_default(self):
        os.environ["ASTRA_FREE_DAILY"] = "lots"
        self.assertEqual(meter.quota_for(meter.TIER_FREE),
                         meter.DEFAULT_FREE_DAILY)

    def test_a_negative_value_is_clamped_to_zero(self):
        os.environ["ASTRA_FREE_DAILY"] = "-5"
        self.assertEqual(meter.quota_for(meter.TIER_FREE), 0)


class CliTest(unittest.TestCase):
    """The operator CLI is how counters are inspected and cleared."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.file = str(Path(self._tmp.name) / "m.json")
        self._saved = os.environ.get("ASTRA_FREE_DAILY")
        os.environ["ASTRA_FREE_DAILY"] = "3"

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("ASTRA_FREE_DAILY", None)
        else:
            os.environ["ASTRA_FREE_DAILY"] = self._saved
        self._tmp.cleanup()

    def _run(self, *argv):
        return meter.main(["--file", self.file, *argv])

    def test_show_runs_clean_on_an_empty_store(self):
        self.assertEqual(self._run("show"), 0)

    def test_reset_one_then_all(self):
        meter.MeteringStore(Path(self.file)).spend("acc_1", meter.TIER_FREE)
        meter.MeteringStore(Path(self.file)).spend("acc_2", meter.TIER_FREE)
        self.assertEqual(self._run("reset", "--id", "acc_1"), 0)
        # A second reset of the same id finds nothing and reports it.
        self.assertEqual(self._run("reset", "--id", "acc_1"), 1)
        self.assertEqual(self._run("reset"), 0)
        self.assertEqual(
            meter.MeteringStore(Path(self.file)).peek("acc_2",
                                                      meter.TIER_FREE).used, 0)


class FailOpenTest(unittest.TestCase):
    """A meter that cannot write must allow the request, not lock the user out.

    The meter is a billing convenience, not the access gate; a disk hiccup must
    never deny a paying user. This is the deliberate opposite of a security
    control, so it is pinned explicitly.
    """

    def test_an_unwritable_path_still_allows_the_request(self):
        # A directory where the file should be makes every write fail.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metering.json"
            path.mkdir()
            store = meter.MeteringStore(path)
            os.environ["ASTRA_FREE_DAILY"] = "1"
            try:
                decision = store.spend("acc_1", meter.TIER_FREE)
                self.assertTrue(decision.allowed)
            finally:
                os.environ.pop("ASTRA_FREE_DAILY", None)


class MeteredRoutesTest(unittest.TestCase):
    """The meter bites on the model-backed routes and nowhere else.

    Drives the real server with accounts and metering on, against real temp
    stores and a stub upstream, so the whole path — key, gate, meter, proxy —
    runs for real.
    """

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("ASTRA_ACCOUNTS", "ASTRA_ACCOUNTS_FILE",
                        "ASTRA_METERING", "ASTRA_METERING_FILE",
                        "ASTRA_ACCESS_TOKEN", "ASTRA_FREE_DAILY")}
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["ASTRA_ACCOUNTS_FILE"] = os.path.join(self._tmp.name,
                                                         "accounts.json")
        os.environ["ASTRA_METERING_FILE"] = os.path.join(self._tmp.name,
                                                         "metering.json")
        os.environ["ASTRA_ACCOUNTS"] = "1"
        os.environ["ASTRA_METERING"] = "1"
        os.environ["ASTRA_FREE_DAILY"] = "2"
        sm.ACCESS_TOKEN = ""
        sm._GATE_LIMITER.reset()
        import accounts
        accounts.reset_store()
        meter.reset_store()
        self._accounts = accounts
        self._upstream = _serve(_StubBridge)
        self._saved_bases = (sm.Handler.engine_base, sm.Handler.advisor_base,
                             sm.Handler.smc_base)
        base = "http://127.0.0.1:%d" % self._upstream.server_address[1]
        sm.Handler.engine_base = base
        sm.Handler.advisor_base = base
        sm.Handler.smc_base = base

    def tearDown(self):
        (sm.Handler.engine_base, sm.Handler.advisor_base,
         sm.Handler.smc_base) = self._saved_bases
        _stop(self._upstream)
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        sm.ACCESS_TOKEN = ""
        sm._GATE_LIMITER.reset()
        self._accounts.reset_store()
        meter.reset_store()
        self._tmp.cleanup()

    def _status(self, path):
        app = _serve(sm.Handler)
        try:
            url = "http://127.0.0.1:%d%s" % (app.server_address[1], path)
            try:
                with urllib.request.urlopen(url, timeout=10) as resp:
                    return resp.status, resp.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read().decode("utf-8")
        finally:
            _stop(app)

    def _free_account(self):
        return self._accounts.store().create(email="a@b.c",
                                             tier=self._accounts.TIER_FREE)

    def test_a_metered_route_is_allowed_until_the_quota_is_spent(self):
        created = self._free_account()
        for _ in range(2):
            status, _ = self._status("/engine/v1/engine?k=" + created.key)
            self.assertEqual(status, 200)
        status, body = self._status("/engine/v1/engine?k=" + created.key)
        self.assertEqual(status, 429)
        self.assertIn("daily quota spent", body)

    def test_an_unmetered_route_is_never_refused(self):
        created = self._free_account()
        for _ in range(5):
            status, _ = self._status("/version.json?k=" + created.key)
            self.assertEqual(status, 200)

    def test_the_smc_route_is_metered_like_the_engine(self):
        # The SMC engine spends a model token per cold read, so it shares the
        # engine's metering rather than being a free route.
        created = self._free_account()
        for _ in range(2):
            status, _ = self._status("/smc/v1/smc?k=" + created.key)
            self.assertEqual(status, 200)
        status, body = self._status("/smc/v1/smc?k=" + created.key)
        self.assertEqual(status, 429)
        self.assertIn("daily quota spent", body)

    def test_a_pro_account_has_a_wider_allowance(self):
        created = self._accounts.store().create(email="p@b.c",
                                                tier=self._accounts.TIER_PRO)
        for _ in range(5):
            status, _ = self._status("/engine/v1/engine?k=" + created.key)
            self.assertEqual(status, 200)

    def test_the_master_token_is_never_metered(self):
        sm.ACCESS_TOKEN = "master-key"
        for _ in range(5):
            status, _ = self._status("/engine/v1/engine?k=master-key")
            self.assertEqual(status, 200)

    def test_me_json_reports_the_meter_without_moving_it(self):
        created = self._free_account()
        self._status("/engine/v1/engine?k=" + created.key)
        status, body = self._status("/me.json?k=" + created.key)
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["meter"]["used"], 1)
        self.assertEqual(payload["meter"]["limit"], 2)
        self.assertEqual(payload["meter"]["remaining"], 1)
        # /me.json is not metered, so a second read still shows 1, not 2.
        _, body2 = self._status("/me.json?k=" + created.key)
        self.assertEqual(json.loads(body2)["meter"]["used"], 1)

    def test_metering_off_leaves_the_routes_open(self):
        os.environ["ASTRA_METERING"] = ""
        created = self._free_account()
        for _ in range(5):
            status, _ = self._status("/engine/v1/engine?k=" + created.key)
            self.assertEqual(status, 200)

    def test_a_refusal_is_not_the_unlock_page(self):
        created = self._free_account()
        self._status("/engine/v1/engine?k=" + created.key)
        self._status("/engine/v1/engine?k=" + created.key)
        status, body = self._status("/engine/v1/engine?k=" + created.key)
        self.assertEqual(status, 429)
        # The body is JSON the app can read, not the HTML lock page.
        payload = json.loads(body)
        self.assertIn("meter", payload)
        self.assertNotIn("<html", body)


if __name__ == "__main__":
    unittest.main()
