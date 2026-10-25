#!/usr/bin/env python3
"""Tests for the multi-user account store (per-user keys with expiry).

These run the real store against real temp files — no mocks. The store is the
security boundary for a sold deployment, so the tests exercise the paths that
matter: key minting and resolution, expiry, revocation, tampering, and the
constant-time comparison.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import accounts as acc  # noqa: E402


class CreateAndAuthenticateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "accounts.json"
        self.store = acc.AccountStore(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_create_returns_a_usable_key(self):
        created = self.store.create(email="a@b.c", tier=acc.TIER_PRO, ttl_days=30)
        self.assertTrue(created.key.startswith("astra_"))
        account = self.store.authenticate(created.key)
        self.assertIsNotNone(account)
        self.assertEqual(account.id, created.account.id)
        self.assertEqual(account.tier, acc.TIER_PRO)

    def test_the_plaintext_key_is_never_stored(self):
        created = self.store.create(email="a@b.c")
        raw = self.path.read_text("utf-8")
        secret = created.key.partition(".")[2]
        self.assertNotIn(secret, raw)
        self.assertNotIn(created.key, raw)
        self.assertIn("key_hash", raw)

    def test_a_wrong_secret_for_a_real_id_is_rejected(self):
        created = self.store.create(email="a@b.c")
        account_id = created.account.id
        forged = f"astra_{account_id}.not-the-real-secret"
        self.assertIsNone(self.store.authenticate(forged))

    def test_unknown_account_id_is_rejected(self):
        self.assertIsNone(self.store.authenticate("astra_acc_zzzzzz.whatever"))

    def test_malformed_keys_are_rejected_without_raising(self):
        for bad in ("", "nope", "astra_", "astra_only", "astra_a_b_c",
                    "x_y_z", "astra_acc_ab12cd", "astra_.secret",
                    "astra_acc_ab12cd."):
            with self.subTest(key=bad):
                self.assertIsNone(self.store.authenticate(bad))

    def test_keys_are_unique_across_accounts(self):
        keys = {self.store.create(email=f"u{i}@x.co").key for i in range(25)}
        self.assertEqual(len(keys), 25)

    def test_account_survives_a_reload(self):
        created = self.store.create(email="a@b.c", ttl_days=10)
        reloaded = acc.AccountStore(self.path)
        account = reloaded.authenticate(created.key)
        self.assertIsNotNone(account)
        self.assertEqual(account.id, created.account.id)


class ExpiryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = acc.AccountStore(Path(self._tmp.name) / "a.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_expired_key_is_rejected(self):
        created = self.store.create(email="a@b.c", ttl_days=1)
        now = created.account.created_at
        # One second past the expiry: the key must no longer resolve.
        future = now + 86400 + 1
        self.assertIsNone(self.store.authenticate(created.key, now=future))

    def test_key_is_valid_just_before_expiry(self):
        created = self.store.create(email="a@b.c", ttl_days=1)
        now = created.account.created_at
        self.assertIsNotNone(
            self.store.authenticate(created.key, now=now + 86400 - 1))

    def test_ttl_zero_means_never_expires(self):
        created = self.store.create(email="a@b.c", ttl_days=None)
        self.assertIsNone(created.account.expires_at)
        self.assertIsNotNone(
            self.store.authenticate(created.key, now=created.account.created_at
                                    + 86400 * 365 * 50))
        self.assertIsNone(created.account.days_left())

    def test_days_left_counts_down(self):
        created = self.store.create(email="a@b.c", ttl_days=10)
        now = created.account.created_at
        self.assertEqual(created.account.days_left(now), 10)
        self.assertEqual(created.account.days_left(now + 86400), 9)


class RevocationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = acc.AccountStore(Path(self._tmp.name) / "a.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_revoked_key_stops_working_and_can_be_restored(self):
        created = self.store.create(email="a@b.c")
        self.assertIsNotNone(self.store.authenticate(created.key))
        self.assertTrue(self.store.revoke(created.account.id))
        self.assertIsNone(self.store.authenticate(created.key))
        self.assertTrue(self.store.restore(created.account.id))
        self.assertIsNotNone(self.store.authenticate(created.key))

    def test_extend_pushes_the_expiry_out(self):
        created = self.store.create(email="a@b.c", ttl_days=5)
        now = created.account.created_at
        before = created.account.expires_at
        self.assertTrue(self.store.extend(created.account.id, 10))
        after = self.store.get(created.account.id).expires_at
        self.assertAlmostEqual(after - before, 10 * 86400, places=3)
        # Still valid well past the original expiry.
        self.assertIsNotNone(
            self.store.authenticate(created.key, now=now + 14 * 86400))

    def test_mutations_on_a_missing_account_report_false(self):
        self.assertFalse(self.store.revoke("acc_nope"))
        self.assertFalse(self.store.extend("acc_nope", 5))
        self.assertFalse(self.store.restore("acc_nope"))
        self.assertFalse(self.store.link_chat("acc_nope", "123"))

    def test_unknown_tier_is_refused(self):
        with self.assertRaises(ValueError):
            self.store.create(email="a@b.c", tier="platinum")
        created = self.store.create(email="a@b.c")
        with self.assertRaises(ValueError):
            self.store.set_tier(created.account.id, "platinum")


class CrossProcessReloadTest(unittest.TestCase):
    """A second process (the CLI) edits the file; a live store must see it.

    The server caches the store in memory. `create`/`revoke`/`extend` run as
    their own process and only rewrite the file, so without an mtime check a
    revoke would not bite until the server restarted — the bug this locks out.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "a.json"
        self.store = acc.AccountStore(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_revoke_in_another_store_takes_effect_here(self):
        created = self.store.create(email="a@b.c")
        self.assertIsNotNone(self.store.authenticate(created.key))
        # A separate store on the same path stands in for the CLI process.
        other = acc.AccountStore(self.path)
        self.assertTrue(other.revoke(created.account.id))
        self.assertIsNone(self.store.authenticate(created.key))

    def test_create_in_another_store_is_visible_here(self):
        other = acc.AccountStore(self.path)
        created = other.create(email="later@b.c", ttl_days=7)
        self.assertIsNotNone(self.store.authenticate(created.key))
        self.assertEqual(len(self.store.list_accounts()), 1)

    def test_extend_in_another_store_is_visible_here(self):
        created = self.store.create(email="a@b.c", ttl_days=1)
        other = acc.AccountStore(self.path)
        self.assertTrue(other.extend(created.account.id, 30))
        # Valid well past the original one-day expiry.
        self.assertIsNotNone(
            self.store.authenticate(created.key, now=created.account.created_at
                                    + 10 * 86400))


class ChatLinkTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = acc.AccountStore(Path(self._tmp.name) / "a.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_find_by_chat_returns_the_linked_account(self):
        created = self.store.create(email="a@b.c", chat_id="555")
        found = self.store.find_by_chat("555")
        self.assertIsNotNone(found)
        self.assertEqual(found.id, created.account.id)

    def test_find_by_chat_ignores_a_revoked_account(self):
        created = self.store.create(email="a@b.c", chat_id="555")
        self.store.revoke(created.account.id)
        self.assertIsNone(self.store.find_by_chat("555"))

    def test_find_by_chat_ignores_an_expired_account(self):
        created = self.store.create(email="a@b.c", chat_id="555", ttl_days=1)
        # One second past expiry the account is no longer valid, so the chat
        # must resolve to nothing even though the link is still on record.
        now = created.account.created_at + 86400 + 1
        self.assertIsNone(self.store.find_by_chat("555", now=now))
        # Just before expiry it still resolves.
        self.assertIsNotNone(
            self.store.find_by_chat("555", now=created.account.created_at))

    def test_empty_chat_id_matches_nothing(self):
        self.store.create(email="a@b.c")
        self.assertIsNone(self.store.find_by_chat(""))

    def test_link_chat_attaches_after_creation(self):
        created = self.store.create(email="a@b.c")
        self.assertTrue(self.store.link_chat(created.account.id, "999"))
        self.assertEqual(self.store.find_by_chat("999").id, created.account.id)


class CountsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = acc.AccountStore(Path(self._tmp.name) / "a.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_counts_track_total_valid_pro_and_expired(self):
        self.store.create(email="p1@x.co", tier=acc.TIER_PRO, ttl_days=30)
        self.store.create(email="p2@x.co", tier=acc.TIER_PRO, ttl_days=30)
        e = self.store.create(email="e@x.co", tier=acc.TIER_PRO, ttl_days=1)
        self.store.extend(e.account.id, -100)  # push it into the past
        c = self.store.counts()
        self.assertEqual(c["total"], 3)
        self.assertEqual(c["pro"], 2)
        self.assertEqual(c["expired"], 1)


class TamperTest(unittest.TestCase):
    """A hand-edited store must fail closed, not open."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "a.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_corrupt_file_loads_as_empty(self):
        self.path.write_text("{not json", "utf-8")
        store = acc.AccountStore(self.path)
        self.assertEqual(store.list_accounts(), [])

    def test_missing_file_loads_as_empty(self):
        store = acc.AccountStore(Path(self._tmp.name) / "absent.json")
        self.assertEqual(store.counts()["total"], 0)

    def test_editing_the_stored_hash_does_not_yield_a_working_key(self):
        store = acc.AccountStore(self.path)
        created = store.create(email="a@b.c")
        raw = json.loads(self.path.read_text("utf-8"))
        raw["accounts"][0]["key_hash"] = "0" * 64
        self.path.write_text(json.dumps(raw), "utf-8")
        reloaded = acc.AccountStore(self.path)
        self.assertIsNone(reloaded.authenticate(created.key))


class EnabledFlagTest(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("ASTRA_ACCOUNTS", "ASTRA_ACCOUNTS_FILE",
                        "ASTRA_RUN_DIR")}
        acc.reset_store()

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        acc.reset_store()

    def test_off_by_default(self):
        os.environ.pop("ASTRA_ACCOUNTS", None)
        self.assertFalse(acc.enabled())

    def test_enabled_by_truthy_flags(self):
        for flag in ("1", "true", "YES", "on"):
            with self.subTest(flag=flag):
                os.environ["ASTRA_ACCOUNTS"] = flag
                self.assertTrue(acc.enabled())

    def test_disabled_by_falsy_flags(self):
        for flag in ("0", "false", "no", ""):
            with self.subTest(flag=flag):
                os.environ["ASTRA_ACCOUNTS"] = flag
                self.assertFalse(acc.enabled())

    def test_path_comes_from_env(self):
        os.environ["ASTRA_ACCOUNTS_FILE"] = "/tmp/custom-accounts.json"
        self.assertEqual(str(acc.accounts_path()), "/tmp/custom-accounts.json")

    def test_path_defaults_under_run_dir(self):
        os.environ.pop("ASTRA_ACCOUNTS_FILE", None)
        os.environ["ASTRA_RUN_DIR"] = "/tmp/astra-run"
        self.assertEqual(str(acc.accounts_path()),
                         "/tmp/astra-run/accounts.json")


class CliTest(unittest.TestCase):
    """The operator CLI is the only way to manage accounts day to day."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.file = str(Path(self._tmp.name) / "a.json")

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, *argv):
        return acc.main(["--file", self.file, *argv])

    def _create(self, **kw):
        return acc.AccountStore(Path(self.file)).create(
            email=kw.pop("email", "a@b.c"), **kw)

    def test_revoke_then_restore_round_trips(self):
        created = self._create()
        self.assertEqual(self._run("revoke", "--id", created.account.id), 0)
        self.assertIsNone(
            acc.AccountStore(Path(self.file)).authenticate(created.key))
        # `restore` exists so a revoke by mistake is recoverable from the CLI.
        self.assertEqual(self._run("restore", "--id", created.account.id), 0)
        self.assertIsNotNone(
            acc.AccountStore(Path(self.file)).authenticate(created.key))

    def test_restore_and_revoke_reject_unknown_ids(self):
        self.assertEqual(self._run("revoke", "--id", "acc_nope"), 1)
        self.assertEqual(self._run("restore", "--id", "acc_nope"), 1)

    def test_extend_and_link_report_missing_accounts(self):
        self.assertEqual(self._run("extend", "--id", "acc_nope",
                                   "--days", "5"), 1)
        self.assertEqual(self._run("link", "--id", "acc_nope",
                                   "--chat-id", "42"), 1)

    def test_list_runs_clean_on_an_empty_store(self):
        self.assertEqual(self._run("list"), 0)


if __name__ == "__main__":
    unittest.main()
