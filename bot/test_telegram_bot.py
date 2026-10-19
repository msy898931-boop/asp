"""Tests for the Telegram notifier's pure logic.

No network: a stub transport records what would be sent. The behaviours that
matter are priming (no backlog blast on first run), no duplicate alerts, and
one result message per resolved signal.

Run: python3 -m unittest discover -s bot -p "test_*.py"
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import telegram_bot as bot  # noqa: E402


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.updates_queue: list[list[dict]] = []

    def send(self, chat_id: str, text: str) -> bool:
        self.sent.append((chat_id, text))
        return True

    def updates(self, offset):
        return self.updates_queue.pop(0) if self.updates_queue else []


def signal(time_value: int, direction: str, outcome: str = "OPEN") -> dict:
    return {"time": time_value, "direction": direction, "entry": 4000.0,
            "stop": 3990.0, "target": 4020.0, "regime": "QUIET", "rsi": 55.0,
            "outcome": outcome, "resolved": outcome != "OPEN",
            "r_net": 1.6 if outcome == "TARGET" else (-1.0 if outcome == "STOP"
                                                      else None),
            "exit_price": 4020.0}


class BotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self._orig_state = bot.STATE_PATH
        bot.STATE_PATH = os.path.join(self.tmp, "state.json")

    def tearDown(self) -> None:
        bot.STATE_PATH = self._orig_state

    def _make(self) -> tuple[bot.Bot, FakeTelegram]:
        tg = FakeTelegram()
        return bot.Bot(tg, "chat-1"), tg

    def test_first_poll_does_not_alert_backlog(self):
        b, tg = self._make()
        b.handle_signals({"signals": [signal(1, "LONG", "TARGET"),
                                      signal(2, "SHORT")]})
        self.assertEqual(tg.sent, [], "priming must stay silent")
        self.assertTrue(b.primed)

    def test_new_signal_alerts_once(self):
        b, tg = self._make()
        b.handle_signals({"signals": [signal(1, "LONG")]})   # prime
        b.handle_signals({"signals": [signal(1, "LONG"), signal(2, "SHORT")]})
        self.assertEqual(len(tg.sent), 1)
        self.assertIn("بيع", tg.sent[0][1])
        b.handle_signals({"signals": [signal(1, "LONG"), signal(2, "SHORT")]})
        self.assertEqual(len(tg.sent), 1, "no duplicate for the same signal")

    def test_result_is_sent_once(self):
        b, tg = self._make()
        b.handle_signals({"signals": []})                    # prime empty
        b.handle_signals({"signals": [signal(1, "LONG")]})
        self.assertEqual(len(tg.sent), 1)                    # the new idea
        b.handle_signals({"signals": [signal(1, "LONG", "TARGET")]})
        self.assertEqual(len(tg.sent), 2)                    # the result
        b.handle_signals({"signals": [signal(1, "LONG", "TARGET")]})
        self.assertEqual(len(tg.sent), 2, "result not repeated")

    def test_restart_resumes_without_realerting(self):
        b1, tg1 = self._make()
        b1.handle_signals({"signals": [signal(1, "LONG")]})  # prime + persist
        b1.handle_signals({"signals": [signal(1, "LONG"), signal(2, "SHORT")]})
        self.assertEqual(len(tg1.sent), 1)
        b2, tg2 = self._make()                               # simulate restart
        self.assertTrue(b2.primed)
        b2.handle_signals({"signals": [signal(1, "LONG"), signal(2, "SHORT")]})
        self.assertEqual(tg2.sent, [], "restart must not replay alerts")

    def test_status_command_reports_summary(self):
        b, tg = self._make()
        b._fetch = lambda: {"summary": {"total_signals": 8, "wins": 2,
                                        "losses": 6, "total_r_net": -3.74,
                                        "total_r_gross": -2.67,
                                        "sufficient": False, "min_scored": 30}}
        tg.updates_queue = [[{"update_id": 5, "message": {"text": "/status"}}]]
        b.handle_updates()
        self.assertEqual(len(tg.sent), 1)
        self.assertIn("الملخّص", tg.sent[0][1])
        self.assertIn("—", tg.sent[0][1], "no rate stated on thin data")


class FormatTests(unittest.TestCase):
    def test_new_signal_has_levels_and_rr(self):
        text = bot.format_new(signal(1, "LONG"))
        for piece in ("شراء", "4000.0", "3990.0", "4020.0", "1.67"):
            self.assertIn(piece, text)

    def test_new_signal_labels_direction_in_arabic(self):
        # A raw "SHORT" in the alert read as a system that only ever buys; the
        # direction is a user-facing field and is labelled like the rest.
        self.assertIn("شراء", bot.format_new(signal(1, "LONG")))
        self.assertIn("بيع", bot.format_new(signal(1, "SHORT")))
        self.assertNotIn("SHORT", bot.format_new(signal(1, "SHORT")))

    def test_result_labels_direction_in_arabic(self):
        text = bot.format_result(signal(1, "SHORT", "STOP"))
        self.assertIn("بيع", text)
        self.assertNotIn("SHORT", text)

    def test_approach_labels_direction_in_arabic(self):
        live = {"direction": "SHORT", "entry": 4150.0, "stop": 4160.0,
                "target": 4130.0}
        text = bot.format_approach(live, {"high": 4155.0, "low": 4131.0})
        self.assertIn("بيع", text)
        self.assertNotIn("SHORT", text)

    def test_result_shows_net_r(self):
        self.assertIn("+1.60R", bot.format_result(signal(1, "LONG", "TARGET")))
        self.assertIn("-1.00R", bot.format_result(signal(1, "LONG", "STOP")))

    def test_summary_refuses_rate_when_thin(self):
        text = bot.format_summary({"wins": 2, "losses": 6, "sufficient": False,
                                   "min_scored": 30, "total_r_net": -3.74})
        self.assertIn("لا بيانات كافية", text)
        self.assertNotIn("نسبة الإصابة: 25", text)

    def test_no_trade_always_states_a_reason(self):
        for reason, phrase in (("NO_EDGE", "حافة"),
                               ("VOLATILE", "تقلّب"),
                               ("COOLDOWN", "تهدئة")):
            text = bot.format_no_trade({"decision": "NO_TRADE", "reason": reason})
            self.assertIn(phrase, text)

    def test_no_trade_includes_the_market_read(self):
        text = bot.format_no_trade({
            "decision": "NO_TRADE", "reason": "NO_EDGE",
            "read": {"regime": "RANGING", "bias": "RANGE", "rsi": 49.5,
                     "atr": 4.3, "close": 4185.0}})
        self.assertIn("عرضي", text)
        self.assertIn("4185.0", text)

    def test_last_bar_picks_trade_or_no_trade(self):
        trade = bot.format_last_bar(
            {"decision": "TRADE", "signal": signal(1, "LONG")})
        self.assertIn("صفقة جديدة", trade)
        flat = bot.format_last_bar({"decision": "NO_TRADE", "reason": "NO_EDGE"})
        self.assertIn("لا صفقة", flat)

    def test_result_states_dollars_on_the_account(self):
        # 10 USD stop distance, -1.0R net, minimum 1 oz => -10.00 on the account.
        text = bot.format_result(signal(1, "LONG", "STOP"), account_usd=200.0)
        self.assertIn("10.00", text)

    def test_plan_line_flags_the_minimum_lot_floor(self):
        plan = {"account_usd": 200.0, "risk_usd": 2.0, "position_oz": 0.31,
                "below_min_lot": True, "min_risk_usd": 6.44, "min_risk_pct": 3.22}
        text = bot._plan_line(plan)
        self.assertIn("أصغر", text)
        self.assertIn("6.44", text)

    def test_approach_is_silent_when_price_is_far(self):
        live = {"direction": "LONG", "entry": 4150.0, "stop": 4140.0,
                "target": 4170.0}
        self.assertIsNone(bot.format_approach(
            live, {"high": 4152.0, "low": 4148.0}))

    def test_approach_warns_near_the_target(self):
        live = {"direction": "LONG", "entry": 4150.0, "stop": 4140.0,
                "target": 4170.0}
        text = bot.format_approach(live, {"high": 4168.0, "low": 4155.0})
        self.assertIn("قربت الهدف", text)
        self.assertIn("4170.0", text)

    def test_approach_warns_near_the_stop(self):
        live = {"direction": "LONG", "entry": 4150.0, "stop": 4140.0,
                "target": 4170.0}
        text = bot.format_approach(live, {"high": 4149.0, "low": 4141.5})
        self.assertIn("قربت الوقف", text)
        self.assertIn("4140.0", text)

    def test_approach_reports_the_nearer_side(self):
        live = {"direction": "LONG", "entry": 4150.0, "stop": 4140.0,
                "target": 4170.0}
        text = bot.format_approach(live, {"high": 4169.5, "low": 4139.0})
        self.assertIn("قربت الهدف", text)

    def test_approach_handles_a_short_trade(self):
        live = {"direction": "SHORT", "entry": 4150.0, "stop": 4160.0,
                "target": 4130.0}
        text = bot.format_approach(live, {"high": 4152.0, "low": 4131.5})
        self.assertIn("قربت الهدف", text)
        self.assertIn("4130.0", text)

    def test_approach_is_inert_without_levels(self):
        self.assertIsNone(bot.format_approach(
            {"direction": "LONG", "entry": 4150.0}, {"high": 4150.0, "low": 4150.0}))


if __name__ == "__main__":
    unittest.main()
