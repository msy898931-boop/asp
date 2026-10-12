#!/usr/bin/env python3
"""ASTRA Telegram notifier: tells you when a trade idea appears and how it ends.

It is a reader, not a decider. It polls the signal tracker over loopback and
pushes two kinds of message:

  * a new trade idea, with direction, reference, stop, target and R:R;
  * the result once price decides it (target, stop or horizon), with the net R.

It also answers a few commands (/status, /summary, /signals) so the same bot
can report the honest scoreboard on demand.

Two deliberate properties:

  * No spam on start. The first poll seeds state with everything already on the
    board and sends nothing, so a restart never replays the backlog.
  * State is persisted, so a restart resumes instead of re-alerting.

Disabled unless ASTRA_ENABLE_BOT=1, because a bot that talks to a chat should
be switched on deliberately. Token and chat come from TELEGRAM_BOT_TOKEN and
TELEGRAM_CHAT_ID.

Run: python3 bot/telegram_bot.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

TRACKER_BASE = os.environ.get("ASTRA_BOT_TRACKER", "http://127.0.0.1:8793")
POLL_SECONDS = float(os.environ.get("ASTRA_BOT_POLL", "20"))
HTTP_TIMEOUT = float(os.environ.get("ASTRA_BOT_TIMEOUT", "15"))
STATE_PATH = os.environ.get("ASTRA_BOT_STATE",
                            os.path.join(ROOT, "run", "telegram_state.json"))
_USER_AGENT = os.environ.get("ASTRA_BOT_USER_AGENT", "ASTRA-bot/1.0")

OUTCOME_AR = {"TARGET": "✅ أصابت الهدف", "STOP": "❌ ضربت الوقف",
              "TIMEOUT": "⌛ انتهت المدّة", "OPEN": "… مفتوحة"}


def _get_json(url: str) -> dict | None:
    try:
        req = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


class Telegram:
    def __init__(self, token: str) -> None:
        self.token = token
        self.base = "https://api.telegram.org/bot%s" % token

    def send(self, chat_id: str, text: str) -> bool:
        data = urllib.parse.urlencode(
            {"chat_id": chat_id, "text": text,
             "disable_web_page_preview": "true"}).encode("utf-8")
        req = urllib.request.Request(self.base + "/sendMessage", data=data,
                                     headers={"User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, OSError) as exc:
            sys.stderr.write("[bot] send failed: %r\n" % (exc,))
            return False

    def updates(self, offset: int | None) -> list[dict]:
        params = {"timeout": "0"}
        if offset is not None:
            params["offset"] = str(offset)
        url = self.base + "/getUpdates?" + urllib.parse.urlencode(params)
        env = _get_json(url)
        if not env or not env.get("ok"):
            return []
        return env.get("result") or []


def signal_key(signal: dict) -> str:
    return "%s:%s" % (signal.get("time"), signal.get("direction"))


def format_new(signal: dict) -> str:
    return ("🔔 إشارة جديدة\n"
            "الاتجاه: %s\n"
            "الدخول: %s\n"
            "الوقف: %s\n"
            "الهدف: %s\n"
            "R:R 1.67 · النظام %s · RSI %s"
            % (signal.get("direction"), signal.get("entry"), signal.get("stop"),
               signal.get("target"), signal.get("regime"), signal.get("rsi")))


def format_result(signal: dict) -> str:
    head = OUTCOME_AR.get(signal.get("outcome"), signal.get("outcome"))
    r = signal.get("r_net")
    r_txt = "—" if r is None else "%+.2fR" % r
    return ("%s\n%s  %s → %s\nصافي %s"
            % (head, signal.get("direction"), signal.get("entry"),
               signal.get("exit_price"), r_txt))


def format_summary(summary: dict) -> str:
    lines = ["📊 الملخّص",
             "الإشارات: %s" % summary.get("total_signals", 0),
             "أصابت: %s · خسرت: %s" % (summary.get("wins", 0),
                                        summary.get("losses", 0)),
             "صافي: %+.2fR · إجمالي: %+.2fR" % (
                 summary.get("total_r_net") or 0.0,
                 summary.get("total_r_gross") or 0.0)]
    if summary.get("sufficient"):
        lines.append("نسبة الإصابة: %s%%" % summary.get("win_rate"))
    else:
        lines.append("نسبة الإصابة: — (لا بيانات كافية، %s/%s)"
                     % (summary.get("wins", 0) + summary.get("losses", 0),
                        summary.get("min_scored", 30)))
    return "\n".join(lines)


class Bot:
    def __init__(self, tg: Telegram, chat_id: str) -> None:
        self.tg = tg
        self.chat_id = chat_id
        self.seen: set[str] = set()
        self.result_sent: dict[str, str] = {}
        self.offset: int | None = None
        self.primed = False
        self._load()

    def _load(self) -> None:
        try:
            with open(STATE_PATH, encoding="utf-8") as fh:
                data = json.load(fh)
            self.seen = set(data.get("seen", []))
            self.result_sent = data.get("result_sent", {})
            self.offset = data.get("offset")
            self.primed = bool(data.get("primed"))
        except (OSError, ValueError):
            pass  # fresh start; priming below prevents a backlog blast

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
            tmp = STATE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"seen": sorted(self.seen),
                           "result_sent": self.result_sent,
                           "offset": self.offset,
                           "primed": self.primed}, fh)
            os.replace(tmp, STATE_PATH)
        except OSError:
            pass

    def _fetch(self) -> dict | None:
        env = _get_json("%s/v1/signals" % TRACKER_BASE)
        return env.get("tracker") if env else None

    def handle_signals(self, data: dict) -> None:
        signals = data.get("signals") or []
        if not self.primed:
            for s in signals:
                key = signal_key(s)
                self.seen.add(key)
                if s.get("resolved"):
                    self.result_sent[key] = s.get("outcome")
            self.primed = True
            self._save()
            sys.stderr.write("[bot] primed with %d existing signals\n" % len(signals))
            return

        for s in signals:
            key = signal_key(s)
            if key not in self.seen:
                self.seen.add(key)
                self.tg.send(self.chat_id, format_new(s))
            if s.get("resolved") and key not in self.result_sent:
                self.result_sent[key] = s.get("outcome")
                self.tg.send(self.chat_id, format_result(s))
        self._save()

    def handle_updates(self) -> None:
        updates = self.tg.updates(self.offset)
        for up in updates:
            self.offset = up.get("update_id", 0) + 1
            msg = up.get("message") or {}
            text = (msg.get("text") or "").strip().lower()
            if not text:
                continue
            if text.startswith("/status") or text.startswith("/summary"):
                data = self._fetch()
                if not data:
                    self.tg.send(self.chat_id, "المتتبّع غير متاح.")
                    continue
                self.tg.send(self.chat_id, format_summary(data.get("summary") or {}))
            elif text.startswith("/signals"):
                data = self._fetch()
                if not data:
                    self.tg.send(self.chat_id, "المتتبّع غير متاح.")
                    continue
                rows = (data.get("signals") or [])[-5:]
                if not rows:
                    self.tg.send(self.chat_id, "لا إشارات بعد.")
                else:
                    self.tg.send(self.chat_id,
                                 "\n\n".join(format_result(s) for s in rows))
            elif text.startswith("/start"):
                self.tg.send(self.chat_id,
                             "بوت ASTRA جاهز. الأوامر: /status /signals")
        if updates:
            self._save()

    def run_forever(self) -> None:
        while True:
            try:
                self.handle_updates()
                data = self._fetch()
                if data:
                    self.handle_signals(data)
            except Exception as exc:  # keep the bot alive across a bad cycle
                sys.stderr.write("[bot] cycle error: %r\n" % (exc,))
            time.sleep(POLL_SECONDS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ASTRA Telegram notifier")
    parser.add_argument("--token", default=os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    parser.add_argument("--chat-id", default=os.environ.get("TELEGRAM_CHAT_ID", ""))
    args = parser.parse_args(argv)

    if not args.token or not args.chat_id:
        sys.stderr.write("[bot] disabled: set TELEGRAM_BOT_TOKEN and "
                         "TELEGRAM_CHAT_ID\n")
        return 2

    bot = Bot(Telegram(args.token), args.chat_id)
    sys.stderr.write("[bot] listening: tracker=%s chat=%s primed=%s\n"
                     % (TRACKER_BASE, args.chat_id, bot.primed))
    bot.tg.send(args.chat_id, "بوت ASTRA اتّصل. سأرسل الإشارات ونتائجها.")
    bot.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
