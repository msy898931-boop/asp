"""ENT/LVC tests - the free/pro gate and price-level crossing.

Run directly:  python3 test_entitlements.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from entitlements import TIER_FREE, Entitlements
from price_levels import DIR_ABOVE, DIR_BELOW, DIR_CROSS, LevelStore, crossed, side_of


class EntitlementsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _ent(self, **kw) -> Entitlements:
        return Entitlements(self.dir / "entitlements.json", **kw)

    def test_gate_dormant_without_upgrade_url(self) -> None:
        ent = self._ent(upgrade_url="")
        self.assertFalse(ent.selling)
        self.assertTrue(ent.is_pro("anyone"))  # nobody is withheld anything
        self.assertEqual(ent.max_levels("anyone"), ent.pro_levels)

    def test_gate_live_with_upgrade_url(self) -> None:
        ent = self._ent(upgrade_url="https://buy.stripe.com/x", free_levels=2)
        self.assertTrue(ent.selling)
        self.assertFalse(ent.is_pro("newuser"))
        self.assertEqual(ent.max_levels("newuser"), 2)
        self.assertIn("https://buy.stripe.com/x", ent.upgrade_line())

    def test_grant_persists(self) -> None:
        ent = self._ent(upgrade_url="https://buy.stripe.com/x")
        ent.grant_pro("42")
        self.assertTrue(ent.is_pro("42"))
        again = self._ent(upgrade_url="https://buy.stripe.com/x")
        self.assertTrue(again.is_pro("42"))

    def test_admin_always_pro(self) -> None:
        ent = self._ent(upgrade_url="https://buy.stripe.com/x", admin_chats=["7"])
        self.assertTrue(ent.is_pro("7"))
        self.assertTrue(ent.is_admin("7"))
        self.assertFalse(ent.is_admin("8"))

    def test_revoke(self) -> None:
        ent = self._ent(upgrade_url="https://buy.stripe.com/x")
        ent.grant_pro("9")
        ent.revoke("9")
        self.assertEqual(ent.tier("9"), TIER_FREE)

    def test_bad_tier_rejected(self) -> None:
        ent = self._ent()
        with self.assertRaises(ValueError):
            ent.set_tier("1", "platinum")


class CrossingTest(unittest.TestCase):
    def test_side_of(self) -> None:
        self.assertEqual(side_of(10.0, 9.0), "above")
        self.assertEqual(side_of(9.0, 9.0), "above")
        self.assertEqual(side_of(8.0, 9.0), "below")

    def test_new_level_learns_side_without_firing(self) -> None:
        lv = LevelLike(price=100.0, last_side=None)
        self.assertIsNone(crossed(lv, 101.0))
        self.assertIsNone(crossed(lv, 99.0))  # still unknown until observed

    def test_cross_up_fires(self) -> None:
        lv = LevelLike(price=100.0, last_side="below", direction=DIR_CROSS)
        self.assertEqual(crossed(lv, 100.5), "above")

    def test_cross_down_fires(self) -> None:
        lv = LevelLike(price=100.0, last_side="above", direction=DIR_CROSS)
        self.assertEqual(crossed(lv, 99.5), "below")

    def test_no_fire_same_side(self) -> None:
        lv = LevelLike(price=100.0, last_side="above", direction=DIR_CROSS)
        self.assertIsNone(crossed(lv, 101.0))

    def test_direction_filters(self) -> None:
        # "above" only cares about rising through the level.
        up_only = LevelLike(price=100.0, last_side="above", direction=DIR_ABOVE)
        self.assertIsNone(crossed(up_only, 99.0))          # falling: ignored
        down_only = LevelLike(price=100.0, last_side="below", direction=DIR_BELOW)
        self.assertIsNone(crossed(down_only, 101.0))       # rising: ignored


class LevelStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "price_levels.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_add_list_remove(self) -> None:
        store = LevelStore(self.path)
        store.add("a", 4180.0, DIR_ABOVE)
        store.add("a", 4100.0, DIR_BELOW)
        self.assertEqual(store.count("a"), 2)
        self.assertTrue(store.remove("a", 4180.0))
        self.assertEqual(store.count("a"), 1)

    def test_persist_roundtrip(self) -> None:
        store = LevelStore(self.path)
        store.add("a", 4180.0)
        again = LevelStore(self.path)
        self.assertEqual(again.count("a"), 1)
        self.assertEqual(again.list("a")[0].price, 4180.0)

    def test_fire_is_one_shot(self) -> None:
        store = LevelStore(self.path)
        store.add("a", 4180.0, DIR_ABOVE)
        store.observe("a", 4170.0)          # learn side: below
        fired = store.observe("a", 4181.0)  # cross up
        self.assertEqual(len(fired), 1)
        # A second observation above must not fire again.
        self.assertEqual(store.observe("a", 4185.0), [])

    def test_only_armed_levels_fire(self) -> None:
        store = LevelStore(self.path)
        store.add("a", 4180.0, DIR_ABOVE)
        store.observe("a", 4181.0)  # first observation only learns the side
        self.assertEqual(store.observe("a", 4181.0), [])

    def test_chats_only_nonempty(self) -> None:
        store = LevelStore(self.path)
        store.add("a", 1.0)
        store.add("b", 2.0)
        store.clear("b")
        self.assertEqual(store.chats(), ["a"])


class LevelLike:
    """Minimal stand-in exposing the fields `crossed` reads."""

    def __init__(self, price: float, last_side, direction: str = DIR_CROSS) -> None:
        self.price = price
        self.last_side = last_side
        self.direction = direction


class AccountsLinkTest(unittest.TestCase):
    """A paying user's chat id is Pro through the mobile account store.

    The store is the real one from `mobile/accounts.py`, driven against a temp
    file — no mock — so the bot honours exactly the tier and expiry the web
    gate does.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        mobile = Path(__file__).resolve().parents[2] / "mobile"
        sys.path.insert(0, str(mobile))
        import accounts
        self.accounts = accounts
        self.store = accounts.AccountStore(self.dir / "accounts.json")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _ent(self, selling: bool = True) -> Entitlements:
        return Entitlements(
            self.dir / "entitlements.json",
            upgrade_url="https://buy.stripe.com/x" if selling else "",
            accounts_store=self.store,
        )

    def test_linked_chat_is_pro(self) -> None:
        self.store.create(email="a@b.c", chat_id="555", ttl_days=30)
        self.assertEqual(self._ent().tier("555"), "pro")

    def test_unlinked_chat_is_free_when_selling(self) -> None:
        self.assertEqual(self._ent().tier("999"), TIER_FREE)

    def test_revoked_account_loses_pro(self) -> None:
        created = self.store.create(email="a@b.c", chat_id="555")
        self.assertEqual(self._ent().tier("555"), "pro")
        self.store.revoke(created.account.id)
        self.assertEqual(self._ent().tier("555"), TIER_FREE)

    def test_expired_account_loses_pro(self) -> None:
        created = self.store.create(email="a@b.c", chat_id="555", ttl_days=1)
        self.store.extend(created.account.id, -100)
        self.assertEqual(self._ent().tier("555"), TIER_FREE)

    def test_admin_still_wins_over_any_account(self) -> None:
        ent = Entitlements(self.dir / "e.json",
                           upgrade_url="https://x", admin_chats=["777"],
                           accounts_store=self.store)
        self.assertEqual(ent.tier("777"), "pro")

    def test_no_store_means_old_behaviour(self) -> None:
        # Without a store the class must behave exactly as before.
        self.assertEqual(self._ent().tier("555"), TIER_FREE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
