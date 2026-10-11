"""TEL-0102 - Tests for the ASTRA Telegram bot.

Exercises the real formatting, command routing, subscription store, and alert
state-machine against recorded API payloads. No network is used: the API and
Telegram transports are replaced by in-process fakes that implement the same
calls the production objects make.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import astra_bot as bot
from astra_api import ApiError, AstraApi


HEALTH_ONLINE = {"aggregate": "ONLINE", "decision_grade_data": True,
                 "degraded_reasons": []}
HEALTH_DEGRADED = {"aggregate": "DEGRADED", "decision_grade_data": False,
                   "degraded_reasons": ["mt5 offline"]}
SYSTEM = {"mode": "SHADOW", "shadow_only": True, "ready": True,
          "startup_stage": "READY"}
BRIDGE = {"bridge_process_state": "ONLINE", "mt5_ready": True,
          "resolved_symbol": "XAUUSD", "last_error": ""}
ANALYSIS_EMPTY = {"timestamp": None, "signal": {"direction": "NONE"}}
ANALYSIS_FULL = {
    "timestamp": "2026-10-09T10:00:00Z",
    "context": {"regime": "TREND", "h4_bias": "BULL", "m15_trigger": "LONG"},
    "signal": {"direction": "LONG", "score": 72, "probability": 0.61,
               "probability_calibrated": True},
}


class FakeApi(AstraApi):
    """Returns canned payloads; raises ApiError when marked unavailable."""

    def __init__(self, health, system, bridge, analysis, available=True):
        self._health = health
        self._system = system
        self._bridge = bridge
        self._analysis = analysis
        self._available = available

    def _maybe_raise(self):
        if not self._available:
            raise ApiError("backend unreachable")

    def health(self):
        self._maybe_raise()
        return self._health

    def system_state(self):
        self._maybe_raise()
        return self._system

    def bridge_status(self):
        self._maybe_raise()
        return self._bridge

    def latest_analysis(self):
        self._maybe_raise()
        return self._analysis

    def latest_signal(self):
        self._maybe_raise()
        return {"available": False, "reason": "no decisions recorded yet"}

    def last_price(self):
        self._maybe_raise()
        return 4181.87


class FakeClient:
    def __init__(self):
        self.sent = []

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


class FormattingTests(unittest.TestCase):
    def test_status_includes_core_fields(self):
        snap = {"health": HEALTH_ONLINE, "system": SYSTEM, "bridge": BRIDGE}
        out = bot.format_status(snap)
        self.assertIn("ONLINE", out)
        self.assertIn("SHADOW", out)
        self.assertIn("XAUUSD", out)
        self.assertIn("Not a trading signal", out)

    def test_status_tolerates_unavailable_section(self):
        snap = {"health": {"_error": "boom"}, "system": SYSTEM, "bridge": BRIDGE}
        out = bot.format_status(snap)
        self.assertIn("unavailable", out)

    def test_analysis_empty_state(self):
        out = bot.format_analysis(ANALYSIS_EMPTY)
        self.assertIn("No decision recorded yet", out)

    def test_analysis_full_state(self):
        out = bot.format_analysis(ANALYSIS_FULL)
        self.assertIn("LONG", out)
        self.assertIn("TREND", out)
        self.assertIn("0.61", out)

    def test_signal_unavailable(self):
        out = bot.format_signal({"available": False, "reason": "nothing"})
        self.assertIn("nothing", out)

    def test_bridge_formatting(self):
        out = bot.format_bridge(BRIDGE)
        self.assertIn("ONLINE", out)
        self.assertIn("yes", out)


class SubscriptionTests(unittest.TestCase):
    def test_add_remove_persist(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "subs.json"
            subs = bot.Subscriptions(path)
            subs.add("111")
            subs.add("222")
            subs.remove("111")
            self.assertEqual(subs.all(), ["222"])
            self.assertTrue(path.exists())
            reloaded = bot.Subscriptions(path)
            self.assertEqual(reloaded.all(), ["222"])


class RouterTests(unittest.TestCase):
    def _router(self, api, chat="9"):
        with tempfile.TemporaryDirectory() as d:
            subs = bot.Subscriptions(Path(d) / "s.json")
            return bot.CommandRouter(api, subs, chat), subs

    def test_help(self):
        api = FakeApi(HEALTH_ONLINE, SYSTEM, BRIDGE, ANALYSIS_EMPTY)
        router, _ = self._router(api)
        self.assertIn("/status", router.handle("9", "/help"))

    def test_status_command(self):
        api = FakeApi(HEALTH_ONLINE, SYSTEM, BRIDGE, ANALYSIS_EMPTY)
        router, _ = self._router(api)
        out = router.handle("9", "/status")
        self.assertIn("ONLINE", out)

    def test_command_with_botname_suffix(self):
        api = FakeApi(HEALTH_ONLINE, SYSTEM, BRIDGE, ANALYSIS_EMPTY)
        router, _ = self._router(api)
        out = router.handle("9", "/status@Uhxsuigf_bot")
        self.assertIn("ONLINE", out)

    def test_subscribe_then_unsubscribe(self):
        api = FakeApi(HEALTH_ONLINE, SYSTEM, BRIDGE, ANALYSIS_EMPTY)
        router, subs = self._router(api)
        router.handle("7", "/subscribe")
        self.assertIn("7", subs.all())
        router.handle("7", "/unsubscribe")
        self.assertNotIn("7", subs.all())

    def test_backend_unavailable_is_reported(self):
        api = FakeApi(HEALTH_ONLINE, SYSTEM, BRIDGE, ANALYSIS_EMPTY, available=False)
        router, _ = self._router(api)
        out = router.handle("9", "/status")
        self.assertIn("unavailable", out)

    def test_unknown_command(self):
        api = FakeApi(HEALTH_ONLINE, SYSTEM, BRIDGE, ANALYSIS_EMPTY)
        router, _ = self._router(api)
        self.assertIn("Unknown command", router.handle("9", "/nope"))


class AlertEngineTests(unittest.TestCase):
    def test_prime_suppresses_initial_alert(self):
        api = FakeApi(HEALTH_ONLINE, SYSTEM, BRIDGE, ANALYSIS_EMPTY)
        engine = bot.AlertEngine(api)
        engine.prime()
        self.assertEqual(engine.poll(), [])

    def test_health_transition_alerts(self):
        api = FakeApi(HEALTH_ONLINE, SYSTEM, BRIDGE, ANALYSIS_EMPTY)
        engine = bot.AlertEngine(api)
        engine.prime()
        api._health = HEALTH_DEGRADED
        alerts = engine.poll()
        self.assertTrue(any("Health changed" in a for a in alerts))

    def test_new_signal_alerts(self):
        api = FakeApi(HEALTH_ONLINE, SYSTEM, BRIDGE, ANALYSIS_EMPTY)
        engine = bot.AlertEngine(api)
        engine.prime()
        api._analysis = ANALYSIS_FULL
        alerts = engine.poll()
        self.assertTrue(any("Analysis" in a for a in alerts))

    def test_no_change_no_alert(self):
        api = FakeApi(HEALTH_ONLINE, SYSTEM, BRIDGE, ANALYSIS_EMPTY)
        engine = bot.AlertEngine(api)
        engine.prime()
        self.assertEqual(engine.poll(), [])


class BroadcastTests(unittest.TestCase):
    def test_broadcast_reaches_every_subscriber(self):
        with tempfile.TemporaryDirectory() as d:
            subs = bot.Subscriptions(Path(d) / "s.json")
            subs.add("1")
            subs.add("2")
            client = FakeClient()
            bot._broadcast(client, subs, "hello")
            self.assertEqual(sorted(c for c, _ in client.sent), ["1", "2"])


class PaidToolTests(unittest.TestCase):
    """The free/pro gate and price-level commands, end to end through the router."""

    def _router(self, ent=None, chat="9"):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        api = FakeApi(HEALTH_ONLINE, SYSTEM, BRIDGE, ANALYSIS_EMPTY)
        subs = bot.Subscriptions(Path(d.name) / "s.json")
        levels = bot.LevelStore(Path(d.name) / "levels.json")
        return bot.CommandRouter(api, subs, chat, levels=levels, ent=ent), subs, levels

    def test_price_command(self):
        router, _, _ = self._router()
        out = router.handle("9", "/price")
        self.assertIn("4,181.87", out)

    def test_level_add_and_list(self):
        router, _, levels = self._router()
        router.handle("9", "/level 4180 above")
        self.assertEqual(levels.count("9"), 1)
        self.assertIn("4180", router.handle("9", "/levels"))

    def test_level_bad_input(self):
        router, _, _ = self._router()
        self.assertIn("not a number", router.handle("9", "/level abc"))

    def test_free_tier_level_cap(self):
        from entitlements import Entitlements
        with tempfile.TemporaryDirectory() as d:
            ent = Entitlements(Path(d) / "e.json", upgrade_url="https://buy/x",
                               free_levels=1)
            router, _, levels = self._router(ent=ent)
            router.handle("9", "/level 4180")
            out = router.handle("9", "/level 4200")
            self.assertIn("Free plan allows 1", out)
            self.assertIn("https://buy/x", out)
            self.assertEqual(levels.count("9"), 1)

    def test_pro_tier_no_cap(self):
        from entitlements import Entitlements
        with tempfile.TemporaryDirectory() as d:
            ent = Entitlements(Path(d) / "e.json", upgrade_url="https://buy/x",
                               free_levels=1)
            ent.grant_pro("9")
            router, _, levels = self._router(ent=ent)
            router.handle("9", "/level 4180")
            router.handle("9", "/level 4200")
            self.assertEqual(levels.count("9"), 2)

    def test_free_subscribe_is_gated(self):
        from entitlements import Entitlements
        with tempfile.TemporaryDirectory() as d:
            ent = Entitlements(Path(d) / "e.json", upgrade_url="https://buy/x")
            router, subs, _ = self._router(ent=ent)
            out = router.handle("9", "/subscribe")
            self.assertIn("Pro", out)
            self.assertNotIn("9", subs.all())

    def test_myid(self):
        router, _, _ = self._router()
        self.assertIn("9", router.handle("9", "/myid"))

    def test_plan_free_shows_upgrade(self):
        from entitlements import Entitlements
        with tempfile.TemporaryDirectory() as d:
            ent = Entitlements(Path(d) / "e.json", upgrade_url="https://buy/x")
            router, _, _ = self._router(ent=ent)
            out = router.handle("9", "/plan")
            self.assertIn("free", out)
            self.assertIn("https://buy/x", out)

    def test_grant_only_for_admin(self):
        from entitlements import Entitlements
        with tempfile.TemporaryDirectory() as d:
            ent = Entitlements(Path(d) / "e.json", upgrade_url="https://buy/x",
                               admin_chats=["1"])
            router, _, _ = self._router(ent=ent)
            # Non-admin cannot grant.
            router.handle("9", "/grant 55 pro")
            self.assertFalse(ent.is_pro("55"))
            # Admin can.
            router.handle("1", "/grant 55 pro")
            self.assertTrue(ent.is_pro("55"))

    def test_level_alert_fires_once(self):
        router, _, levels = self._router()
        router.handle("9", "/level 4180 above")
        client = FakeClient()
        router.api.last_price = lambda: 4170.0   # start below the level
        bot._check_levels(router.api, levels, None, client)
        self.assertEqual(client.sent, [])        # first look only learns the side
        router.api.last_price = lambda: 4181.0   # now rise through it
        bot._check_levels(router.api, levels, None, client)
        self.assertEqual(len(client.sent), 1)
        self.assertIn("up through", client.sent[0][1])
        # One-shot: no repeat.
        bot._check_levels(router.api, levels, None, client)
        self.assertEqual(len(client.sent), 1)


if __name__ == "__main__":
    unittest.main()
