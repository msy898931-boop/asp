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
        self.assertIn("ليست إشارة تداول", out)

    def test_status_tolerates_unavailable_section(self):
        snap = {"health": {"_error": "boom"}, "system": SYSTEM, "bridge": BRIDGE}
        out = bot.format_status(snap)
        self.assertIn("غير متاح", out)

    def test_analysis_empty_state(self):
        out = bot.format_analysis(ANALYSIS_EMPTY)
        self.assertIn("لا قرار مسجّل بعد", out)

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
        self.assertIn("نعم", out)


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
        self.assertIn("غير متاح", out)

    def test_unknown_command(self):
        api = FakeApi(HEALTH_ONLINE, SYSTEM, BRIDGE, ANALYSIS_EMPTY)
        router, _ = self._router(api)
        self.assertIn("أمر غير معروف", router.handle("9", "/nope"))


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
        self.assertTrue(any("تغيّرت الصحة" in a for a in alerts))

    def test_new_signal_alerts(self):
        api = FakeApi(HEALTH_ONLINE, SYSTEM, BRIDGE, ANALYSIS_EMPTY)
        engine = bot.AlertEngine(api)
        engine.prime()
        api._analysis = ANALYSIS_FULL
        alerts = engine.poll()
        self.assertTrue(any("التحليل" in a for a in alerts))

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
        self.assertIn("ليس رقمًا", router.handle("9", "/level abc"))

    def test_free_tier_level_cap(self):
        from entitlements import Entitlements
        with tempfile.TemporaryDirectory() as d:
            ent = Entitlements(Path(d) / "e.json", upgrade_url="https://buy/x",
                               free_levels=1)
            router, _, levels = self._router(ent=ent)
            router.handle("9", "/level 4180")
            out = router.handle("9", "/level 4200")
            self.assertIn("الخطة المجانية تسمح بـ 1", out)
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
            self.assertIn("مجاني", out)
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
        self.assertIn("صعودًا عبر", client.sent[0][1])
        # One-shot: no repeat.
        bot._check_levels(router.api, levels, None, client)
        self.assertEqual(len(client.sent), 1)


class ArabicSurfaceTests(unittest.TestCase):
    """The Arabic keyboard, the command menu, and Arabic direction words."""

    def test_keyboard_labels_all_map_to_commands(self):
        for row in bot.REPLY_KEYBOARD:
            for label in row:
                self.assertIn(label, bot.BUTTON_COMMANDS)

    def test_tapping_a_button_runs_its_command(self):
        router, _, levels = _paid_router()
        out = router.handle("9", "🔔 مستوى جديد")  # same as /level
        self.assertIn("الصيغة", out)              # the /level usage line

    def test_button_price_label(self):
        router, _, _ = _paid_router()
        self.assertIn("XAUUSD", router.handle("9", "💰 سعر الذهب"))

    def test_arabic_direction_word_is_accepted(self):
        router, _, levels = _paid_router()
        router.handle("9", "/level 4200 فوق")
        self.assertEqual(levels.list("9")[0].direction, "above")

    def test_command_menu_is_arabic_and_covers_the_real_commands(self):
        cmds = dict(bot.BOT_COMMANDS)
        for expected in ("/price", "/level", "/levels", "/plan", "/myid",
                         "/subscribe", "/unsubscribe", "/help"):
            self.assertIn(expected, cmds)
        self.assertTrue(any("\u0600" <= ch <= "\u06ff" for d in cmds.values()
                            for ch in d), "menu descriptions should be Arabic")


def _paid_router(ent=None):
    """A router with a working fake backend, for the surface tests."""
    api = FakeApi(HEALTH_ONLINE, SYSTEM, BRIDGE, ANALYSIS_EMPTY)
    subs = bot.Subscriptions(Path(tempfile.mkdtemp()) / "s.json")
    levels = bot.LevelStore(Path(tempfile.mkdtemp()) / "l.json")
    return bot.CommandRouter(api, subs, None, levels=levels, ent=ent), subs, levels


class FakeVolApi(FakeApi):
    """A fake backend whose bridge returns a canned candle series."""

    def __init__(self, bars, available=True):
        super().__init__(HEALTH_ONLINE, SYSTEM, BRIDGE, ANALYSIS_EMPTY,
                         available=available)
        self._bars = bars

    def candles(self, timeframe="M15", limit=2):
        return {"payload": {"bars": self._bars}}


class FakeModel:
    """A VolatilityModel stand-in with a scripted prediction."""

    threshold = 0.65

    def __init__(self, value):
        self.value = value

    def predict(self, bars_by_tf):
        return self.value


class VolatilityCommandTests(unittest.TestCase):
    def _router(self, model, available=True):
        api = FakeVolApi([{"close": 1800.0, "high": 1801, "low": 1799}], available)
        subs = bot.Subscriptions(Path(tempfile.mkdtemp()) / "s.json")
        return bot.CommandRouter(api, subs, None, vol_model=model), api, subs

    def test_high_reading_is_reported_as_a_percentage(self):
        router, _, _ = self._router(FakeModel(0.80))
        reply = router.handle("1", "/vol")
        self.assertIn("80%", reply)
        self.assertIn("مرتفع", reply)

    def test_reading_states_it_does_not_predict_direction(self):
        router, _, _ = self._router(FakeModel(0.50))
        self.assertIn("لا اتجاهها", router.handle("1", "/vol"))

    def test_unavailable_model_degrades_cleanly(self):
        router, _, _ = self._router(None)
        self.assertIn("غير مفعّل", router.handle("1", "/vol"))

    def test_missing_prediction_does_not_invent_a_number(self):
        router, _, _ = self._router(FakeModel(None))
        self.assertIn("لا بيانات كافية", router.handle("1", "/vol"))

    def test_help_lists_the_vol_command(self):
        self.assertIn("/vol", bot.CommandRouter.HELP)


class VolatilityMonitorTests(unittest.TestCase):
    def _check(self, value, was_high):
        api = FakeVolApi([{"close": 1800.0, "high": 1801, "low": 1799}])
        subs = bot.Subscriptions(Path(tempfile.mkdtemp()) / "s.json")
        subs.add("42")
        client = FakeClient()
        new_state = bot._check_volatility(api, FakeModel(value), subs, client,
                                          was_high)
        return new_state, client.sent

    def test_crossing_the_threshold_alerts_once(self):
        state, sent = self._check(0.80, was_high=False)
        self.assertTrue(state)
        self.assertEqual(len(sent), 1)
        self.assertIn("80%", sent[0][1])

    def test_staying_high_does_not_repeat_the_alert(self):
        state, sent = self._check(0.80, was_high=True)
        self.assertTrue(state)
        self.assertEqual(sent, [])

    def test_calm_market_does_not_alert(self):
        state, sent = self._check(0.30, was_high=False)
        self.assertFalse(state)
        self.assertEqual(sent, [])

    def test_rearming_after_calm_alerts_again(self):
        state, sent = self._check(0.80, was_high=False)
        self.assertTrue(state)
        state, sent = self._check(0.30, was_high=state)
        self.assertFalse(state)
        state, sent = self._check(0.80, was_high=state)
        self.assertTrue(state)
        self.assertEqual(len(sent), 1)


if __name__ == "__main__":
    unittest.main()
