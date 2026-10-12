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
        self.assertIn("SHORT", tg.sent[0][1])
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
        for piece in ("LONG", "4000.0", "3990.0", "4020.0", "1.67"):
            self.assertIn(piece, text)

    def test_result_shows_net_r(self):
        self.assertIn("+1.60R", bot.format_result(signal(1, "LONG", "TARGET")))
        self.assertIn("-1.00R", bot.format_result(signal(1, "LONG", "STOP")))

    def test_summary_refuses_rate_when_thin(self):
        text = bot.format_summary({"wins": 2, "losses": 6, "sufficient": False,
                                   "min_scored": 30, "total_r_net": -3.74})
        self.assertIn("لا بيانات كافية", text)
        self.assertNotIn("نسبة الإصابة: 25", text)


if __name__ == "__main__":
    unittest.main()
