"""TEL-0102 - Tests for the ASTRA Telegram bot.

Exercises the real formatting, command routing, subscription store, and alert
state-machine against recorded API payloads. No network is used: the API and
Telegram transports are replaced by in-process fakes that implement the same
calls the production objects make.
"""

from __future__ import annotations

import tempfile
import time
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
        self.assertIn("شراء", out)
        self.assertIn("TREND", out)
        self.assertIn("0.61", out)

    def test_analysis_labels_the_direction_in_arabic(self):
        # A raw "SHORT" beside an Arabic label is the same mismatch the trade
        # alerts had; the direction is labelled like the rest of the line.
        out = bot.format_analysis({"timestamp": "t", "context": {},
                                   "signal": {"direction": "SHORT"}})
        self.assertIn("الاتجاه: بيع", out)
        self.assertNotIn("SHORT", out)

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


class StatePersistenceTests(unittest.TestCase):
    """Every store must survive a restart — a restart is a new object reading
    the same directory. This is the bot's whole memory (subscribers, levels,
    tiers); on a host with a persistent disk at /root/.astra it survives a
    deploy, and if that disk is missing this is exactly what is lost."""

    def test_all_stores_reload_from_the_same_dir(self):
        with tempfile.TemporaryDirectory() as d:
            state_dir = Path(d)
            # A selling URL is needed, else everyone is Pro by default and the
            # tier assertion would pass without any persistence.
            subs = bot.Subscriptions(state_dir / "telegram_subscribers.json")
            levels = bot.LevelStore(state_dir / "price_levels.json")
            ent = bot.Entitlements(state_dir / "entitlements.json",
                                   upgrade_url="https://buy.example/x")

            subs.add("111")
            levels.add("111", 2400.5)
            ent.grant_pro("111")

            # A restart: fresh objects over the same directory.
            subs2 = bot.Subscriptions(state_dir / "telegram_subscribers.json")
            levels2 = bot.LevelStore(state_dir / "price_levels.json")
            ent2 = bot.Entitlements(state_dir / "entitlements.json",
                                    upgrade_url="https://buy.example/x")

            self.assertEqual(subs2.all(), ["111"])
            self.assertEqual(levels2.count("111"), 1)
            self.assertTrue(ent2.is_pro("111"))
            self.assertFalse(ent2.is_pro("999"))  # not granted, stays free

    def test_the_stores_use_separate_files(self):
        with tempfile.TemporaryDirectory() as d:
            state_dir = Path(d)
            bot.Subscriptions(state_dir / "telegram_subscribers.json").add("111")
            bot.LevelStore(state_dir / "price_levels.json").add("111", 2400.5)
            bot.Entitlements(state_dir / "entitlements.json").grant_pro("111")
            names = sorted(p.name for p in state_dir.iterdir())
            self.assertEqual(
                names,
                ["entitlements.json", "price_levels.json",
                 "telegram_subscribers.json"])


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

    def test_broadcast_mirrors_to_web_push(self):
        """Every Telegram alert is also pushed to the phone's subscribers."""
        calls = []

        class _FakePush:
            @staticmethod
            def broadcast(payload):
                calls.append(payload)
                return {"configured": True, "sent": 1, "removed": 0}

        saved = bot.webpush
        bot.webpush = _FakePush
        try:
            with tempfile.TemporaryDirectory() as d:
                subs = bot.Subscriptions(Path(d) / "s.json")
                subs.add("1")
                bot._broadcast(FakeClient(), subs, "signal")
        finally:
            bot.webpush = saved
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["body"], "signal")

    def test_push_failure_never_breaks_the_telegram_send(self):
        """Web Push is best-effort: a broken one must not stop the alert."""
        class _BrokenPush:
            @staticmethod
            def broadcast(payload):
                raise RuntimeError("push service down")

        saved = bot.webpush
        bot.webpush = _BrokenPush
        try:
            with tempfile.TemporaryDirectory() as d:
                subs = bot.Subscriptions(Path(d) / "s.json")
                subs.add("1")
                client = FakeClient()
                bot._broadcast(client, subs, "hello")  # must not raise
        finally:
            bot.webpush = saved
        self.assertEqual([c for c, _ in client.sent], ["1"])

    def test_push_is_a_noop_without_the_module(self):
        saved = bot.webpush
        bot.webpush = None
        try:
            bot._push_mirror("anything")  # must not raise
        finally:
            bot.webpush = saved


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

    def test_the_keyboard_stays_small(self):
        # The keyboard is for the day-to-day essentials only. It drifted to
        # eleven buttons once, then to six; this pins the five that survived
        # so it cannot creep back.
        labels = [label for row in bot.REPLY_KEYBOARD for label in row]
        self.assertLessEqual(len(labels), 5)
        self.assertEqual(len(labels), len(set(labels)), "no duplicate buttons")

    def test_the_keyboard_keeps_the_engine_visible(self):
        # The engine button was added but got lost in the crowd; it now sits in
        # the top row so it is the first thing a user sees.
        self.assertEqual(bot.REPLY_KEYBOARD[0][1], "🤖 محرّك التحليل")
        self.assertEqual(bot.BUTTON_COMMANDS["🤖 محرّك التحليل"], "/engine")

    def test_the_keyboard_keeps_the_smc_engine_visible(self):
        # The SMC engine sits beside the AI engine in the top row, so the
        # operator can reach the SMC read with one tap.
        self.assertEqual(bot.REPLY_KEYBOARD[0][2], "📐 محرّك SMC")
        self.assertEqual(bot.BUTTON_COMMANDS["📐 محرّك SMC"], "/smc")

    def test_trimmed_buttons_still_work_when_typed(self):
        # Dropping a button must not drop the function: the Arabic phrase and
        # the plain command both keep working.
        router, _, _ = _paid_router()
        self.assertIn("XAUUSD", router.handle("9", "📊 حالة السوق"))
        self.assertIn("XAUUSD", router.handle("9", "/status"))

    def test_tapping_a_button_runs_its_command(self):
        router, _, _levels = _paid_router()
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
    def _check(self, value, was_high, news_reason=None):
        api = FakeVolApi([{"close": 1800.0, "high": 1801, "low": 1799}])
        subs = bot.Subscriptions(Path(tempfile.mkdtemp()) / "s.json")
        subs.add("42")
        client = FakeClient()
        new_state = bot._check_volatility(api, FakeModel(value), subs, client,
                                          was_high, news_reason)
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

    def test_news_reason_is_appended_to_the_alert(self):
        _, sent = self._check(0.80, was_high=False, news_reason="⏸ نافذة خبر: CPI")
        self.assertEqual(len(sent), 1)
        self.assertIn("نافذة خبر", sent[0][1])


class LevelNewsContextTests(unittest.TestCase):
    def _fire_once(self, news_context):
        api = FakeApi(HEALTH_ONLINE, SYSTEM, BRIDGE, ANALYSIS_EMPTY)
        levels = bot.LevelStore(Path(tempfile.mkdtemp()) / "l.json")
        levels.add("7", 4180.0, "above")
        client = FakeClient()
        api.last_price = lambda: 4170.0
        bot._check_levels(api, levels, None, client, news_context)  # learn side
        api.last_price = lambda: 4181.0
        bot._check_levels(api, levels, None, client, news_context)  # cross
        return client.sent

    def test_level_alert_carries_the_news_context(self):
        sent = self._fire_once("📰 آخر خبر مهم: CPI")
        self.assertEqual(len(sent), 1)
        self.assertIn("📰", sent[0][1])
        self.assertIn("تنبيه سعري", sent[0][1])

    def test_level_alert_without_news_is_unchanged(self):
        sent = self._fire_once(None)
        self.assertEqual(len(sent), 1)
        self.assertNotIn("📰", sent[0][1])


class NewsCommandTests(unittest.TestCase):
    """The /news reply: window word, schedule, tape, headlines, honesty."""

    NOW = 5_000_000.0

    def _digest(self):
        return {
            "engine": "news", "symbol": "XAUUSD",
            "window": {"state": "active", "reason": "⏸ نافذة خبر: CPI بعد ~5 دقيقة",
                       "event": {"title": "CPI", "country": "US", "in_minutes": 5}},
            "upcoming": [{"title": "NFP", "title_ar": "الوظائف", "country": "US",
                          "in_minutes": 30}],
            "released": [{"title": "TIC Flows", "title_ar": "تدفقات",
                          "country": "US", "minutes_ago": 12, "actual": 279.2,
                          "forecast": 75.2, "previous": -27.9, "surprise": "above",
                          "currency": "USD", "currency_dir": 1, "gold_dir": -1,
                          "certain": True},
                         {"title": "EU Trade", "title_ar": "تجارة أوروبا",
                          "country": "EU", "minutes_ago": 40, "actual": -16.1,
                          "forecast": None, "previous": 10.7, "surprise": "below",
                          "currency": "EUR", "currency_dir": -1, "gold_dir": 0,
                          "certain": False}],
            "headlines": [{"title": "Gold holds support", "source": "FOREX.com",
                           "published": ""}],
            "counts": {"upcoming": 1, "released": 2, "headlines": 1, "events": 9},
        }

    def test_digest_renders_the_window_and_the_tape(self):
        out = bot.format_news(self._digest())
        self.assertIn("نافذة خبر", out)
        self.assertIn("الوظائف", out)          # upcoming, Arabic title
        self.assertIn("تدفقات", out)            # released, Arabic title
        self.assertIn("279.2", out)             # the actual
        self.assertIn("ضغط على الذهب", out)     # US gold read is asserted
        self.assertIn("غير مباشر", out)         # EU read is not
        self.assertIn("Gold holds support", out)
        self.assertIn("لا يُدَّعى حافة", out)   # the honesty caveat

    def test_no_baseline_says_so_and_asserts_nothing(self):
        d = self._digest()
        d["released"] = [{"title": "Mystery", "title_ar": "غامض", "country": "US",
                          "minutes_ago": 5, "actual": 5.0, "forecast": None,
                          "previous": None, "surprise": "unknown", "currency": None,
                          "currency_dir": 0, "gold_dir": 0, "certain": False}]
        out = bot.format_news(d)
        self.assertIn("غير متوفرة", out)
        self.assertNotIn("ضغط على الذهب", out)

    def test_empty_digest_says_there_is_nothing(self):
        d = self._digest()
        d.update({"upcoming": [], "released": [], "headlines": [],
                  "window": {"state": "clear", "reason": None, "event": None}})
        out = bot.format_news(d)
        self.assertIn("خالٍ من الأخبار", out)
        self.assertIn("لا أخبار", out)

    def test_news_command_degrades_when_the_engine_is_absent(self):
        api = FakeApi(HEALTH_ONLINE, SYSTEM, BRIDGE, ANALYSIS_EMPTY)
        router, _ = RouterTests()._router(api)
        saved = bot.news_engine
        bot.news_engine = None
        try:
            out = router.handle("9", "/news")
        finally:
            bot.news_engine = saved
        self.assertIn("غير متاحة", out)

    def test_news_command_reads_the_digest(self):
        api = FakeApi(HEALTH_ONLINE, SYSTEM, BRIDGE, ANALYSIS_EMPTY)
        router, _ = RouterTests()._router(api)
        saved = bot.news_digest
        bot.news_digest = lambda *a, **k: self._digest()
        try:
            out = router.handle("9", "/news")
        finally:
            bot.news_digest = saved
        self.assertIn("نافذة خبر", out)

    def test_a_headline_source_is_not_printed_twice(self):
        d = self._digest()
        d["headlines"] = [{"title": "Gold holds support - FOREX.com",
                           "source": "FOREX.com", "published": ""}]
        out = bot.format_news(d)
        self.assertEqual(out.count("FOREX.com"), 1)

    def test_the_keyboard_offers_news(self):
        self.assertIn("📰 الأخبار والذهب", bot.BUTTON_COMMANDS)
        self.assertEqual(bot.BUTTON_COMMANDS["📰 الأخبار والذهب"], "/news")
        self.assertIn("/news", dict(bot.BOT_COMMANDS))

    def test_help_lists_the_news_command(self):
        api = FakeApi(HEALTH_ONLINE, SYSTEM, BRIDGE, ANALYSIS_EMPTY)
        router, _ = RouterTests()._router(api)
        self.assertIn("/news", router.handle("9", "/help"))


class ForecastCommandTests(unittest.TestCase):
    """The /forecast reply: the prep plan, and its refusal to call a side."""

    NOW = 5_000_000.0

    def _digest(self):
        return {
            "engine": "news-forecast", "symbol": "XAUUSD", "state": "prep",
            "event": {"title": "CPI", "title_ar": "التضخم", "country": "US",
                      "at_utc": int(self.NOW + 40 * 60), "in_minutes": 40,
                      "forecast": 3.1, "previous": 3.0, "importance": 1},
            "plan": {
                "event_family": "inflation", "event_family_ar": "تضخم",
                "in_minutes": 40, "direction": "unknown",
                "direction_note": "الاتجاه غير قابل للتنبؤ ميكانيكياً.",
                "expected_move": {"horizon_min": 60, "median": 3.8, "p90": 10.6,
                                  "family": "inflation", "family_known": True,
                                  "n": 83},
                "levels": {"price": 4000.0, "break_up": 4000.95,
                           "break_down": 3999.05, "stop_distance": 3.8,
                           "target_distance": 7.6, "rr": 2.0},
                "steps": ["قبل الصدور: حدّد نطاقاً.", "ارسم أمرَي إيقاف."],
                "invalidation": "إذا مرّ 60 دقيقة دون كسر، ألغِ الخطة.",
            },
            "blackout": False,
            "notes": ["الحجم متوقّع؛ الاتجاه لا."],
        }

    def test_digest_renders_the_plan_and_the_band(self):
        out = bot.news_forecast.format_forecast(self._digest())
        self.assertIn("التضخم", out)
        self.assertIn("المدى المتوقّع", out)
        self.assertIn("كسر صعودي", out)
        self.assertIn("كسر هبوطي", out)

    def test_digest_never_claims_a_direction(self):
        out = bot.news_forecast.format_forecast(self._digest())
        self.assertIn("الاتجاه غير متوقّع", out)
        self.assertNotIn("سيصعد", out)
        self.assertNotIn("سينزل", out)

    def test_clear_state_says_there_is_no_plan(self):
        d = self._digest()
        d["state"] = "clear"
        d["event"] = None
        d["plan"] = None
        out = bot.news_forecast.format_forecast(d)
        self.assertIn("ما في إصدار", out)

    def test_the_command_reads_the_forecast(self):
        api = FakeApi(HEALTH_ONLINE, SYSTEM, BRIDGE, ANALYSIS_EMPTY)
        router, _ = RouterTests()._router(api)
        saved = bot.news_forecast_now
        bot.news_forecast_now = lambda *a, **k: self._digest()
        try:
            out = router.handle("9", "/forecast")
        finally:
            bot.news_forecast_now = saved
        self.assertIn("خطة", out)

    def test_the_command_is_graceful_without_the_advisor(self):
        api = FakeApi(HEALTH_ONLINE, SYSTEM, BRIDGE, ANALYSIS_EMPTY)
        router, _ = RouterTests()._router(api)
        saved = bot.news_forecast
        bot.news_forecast = None
        try:
            out = router.handle("9", "/forecast")
        finally:
            bot.news_forecast = saved
        self.assertIn("غير متاح", out)

    def test_the_keyboard_offers_the_forecast(self):
        self.assertIn("🧭 توقّع الخبر", bot.BUTTON_COMMANDS)
        self.assertEqual(bot.BUTTON_COMMANDS["🧭 توقّع الخبر"], "/forecast")
        self.assertIn("/forecast", dict(bot.BOT_COMMANDS))

    def test_help_lists_the_forecast_command(self):
        api = FakeApi(HEALTH_ONLINE, SYSTEM, BRIDGE, ANALYSIS_EMPTY)
        router, _ = RouterTests()._router(api)
        self.assertIn("/forecast", router.handle("9", "/help"))


@unittest.skipUnless(bot.news_window is not None and bot.calendar_feed is not None,
                     "advisor calendar modules not importable")
class NewsWatchTests(unittest.TestCase):
    """The proactive news alerts: window-open and just-printed, once each."""

    T0 = 6_000_000.0

    def _ev(self, at, title="CPI", country="US", imp=1, actual=None,
            forecast=None, previous=None):
        return {"at_utc": at, "title": title, "title_ar": title,
                "country": country, "importance": imp, "actual": actual,
                "forecast": forecast, "previous": previous}

    def test_a_quiet_start_learns_nothing_and_alerts_nothing(self):
        watch = bot.NewsWatch()
        far = [self._ev(self.T0 + 3600)]
        self.assertEqual(watch.poll(far, self.T0), [])   # prime
        self.assertEqual(watch.poll(far, self.T0), [])   # still quiet

    def test_the_window_opening_alerts_once(self):
        watch = bot.NewsWatch()
        watch.poll([], self.T0)                                  # prime quiet
        soon = [self._ev(self.T0 + 600)]                         # 10 min ahead
        first = watch.poll(soon, self.T0)
        self.assertEqual(len(first), 1)
        self.assertIn("نافذة خبر", first[0])
        self.assertEqual(watch.poll(soon, self.T0), [])          # not twice

    def test_a_print_alerts_once_with_the_read(self):
        watch = bot.NewsWatch()
        watch.poll([], self.T0)                                  # prime quiet
        out = watch.poll([self._ev(self.T0 - 60, actual=3.2, forecast=3.0)],
                         self.T0)
        self.assertEqual(len(out), 1)
        self.assertIn("صدر للتو", out[0])
        self.assertIn("الذهب", out[0])

    def test_prime_suppresses_an_already_open_window(self):
        watch = bot.NewsWatch()
        open_now = [self._ev(self.T0)]                            # window open
        self.assertEqual(watch.poll(open_now, self.T0), [])       # prime
        self.assertEqual(watch.poll(open_now, self.T0), [])       # no replay

    def test_prime_suppresses_an_already_printed_release(self):
        watch = bot.NewsWatch()
        printed = [self._ev(self.T0 - 60, actual=3.2, forecast=3.0)]
        self.assertEqual(watch.poll(printed, self.T0), [])        # prime
        self.assertEqual(watch.poll(printed, self.T0), [])        # no replay

    def test_a_low_impact_print_does_not_alert(self):
        watch = bot.NewsWatch()
        watch.poll([], self.T0)
        low = [self._ev(self.T0 - 60, title="Minor", imp=-1, actual=1.0,
                        forecast=0.5)]
        self.assertEqual(watch.poll(low, self.T0), [])

    def test_missing_advisor_disables_the_watch_cleanly(self):
        watch = bot.NewsWatch()
        saved = bot.news_window
        bot.news_window = None
        try:
            self.assertEqual(watch.poll([self._ev(self.T0)], self.T0), [])
        finally:
            bot.news_window = saved


@unittest.skipUnless(bot.news_forecast is not None and bot.news_window is not None,
                     "advisor forecast modules not importable")
class ForecastWatchTests(unittest.TestCase):
    """The pre-news prep push: one plan per release, an hour out, never twice."""

    T0 = 6_000_000.0

    def _ev(self, at, title="US CPI", country="US", imp=1, actual=None,
            forecast=None, previous=None):
        return {"at_utc": at, "title": title, "title_ar": title,
                "country": country, "importance": imp, "actual": actual,
                "forecast": forecast, "previous": previous}

    def test_a_far_release_pushes_nothing(self):
        watch = bot.ForecastWatch()
        far = [self._ev(self.T0 + 4 * 3600)]
        self.assertEqual(watch.poll(far, self.T0, 4000.0), [])   # prime
        self.assertEqual(watch.poll(far, self.T0, 4000.0), [])

    def test_a_release_inside_the_horizon_pushes_a_plan_once(self):
        watch = bot.ForecastWatch()
        watch.poll([], self.T0, 4000.0)                          # prime quiet
        soon = [self._ev(self.T0 + 40 * 60)]                     # 40 min ahead
        first = watch.poll(soon, self.T0, 4000.0)
        self.assertEqual(len(first), 1)
        self.assertIn("تحضير قبل الخبر", first[0])
        self.assertEqual(watch.poll(soon, self.T0, 4000.0), [])  # not twice

    def test_prime_suppresses_a_release_already_in_range(self):
        watch = bot.ForecastWatch()
        soon = [self._ev(self.T0 + 40 * 60)]
        self.assertEqual(watch.poll(soon, self.T0, 4000.0), [])  # prime
        self.assertEqual(watch.poll(soon, self.T0, 4000.0), [])  # no replay

    def test_the_blackout_watch_keeps_a_print_it_owns(self):
        watch = bot.ForecastWatch()
        watch.poll([], self.T0, 4000.0)                          # prime quiet
        # 5 min ahead: inside the prep horizon and inside the blackout window.
        now_ev = [self._ev(self.T0 + 5 * 60)]
        self.assertEqual(watch.poll(now_ev, self.T0, 4000.0), [])

    def test_low_impact_is_never_pushed(self):
        watch = bot.ForecastWatch()
        watch.poll([], self.T0, 4000.0)
        low = [self._ev(self.T0 + 40 * 60, imp=0)]
        self.assertEqual(watch.poll(low, self.T0, 4000.0), [])

    def test_missing_advisor_disables_the_watch_cleanly(self):
        watch = bot.ForecastWatch()
        saved = bot.news_forecast
        bot.news_forecast = None
        try:
            self.assertEqual(watch.poll([self._ev(self.T0 + 600)], self.T0,
                                        4000.0), [])
        finally:
            bot.news_forecast = saved

    def test_the_push_carries_no_direction_claim(self):
        watch = bot.ForecastWatch()
        watch.poll([], self.T0, 4000.0)
        out = watch.poll([self._ev(self.T0 + 40 * 60)], self.T0, 4000.0)
        self.assertEqual(len(out), 1)
        self.assertIn("الاتجاه غير متوقّع", out[0])
        self.assertNotIn("سيصعد", out[0])
        self.assertNotIn("سينزل", out[0])


def _card(verdict="LONG", entry=2651.4, stop=2643.1, target=2667.9, rr=2.0,
          available=True, direction_tf="LONG", rsi=57.0, targets=None):
    """A trade card shaped exactly as the advisor's reply carries it."""
    inner = None if not available or verdict == "NEUTRAL" else {
        "direction": verdict, "entry": entry, "stop": stop,
        "target": target, "rr": rr, "agreement": "4/5",
    }
    if inner is not None and targets is not None:
        inner["targets"] = targets
    return {
        "available": available,
        "verdict": verdict,
        "agreement": {"long": 4, "short": 1, "neutral": 0, "of": 5},
        "timeframes": {tf: {"direction": direction_tf, "rsi": rsi}
                       for tf in ("M5", "M15", "M30", "H1", "H4")},
        "reason": "no majority across timeframes",
        "card": inner,
    }


class FakeCardApi(FakeApi):
    """A fake backend whose advisor returns a canned trade card."""

    def __init__(self, card, available=True):
        super().__init__(HEALTH_ONLINE, SYSTEM, BRIDGE, ANALYSIS_EMPTY,
                         available=available)
        self._card = card

    def trade_card(self):
        self._maybe_raise()
        return self._card


class TradeCardFormatTests(unittest.TestCase):
    def test_card_carries_the_exact_levels_and_direction(self):
        text = bot.format_trade_card(_card())
        self.assertIn("شراء", text)
        self.assertIn("الدخول: 2651.4", text)
        self.assertIn("الوقف: 2643.1", text)
        self.assertIn("الهدف: 2667.9", text)
        self.assertIn("R:R = 2.0", text)

    def test_card_states_it_is_not_advice_and_does_not_execute(self):
        text = bot.format_trade_card(_card())
        self.assertIn("ليست نصيحة", text)
        self.assertIn("لا تنفيذ", text)

    def test_a_poor_reward_risk_is_surfaced_not_hidden(self):
        text = bot.format_trade_card(_card(rr=0.8))
        self.assertIn("أقل من 1", text)

    def test_a_good_reward_risk_carries_no_warning(self):
        self.assertNotIn("أقل من 1", bot.format_trade_card(_card(rr=2.0)))

    def test_the_per_timeframe_read_is_shown(self):
        text = bot.format_trade_card(_card())
        self.assertIn("M15", text)
        self.assertIn("▲", text)

    def test_a_scaled_ladder_is_pushed_as_one_list(self):
        targets = [2660.0, 2664.0, 2668.0, 2672.0, 2676.0]
        text = bot.format_trade_card(_card(target=2676.0, targets=targets))
        self.assertIn("الأهداف (5)", text)
        for t in targets:
            self.assertIn(str(t), text)

    def test_a_single_target_still_renders_as_before(self):
        # Older cards (no ladder) must keep their exact wording.
        text = bot.format_trade_card(_card())
        self.assertIn("الهدف: 2667.9", text)
        self.assertNotIn("الأهداف", text)


class TradeCardWatchTests(unittest.TestCase):
    def test_first_poll_primes_and_stays_silent(self):
        watch = bot.TradeCardWatch()
        self.assertEqual(watch.poll(_card()), [])

    def test_a_ladder_only_change_is_news(self):
        # The stop holds, but the whole ladder shifts by most of a risk: the
        # targets are what a trader acts on, so it is announced.
        watch = bot.TradeCardWatch()
        watch.poll(_card(targets=[2660.0, 2664.0, 2668.0, 2672.0, 2676.0]))
        out = watch.poll(_card(targets=[2668.0, 2672.0, 2676.0, 2680.0, 2684.0]))
        self.assertEqual(len(out), 1)

    def test_a_ladder_nudge_stays_quiet(self):
        watch = bot.TradeCardWatch()
        watch.poll(_card(targets=[2660.0, 2664.0, 2668.0, 2672.0, 2676.0]))
        self.assertEqual(
            watch.poll(_card(targets=[2660.1, 2664.1, 2668.1, 2672.1, 2676.1])), [])

    def test_a_new_card_alerts_once(self):
        watch = bot.TradeCardWatch()
        watch.poll(_card())                       # prime on the current card
        out = watch.poll(_card(entry=2660.0, stop=2650.0, target=2680.0))
        self.assertEqual(len(out), 1)
        self.assertIn("2660.0", out[0])

    def test_the_same_card_never_re_alerts(self):
        watch = bot.TradeCardWatch()
        watch.poll(_card())
        self.assertEqual(watch.poll(_card()), [])
        self.assertEqual(watch.poll(_card()), [])

    def test_a_neutral_read_never_alerts(self):
        watch = bot.TradeCardWatch()
        watch.poll(_card(verdict="NEUTRAL", available=False))
        self.assertEqual(watch.poll(_card(verdict="NEUTRAL", available=False)), [])

    def test_flipping_direction_alerts_even_at_the_same_entry(self):
        watch = bot.TradeCardWatch()
        watch.poll(_card(verdict="LONG"))
        out = watch.poll(_card(verdict="SHORT"))
        self.assertEqual(len(out), 1)
        self.assertIn("بيع", out[0])

    def test_a_missing_card_is_never_announced(self):
        watch = bot.TradeCardWatch()
        watch.poll(_card())
        self.assertEqual(watch.poll(None), [])

    def test_a_burst_of_re_reads_stays_silent(self):
        # Every closed M5 bar nudges the entry, and the stop drifts with it by
        # cents. That is one setup being re-read, not a burst of new ones, so
        # none of it is announced.
        watch = bot.TradeCardWatch()
        watch.poll(_card(entry=2651.4, stop=2643.1, target=2667.9))
        for i in range(1, 10):
            self.assertEqual(
                watch.poll(_card(entry=2651.4 + i * 0.1,
                                 stop=2643.1 + i * 0.1,
                                 target=2667.9 + i * 0.1)), [])

    def test_a_cent_move_is_not_news(self):
        # The entry is the freshest M5 close: it drifts by cents, and the stop
        # moves with it. That is the same setup, not a new one.
        watch = bot.TradeCardWatch()
        watch.poll(_card(entry=2651.4, stop=2643.1, target=2667.9))
        self.assertEqual(
            watch.poll(_card(entry=2651.9, stop=2643.6, target=2668.4)), [])

    def test_a_real_structure_move_is_news(self):
        # The stop lifts by most of the risk: the setup genuinely changed, so it
        # is announced even though the direction did not.
        watch = bot.TradeCardWatch()
        watch.poll(_card(entry=2651.4, stop=2643.1, target=2667.9))  # risk 8.3
        out = watch.poll(_card(entry=2655.0, stop=2650.0, target=2675.0))
        self.assertEqual(len(out), 1)

    def test_a_move_just_under_the_threshold_stays_quiet(self):
        watch = bot.TradeCardWatch(material=0.5)
        watch.poll(_card(entry=2651.4, stop=2643.1, target=2667.9))  # risk 8.3
        # The stop lifts 3.3 against an 8.3 risk: 0.40R, under the threshold.
        self.assertEqual(
            watch.poll(_card(entry=2654.7, stop=2646.4, target=2671.2)), [])

    def test_the_materiality_threshold_is_configurable(self):
        watch = bot.TradeCardWatch(material=0.0)
        watch.poll(_card(entry=2651.4, stop=2643.1, target=2667.9))
        out = watch.poll(_card(entry=2651.9, stop=2643.6, target=2668.4))
        self.assertEqual(len(out), 1)

    def test_a_change_after_the_cooldown_alerts_again(self):
        watch = bot.TradeCardWatch(cooldown=600)
        watch._last_alert = time.time() - 601  # the window has elapsed
        watch.poll(_card())
        out = watch.poll(_card(entry=2651.4, stop=2650.0, target=2675.0))
        self.assertEqual(len(out), 1)

    def test_the_clock_is_only_advanced_by_a_real_alert(self):
        # A neutral or unchanged read must not consume the cooldown, or a real
        # change arriving right after one would be swallowed.
        watch = bot.TradeCardWatch(cooldown=600)
        watch.poll(_card())
        self.assertEqual(watch._last_alert, 0.0)
        watch.poll(_card())  # unchanged
        self.assertEqual(watch._last_alert, 0.0)

    def test_the_cooldown_is_configurable(self):
        watch = bot.TradeCardWatch(cooldown=0)
        watch.poll(_card())
        out = watch.poll(_card(entry=2660.0, stop=2650.0, target=2680.0))
        self.assertEqual(len(out), 1)

    def test_a_reversal_bypasses_the_cooldown(self):
        # A direction flip is the one change worth an immediate push, so it must
        # not be swallowed by a window opened moments earlier.
        watch = bot.TradeCardWatch(cooldown=900)
        watch.poll(_card(verdict="LONG"))
        first = watch.poll(_card(verdict="LONG", entry=2660.0, stop=2650.0,
                                 target=2680.0))
        self.assertEqual(len(first), 1)
        # Still inside the window: a level tweak stays silent...
        self.assertEqual(watch.poll(_card(verdict="LONG", entry=2665.0,
                                          stop=2655.0, target=2685.0)), [])
        # ...but a reversal is pushed at once.
        flip = watch.poll(_card(verdict="SHORT", entry=2660.0, stop=2670.0,
                                target=2640.0))
        self.assertEqual(len(flip), 1)
        self.assertIn("بيع", flip[0])


class TradeCardCheckTests(unittest.TestCase):
    def _check(self, card, news_context=None):
        api = FakeCardApi(card)
        subs = bot.Subscriptions(Path(tempfile.mkdtemp()) / "s.json")
        subs.add("42")
        client = FakeClient()
        watch = bot.TradeCardWatch()
        # Prime on the opposite direction, so `card` is genuinely new and the
        # test exercises the broadcast path rather than the materiality rule.
        watch.poll(_card(verdict="SHORT"))
        bot._check_trade_card(api, watch, subs, client, news_context)
        return client.sent

    def test_a_new_card_is_broadcast_to_subscribers(self):
        sent = self._check(_card(entry=2660.0, stop=2650.0, target=2680.0))
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], "42")
        self.assertIn("2660.0", sent[0][1])

    def test_an_unreachable_advisor_stays_silent(self):
        api = FakeCardApi(_card(), available=False)
        subs = bot.Subscriptions(Path(tempfile.mkdtemp()) / "s.json")
        subs.add("42")
        client = FakeClient()
        bot._check_trade_card(api, bot.TradeCardWatch(), subs, client)
        self.assertEqual(client.sent, [])

    def test_news_context_is_appended_to_a_fresh_card(self):
        sent = self._check(_card(entry=2660.0), news_context="⏸ نافذة خبر: CPI")
        self.assertIn("نافذة خبر", sent[0][1])


class CardCommandTests(unittest.TestCase):
    def _router(self, card, available=True):
        api = FakeCardApi(card, available)
        subs = bot.Subscriptions(Path(tempfile.mkdtemp()) / "s.json")
        return bot.CommandRouter(api, subs, None), api

    def test_card_command_renders_the_levels(self):
        router, _ = self._router(_card())
        reply = router.handle("1", "/card")
        self.assertIn("2651.4", reply)
        self.assertIn("R:R", reply)

    def test_card_button_runs_the_command(self):
        router, _ = self._router(_card())
        self.assertIn("2651.4", router.handle("1", "🎯 بطاقة الصفقة"))

    def test_card_command_reports_no_trade_honestly(self):
        router, _ = self._router(_card(verdict="NEUTRAL"))
        reply = router.handle("1", "/card")
        self.assertIn("لا صفقة", reply)
        self.assertNotIn("الدخول", reply)

    def test_card_command_degrades_when_the_advisor_is_down(self):
        router, _ = self._router(_card(), available=False)
        self.assertIn("تعذّر", router.handle("1", "/card"))

    def test_help_lists_the_card_command(self):
        self.assertIn("/card", bot.CommandRouter.HELP)

    def test_the_menu_lists_the_card_command(self):
        self.assertIn("/card", dict(bot.BOT_COMMANDS))


def _engine(configured=True, rate_limited=False, bias="LONG",
            market_read="الفريمات متوافقة صعودًا.", trades=None,
            key_event="", analysis=True, provider="mistral", model="m"):
    """An engine envelope shaped exactly as ai_engine.py emits it."""
    if trades is None:
        trades = [{
            "direction": "LONG", "entry": 4122.5, "stop": 4110.0,
            "targets": [4135.0, 4148.0], "target": 4148.0,
            "risk": 12.5, "reward": 25.5, "reward_risk": 2.04,
            "win_rate": 0.58, "rationale": "امتداد الاتجاه",
            "timeframe": "H1", "win_rate_validated": False,
            "anchor": {"anchored": True, "note": ""},
        }]
    env = {
        "schema_version": "1.0", "symbol": "XAUUSD", "validated": False,
        "source": "ai-engine (isolated)", "provider": provider,
        "configured": configured, "model": model if configured else None,
        "snapshot": {"price": 4122.0, "timeframes": {}, "rule_card": None},
        "analysis": None,
    }
    if rate_limited:
        env["rate_limited"] = True
        env["note"] = "AI engine paused: the provider's daily quota is spent."
    elif configured and analysis:
        env["analysis"] = {
            "market_read": market_read, "bias": bias, "trades": trades,
            "rejected": [], "news_used": False, "key_event": key_event,
        }
        env["note"] = "AI analysis engine output."
    return env


class FakeEngineApi(FakeApi):
    """A fake backend whose engine returns a canned read (or is unreachable)."""

    def __init__(self, engine, available=True):
        super().__init__(HEALTH_ONLINE, SYSTEM, BRIDGE, ANALYSIS_EMPTY,
                         available=available)
        self._engine = engine

    def engine_read(self, refresh=False):
        self._maybe_raise()
        return self._engine


def _smc(configured=True, rate_limited=False, bias="LONG",
         market_read="بنية صاعدة مع مسح سيولة أسفل القاع.", plans=None,
         key_event="", news_caution=False, analysis=True, provider="mistral",
         model="m"):
    """An SMC envelope shaped exactly as smc_engine.py emits it."""
    if plans is None:
        plans = [{
            "direction": "LONG", "entry": 4122.5, "stop": 4110.0,
            "targets": [4135.0, 4148.0], "target": 4148.0,
            "risk": 12.5, "reward": 25.5, "reward_risk": 2.04,
            "confidence": 0.58, "rationale": "CHoCH صاعد",
            "sweep": "قاع متساوٍ عند 4112", "poi": "بلوك أوامر عند 4115",
            "confidence_validated": False,
        }]
    env = {
        "schema_version": "1.0", "symbol": "XAUUSD", "validated": False,
        "source": "smc-engine (isolated)", "provider": provider,
        "framework": "SMC/ICT",
        "configured": configured, "model": model if configured else None,
        "snapshot": {"price": 4122.0, "facts": {}},
        "analysis": None,
    }
    if rate_limited:
        env["rate_limited"] = True
        env["note"] = "SMC engine paused: the provider's daily quota is spent."
    elif configured and analysis:
        env["analysis"] = {
            "market_read": market_read, "bias": bias, "plans": plans,
            "rejected": [], "news_caution": news_caution, "key_event": key_event,
        }
        env["note"] = "SMC/ICT analysis engine output."
    return env


class FakeSmcApi(FakeApi):
    """A fake backend whose SMC engine returns a canned read (or is down)."""

    def __init__(self, smc, available=True):
        super().__init__(HEALTH_ONLINE, SYSTEM, BRIDGE, ANALYSIS_EMPTY,
                         available=available)
        self._smc = smc

    def smc_read(self, refresh=False):
        self._maybe_raise()
        return self._smc


class EngineFormatTests(unittest.TestCase):
    def test_a_trade_carries_the_exact_levels_and_direction(self):
        text = bot.format_engine(_engine())
        self.assertIn("شراء", text)
        self.assertIn("4122.5", text)
        self.assertIn("4110", text)
        self.assertIn("4135", text)
        self.assertIn("4148", text)

    def test_the_win_rate_is_labelled_as_the_model_estimate(self):
        # The engine stamps win_rate_validated: false; the reply must not read
        # as a measured probability.
        text = bot.format_engine(_engine())
        self.assertIn("تقدير الموديل", text)
        self.assertIn("58%", text)

    def test_the_shadow_disclaimer_always_closes_a_live_read(self):
        text = bot.format_engine(_engine())
        self.assertIn("وضع الظل", text)
        self.assertIn("لا تنفيذ", text)

    def test_a_neutral_read_reports_no_trades_honestly(self):
        text = bot.format_engine(_engine(bias="NEUTRAL", trades=[]))
        self.assertIn("حياد", text)
        self.assertIn("لا صفقات", text)
        self.assertNotIn("دخول", text)

    def test_a_scaled_ladder_renders_every_target(self):
        trades = [{"direction": "SHORT", "entry": 4150.0, "stop": 4162.0,
                   "targets": [4138.0, 4126.0, 4114.0], "target": 4114.0,
                   "reward_risk": 3.0, "win_rate": 0.5, "timeframe": "H4"}]
        text = bot.format_engine(_engine(bias="SHORT", trades=trades))
        for level in ("4138", "4126", "4114"):
            self.assertIn(level, text)

    def test_an_unconfigured_engine_says_what_to_set(self):
        text = bot.format_engine(_engine(configured=False, analysis=False))
        self.assertIn("غير مفعّل", text)
        self.assertIn("وضع الظل", text)

    def test_a_paused_engine_reports_the_spent_quota(self):
        text = bot.format_engine(_engine(rate_limited=True, analysis=False))
        self.assertIn("استُهلكت", text)

    def test_a_missing_engine_is_reported_plainly(self):
        self.assertIn("غير متاح", bot.format_engine({}))

    def test_the_key_event_is_surfaced_when_present(self):
        text = bot.format_engine(_engine(key_event="قرار الفائدة 20:00"))
        self.assertIn("قرار الفائدة", text)

    def test_a_missing_level_renders_as_a_dash_not_a_crash(self):
        trades = [{"direction": "LONG", "entry": None, "stop": None,
                   "targets": [], "target": None, "reward_risk": None,
                   "win_rate": None, "timeframe": "M15"}]
        text = bot.format_engine(_engine(trades=trades))
        self.assertIn("—", text)


class EngineCommandTests(unittest.TestCase):
    def _router(self, engine, available=True):
        api = FakeEngineApi(engine, available)
        subs = bot.Subscriptions(Path(tempfile.mkdtemp()) / "s.json")
        return bot.CommandRouter(api, subs, None), api

    def test_engine_command_renders_the_read(self):
        router, _ = self._router(_engine())
        reply = router.handle("1", "/engine")
        self.assertIn("4122.5", reply)
        self.assertIn("وضع الظل", reply)

    def test_engine_button_runs_the_command(self):
        router, _ = self._router(_engine())
        self.assertIn("4122.5", router.handle("1", "🤖 محرّك التحليل"))

    def test_engine_command_degrades_when_the_engine_is_down(self):
        router, _ = self._router(_engine(), available=False)
        reply = router.handle("1", "/engine")
        self.assertIn("غير متاح", reply)
        self.assertIn("ASTRA_ENABLE_ENGINE", reply)

    def test_help_lists_the_engine_command(self):
        self.assertIn("/engine", bot.CommandRouter.HELP)

    def test_the_menu_lists_the_engine_command(self):
        self.assertIn("/engine", dict(bot.BOT_COMMANDS))


class SmcFormatTests(unittest.TestCase):
    def test_a_plan_carries_the_exact_levels_and_direction(self):
        text = bot.format_smc(_smc())
        self.assertIn("شراء", text)
        self.assertIn("4122.5", text)
        self.assertIn("4110", text)
        self.assertIn("4135", text)
        self.assertIn("4148", text)

    def test_the_confidence_is_labelled_as_the_model_estimate(self):
        # The engine stamps confidence_validated: false; the reply must not
        # read as a measured probability.
        text = bot.format_smc(_smc())
        self.assertIn("تقدير الموديل", text)
        self.assertIn("58%", text)

    def test_the_sweep_and_point_of_interest_are_surfaced(self):
        text = bot.format_smc(_smc())
        self.assertIn("السيولة الممسوحة", text)
        self.assertIn("قاع متساوٍ", text)
        self.assertIn("منطقة الاهتمام", text)
        self.assertIn("بلوك أوامر", text)

    def test_the_shadow_disclaimer_always_closes_a_live_read(self):
        text = bot.format_smc(_smc())
        self.assertIn("وضع الظل", text)
        self.assertIn("لا تنفيذ", text)

    def test_a_neutral_read_reports_no_plans_honestly(self):
        text = bot.format_smc(_smc(bias="NEUTRAL", plans=[]))
        self.assertIn("حياد", text)
        self.assertIn("لا إعدادات", text)
        self.assertNotIn("دخول", text)

    def test_a_scaled_ladder_renders_every_target(self):
        plans = [{"direction": "SHORT", "entry": 4150.0, "stop": 4162.0,
                  "targets": [4138.0, 4126.0, 4114.0], "target": 4114.0,
                  "reward_risk": 3.0, "confidence": 0.5}]
        text = bot.format_smc(_smc(bias="SHORT", plans=plans))
        for level in ("4138", "4126", "4114"):
            self.assertIn(level, text)

    def test_an_unconfigured_engine_says_what_to_set(self):
        text = bot.format_smc(_smc(configured=False, analysis=False))
        self.assertIn("غير مفعّل", text)
        self.assertIn("وضع الظل", text)

    def test_a_paused_engine_reports_the_spent_quota(self):
        text = bot.format_smc(_smc(rate_limited=True, analysis=False))
        self.assertIn("استُهلكت", text)

    def test_a_missing_engine_is_reported_plainly(self):
        self.assertIn("غير متاح", bot.format_smc({}))

    def test_the_news_caution_is_surfaced_when_present(self):
        text = bot.format_smc(_smc(news_caution=True,
                                   key_event="قرار الفائدة 20:00"))
        self.assertIn("حذر إخباري", text)
        self.assertIn("قرار الفائدة", text)

    def test_a_missing_level_renders_as_a_dash_not_a_crash(self):
        plans = [{"direction": "LONG", "entry": None, "stop": None,
                  "targets": [], "target": None, "reward_risk": None,
                  "confidence": None}]
        text = bot.format_smc(_smc(plans=plans))
        self.assertIn("—", text)

    def test_the_rationale_is_shown_so_the_level_has_a_reason(self):
        # The app has shown the model's rationale since the beginning; the bot
        # had not, so a phone user saw the levels without why they exist.
        text = bot.format_smc(_smc())
        self.assertIn("السبب", text)
        self.assertIn("CHoCH صاعد", text)

    def test_the_verified_anchor_names_the_zone_and_frame(self):
        plans = [{"direction": "LONG", "entry": 4122.5, "stop": 4110.0,
                  "targets": [4135.0, 4148.0], "target": 4148.0,
                  "reward_risk": 2.04, "confidence": 0.58,
                  "anchored": True, "anchor_zone": "order block (bullish)",
                  "anchor_timeframe": "M15"}]
        text = bot.format_smc(_smc(plans=plans))
        self.assertIn("مرساة الدخول", text)
        self.assertIn("order block (bullish)", text)
        self.assertIn("M15", text)

    def test_an_unanchored_plan_shows_no_anchor_line(self):
        plans = [{"direction": "LONG", "entry": 4122.5, "stop": 4110.0,
                  "targets": [4135.0], "target": 4135.0, "reward_risk": 2.0,
                  "confidence": 0.5}]
        self.assertNotIn("مرساة الدخول", bot.format_smc(_smc(plans=plans)))

    def test_the_distance_from_price_is_shown_with_its_side(self):
        plans = [{"direction": "LONG", "entry": 4122.5, "stop": 4110.0,
                  "targets": [4135.0], "target": 4135.0, "reward_risk": 2.0,
                  "confidence": 0.5, "distance_from_price": -5.5}]
        text = bot.format_smc(_smc(plans=plans))
        self.assertIn("يبعد", text)
        self.assertIn("5.5", text)
        self.assertIn("أدنى", text)

    def test_a_rejected_plan_is_shown_with_its_reason(self):
        # A bare "no setups" cannot be told apart from "a setup was proposed and
        # failed the checks" unless the rejection is surfaced.
        smc = _smc()
        smc["analysis"]["rejected"] = [
            {"plan": {"direction": "LONG"}, "reason": "reward-to-risk below min"}]
        text = bot.format_smc(smc)
        self.assertIn("مرفوضة", text)
        self.assertIn("reward-to-risk below min", text)

    def test_no_rejected_section_when_nothing_was_rejected(self):
        self.assertNotIn("مرفوضة", bot.format_smc(_smc()))


class SmcCommandTests(unittest.TestCase):
    def _router(self, smc, available=True):
        api = FakeSmcApi(smc, available)
        subs = bot.Subscriptions(Path(tempfile.mkdtemp()) / "s.json")
        return bot.CommandRouter(api, subs, None), api

    def test_smc_command_renders_the_read(self):
        router, _ = self._router(_smc())
        reply = router.handle("1", "/smc")
        self.assertIn("4122.5", reply)
        self.assertIn("وضع الظل", reply)

    def test_smc_button_runs_the_command(self):
        router, _ = self._router(_smc())
        self.assertIn("4122.5", router.handle("1", "📐 محرّك SMC"))

    def test_smc_command_degrades_when_the_engine_is_down(self):
        router, _ = self._router(_smc(), available=False)
        reply = router.handle("1", "/smc")
        self.assertIn("غير متاح", reply)
        self.assertIn("ASTRA_ENABLE_SMC", reply)

    def test_help_lists_the_smc_command(self):
        self.assertIn("/smc", bot.CommandRouter.HELP)

    def test_the_menu_lists_the_smc_command(self):
        self.assertIn("/smc", dict(bot.BOT_COMMANDS))


class CommandMenuPayloadTest(unittest.TestCase):
    """Telegram rejects a command name that carries the leading slash with
    BOT_COMMAND_INVALID and drops the entire list, so the menu stays empty in
    the app while every command still works when typed. The registered payload
    must carry the bare names."""

    def test_registered_names_have_no_slash(self):
        client = bot.TelegramClient("tok")
        captured = {}

        def fake_call(method, payload, timeout):
            captured.update(method=method, payload=payload)
            return {"ok": True}

        client._call = fake_call
        client.set_my_commands()
        self.assertEqual(captured["method"], "setMyCommands")
        commands = captured["payload"]["commands"]
        self.assertEqual(len(commands), len(bot.BOT_COMMANDS))
        for entry in commands:
            self.assertNotIn("/", entry["command"],
                             "Telegram would reject this name")
            self.assertRegex(entry["command"], r"^[a-z0-9_]{1,32}$")


class SmcAlertFormatTests(unittest.TestCase):
    """The pushed alert is compact but must not drop the honesty contract."""

    def test_the_alert_carries_the_levels_and_the_direction(self):
        smc = _smc()
        text = bot.format_smc_alert(smc, smc["analysis"]["plans"][0])
        self.assertIn("شراء", text)
        self.assertIn("4122.5", text)
        self.assertIn("4110", text)
        self.assertIn("4148", text)

    def test_the_alert_labels_the_confidence_as_an_estimate(self):
        smc = _smc()
        text = bot.format_smc_alert(smc, smc["analysis"]["plans"][0])
        self.assertIn("58%", text)
        self.assertIn("تقدير", text)

    def test_the_alert_states_shadow_mode_and_no_execution(self):
        smc = _smc()
        text = bot.format_smc_alert(smc, smc["analysis"]["plans"][0])
        self.assertIn("وضع الظل", text)
        self.assertIn("لا تنفيذ", text)

    def test_the_alert_carries_the_news_caution_when_set(self):
        smc = _smc(news_caution=True, key_event="CPI")
        text = bot.format_smc_alert(smc, smc["analysis"]["plans"][0])
        self.assertIn("حذر إخباري", text)
        self.assertIn("CPI", text)

    def test_the_alert_carries_the_reason_the_setup_exists(self):
        # A pushed level without a reason is a number the reader cannot judge.
        smc = _smc()
        text = bot.format_smc_alert(smc, smc["analysis"]["plans"][0])
        self.assertIn("السبب", text)
        self.assertIn("CHoCH صاعد", text)

    def test_the_alert_omits_the_reason_line_when_there_is_none(self):
        plan = {"direction": "LONG", "entry": 4122.5, "stop": 4110.0,
                "targets": [4135.0], "reward_risk": 2.0, "confidence": 0.5}
        text = bot.format_smc_alert(_smc(), plan)
        self.assertNotIn("السبب", text)

    def test_a_short_plan_is_labelled_as_a_sale(self):
        plans = [{"direction": "SHORT", "entry": 4200.0, "stop": 4215.0,
                  "targets": [4170.0], "target": 4170.0, "risk": 15.0,
                  "reward": 30.0, "reward_risk": 2.0, "confidence": 0.7}]
        smc = _smc(bias="SHORT", plans=plans)
        text = bot.format_smc_alert(smc, plans[0])
        self.assertIn("بيع", text)
        self.assertIn("70%", text)


class SmcWatchTests(unittest.TestCase):
    def _plan(self, direction="LONG", entry=4122.5, stop=4110.0,
              targets=(4135.0, 4148.0), confidence=0.58):
        return {"direction": direction, "entry": entry, "stop": stop,
                "targets": list(targets), "target": list(targets)[-1],
                "risk": abs(entry - stop),
                "reward": abs(list(targets)[-1] - entry),
                "reward_risk": 2.0, "confidence": confidence}

    def _read(self, *plans, bias="LONG", news_caution=False, key_event=""):
        return _smc(bias=bias, plans=list(plans) or None,
                    news_caution=news_caution, key_event=key_event)

    def test_first_poll_primes_and_stays_silent(self):
        watch = bot.SmcWatch()
        self.assertEqual(watch.poll(self._read(self._plan())), [])

    def test_a_confident_new_setup_is_pushed_once(self):
        watch = bot.SmcWatch()
        watch.poll(self._read(self._plan(entry=4122.5, stop=4110.0)))
        out = watch.poll(self._read(self._plan(entry=4200.0, stop=4180.0,
                                               targets=(4240.0,))))
        self.assertEqual(len(out), 1)
        self.assertIn("4200", out[0])

    def test_the_same_setup_never_re_alerts(self):
        watch = bot.SmcWatch()
        watch.poll(self._read(self._plan()))
        self.assertEqual(watch.poll(self._read(self._plan())), [])
        self.assertEqual(watch.poll(self._read(self._plan())), [])

    def test_a_plan_below_the_confidence_floor_is_never_pushed(self):
        watch = bot.SmcWatch(min_confidence=0.6)
        watch.poll(self._read(self._plan()))
        out = watch.poll(self._read(self._plan(entry=4200.0, stop=4180.0,
                                               targets=(4240.0,),
                                               confidence=0.55)))
        self.assertEqual(out, [])

    def test_an_unscored_plan_is_never_pushed(self):
        watch = bot.SmcWatch()
        plan = self._plan(entry=4200.0, stop=4180.0, targets=(4240.0,))
        del plan["confidence"]
        watch.poll(self._read(self._plan()))
        self.assertEqual(watch.poll(self._read(plan)), [])

    def test_a_missing_analysis_is_never_announced(self):
        watch = bot.SmcWatch()
        watch.poll(self._read(self._plan()))
        self.assertEqual(watch.poll(_smc(configured=False, analysis=False)), [])
        self.assertEqual(watch.poll(_smc(rate_limited=True, analysis=False)), [])
        self.assertEqual(watch.poll({}), [])
        self.assertEqual(watch.poll(None), [])

    def test_a_cent_move_is_not_news(self):
        # The entry is the freshest close: it drifts by cents and the stop moves
        # with it. That is the same setup being re-read, not a new one.
        watch = bot.SmcWatch(material=0.5)
        watch.poll(self._read(self._plan(entry=4122.5, stop=4110.0)))
        self.assertEqual(
            watch.poll(self._read(self._plan(entry=4122.9, stop=4110.4))), [])

    def test_a_moved_structure_is_news(self):
        watch = bot.SmcWatch(material=0.5)
        watch.poll(self._read(self._plan(entry=4122.5, stop=4110.0,
                                         targets=(4135.0, 4148.0))))
        out = watch.poll(self._read(self._plan(entry=4122.5, stop=4120.0,
                                               targets=(4160.0, 4180.0))))
        self.assertEqual(len(out), 1)

    def test_a_reversal_is_pushed_at_once(self):
        watch = bot.SmcWatch(material=0.9)
        watch.poll(self._read(self._plan()))
        out = watch.poll(self._read(
            self._plan(direction="SHORT", entry=4122.5, stop=4130.0,
                       targets=(4100.0,)), bias="SHORT"))
        self.assertEqual(len(out), 1)
        self.assertIn("بيع", out[0])

    def test_the_highest_confidence_plan_is_the_one_pushed(self):
        watch = bot.SmcWatch(min_confidence=0.5)
        watch.poll(self._read(self._plan(confidence=0.55)))
        low = self._plan(entry=4100.0, stop=4090.0, targets=(4130.0,),
                         confidence=0.55)
        high = self._plan(entry=4200.0, stop=4180.0, targets=(4260.0,),
                          confidence=0.9)
        out = watch.poll(self._read(low, high))
        self.assertEqual(len(out), 1)
        self.assertIn("4200", out[0])
        self.assertIn("90%", out[0])

    def test_a_lone_low_confidence_plan_does_not_block_a_high_one(self):
        watch = bot.SmcWatch(min_confidence=0.5)
        watch.poll(self._read(self._plan(confidence=0.55)))
        weak = self._plan(entry=4100.0, stop=4090.0, targets=(4130.0,),
                          confidence=0.2)
        strong = self._plan(entry=4200.0, stop=4180.0, targets=(4260.0,),
                            confidence=0.8)
        out = watch.poll(self._read(weak, strong))
        self.assertEqual(len(out), 1)
        self.assertIn("4200", out[0])

    def test_due_is_false_before_the_interval_and_true_after(self):
        watch = bot.SmcWatch(interval=900.0)
        now = 1_000_000.0
        self.assertTrue(watch.due(now))
        watch.mark_read(now)
        self.assertFalse(watch.due(now + 100))
        self.assertFalse(watch.due(now + 899))
        self.assertTrue(watch.due(now + 900))

    def test_a_burst_of_re_reads_stays_silent(self):
        watch = bot.SmcWatch(material=0.5)
        watch.poll(self._read(self._plan(entry=4122.5, stop=4110.0,
                                         targets=(4135.0, 4148.0))))
        for i in range(1, 8):
            self.assertEqual(
                watch.poll(self._read(self._plan(
                    entry=4122.5 + i * 0.1, stop=4110.0 + i * 0.1,
                    targets=(4135.0 + i * 0.1, 4148.0 + i * 0.1)))), [])


class SmcMonitorTests(unittest.TestCase):
    """The monitor asks the engine only when the watch says it is due."""

    def test_the_engine_is_read_at_most_once_per_interval(self):
        smc = _smc()
        api = FakeSmcApi(smc)
        calls = {"n": 0}
        original = api.smc_read

        def counting_smc_read(refresh=False):
            calls["n"] += 1
            return original(refresh=refresh)

        api.smc_read = counting_smc_read
        subs = bot.Subscriptions(Path(tempfile.mkdtemp()) / "subs.json")
        sent = []

        class FakeClient:
            def send_message(self, chat_id, text, keyboard=False):
                sent.append(text)

        watch = bot.SmcWatch(interval=3600.0)
        subs.add("1")
        bot._check_smc(api, watch, subs, FakeClient())  # type: ignore[arg-type]
        bot._check_smc(api, watch, subs, FakeClient())  # type: ignore[arg-type]
        self.assertEqual(calls["n"], 1)

    def test_an_unreachable_engine_is_silent(self):
        api = FakeSmcApi(_smc(), available=False)
        subs = bot.Subscriptions(Path(tempfile.mkdtemp()) / "subs.json")
        sent = []

        class FakeClient:
            def send_message(self, chat_id, text, keyboard=False):
                sent.append(text)

        watch = bot.SmcWatch(interval=0.0)
        subs.add("1")
        bot._check_smc(api, watch, subs, FakeClient())  # type: ignore[arg-type]
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
