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
from typing import Any

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

# Why the engine did not open a trade on a bar. Named so a quiet bar is
# explained, never left blank.
REASON_AR = {
    "NO_DATA": "ما في بيانات كافية بعد",
    "STRUCTURE_INCOMPLETE": "الهيكل السعري غير مكتمل",
    "VOLATILE": "تقلّب عالٍ — مستثنى من التداول",
    "NO_EDGE": "ما في حافة واضحة (الإشارات متعارضة)",
    "IN_POSITION": "في صفقة مفتوحة أصلاً",
    "COOLDOWN": "فترة تهدئة بعد آخر صفقة",
    "JUST_RESOLVED": "الصفقة السابقة انتهت للتوّ",
}

REGIME_AR = {"TRENDING_UP": "صعود", "TRENDING_DOWN": "هبوط",
             "RANGING": "عرضي", "QUIET": "هادئ", "VOLATILE": "متذبذب",
             "UNKNOWN": "غير معروف"}

BIAS_AR = {"BULLISH": "صاعد", "BEARISH": "هابط", "RANGE": "عرضي",
           "INCOMPLETE": "غير مكتمل"}

# A signal's direction reaches the alert verbatim ("LONG"/"SHORT"); the reader
# is Arabic, so it is labelled like every other field rather than left in code.
DIRECTION_AR = {"LONG": "شراء", "SHORT": "بيع", "NEUTRAL": "حياد"}


def _ar(table: dict[str, str], key: object) -> Any:
    """Arabic label for `key` from `table`, else `key` itself (or None).

    The signal/status dicts are untrusted JSON, so the key is only looked up
    when it really is a string; anything else (None, a number) is passed
    through unchanged, which is what the callers already relied on.
    """
    if isinstance(key, str):
        return table.get(key, key)
    return key

# A live trade counts as "near" a level when the price is within this fraction
# of the stop-to-target span. 0.2 means the last 20% before a level.
APPROACH_FRAC = float(os.environ.get("ASTRA_APPROACH_FRAC", "0.2"))


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


def format_new(signal: dict, plan: dict | None = None) -> str:
    entry, stop, target = (signal.get("entry"), signal.get("stop"),
                           signal.get("target"))
    stop_dist = abs((entry or 0) - (stop or 0))
    lines = [
        "🔔 صفقة جديدة — %s" % _ar(DIRECTION_AR, signal.get("direction")),
        "الدخول: %s" % entry,
        "الوقف: %s  (مسافة %s$/أونصة)" % (stop, round(stop_dist, 2)),
        "الهدف: %s" % target,
        "المخاطرة/العائد: 1.67",
        "السبب: النظام %s · الميل %s · RSI %s"
        % (_ar(REGIME_AR, signal.get("regime")),
           _ar(BIAS_AR, signal.get("bias")),
           signal.get("rsi")),
    ]
    if plan:
        lines.append(_plan_line(plan))
    return "\n".join(lines)


def _plan_line(plan: dict) -> str:
    """The money side of a trade, including gold's minimum-lot reality."""
    if plan.get("below_min_lot"):
        return ("⚠️ على حساب %s$: المخاطرة المطلوبة %s$ تحتاج %s أونصة، "
                "وهذا أقل من أصغر حجم (1 أونصة).\n"
                "أصغر صفقة ممكنة = 1 أونصة → مخاطرة %s$ (%s%% من الحساب)."
                % (_money(plan.get("account_usd")), _money(plan.get("risk_usd")),
                   plan.get("position_oz"), _money(plan.get("min_risk_usd")),
                   plan.get("min_risk_pct")))
    return ("💵 على حساب %s$: الحجم %s أونصة · مخاطرة %s$ (%s%%)."
            % (_money(plan.get("account_usd")), plan.get("position_oz"),
               _money(plan.get("risk_usd")), plan.get("risk_pct")))


def _money(value) -> str:
    return "—" if value is None else "%.2f" % float(value)


def format_result(signal: dict, account_usd: float = 0.0) -> str:
    head = _ar(OUTCOME_AR, signal.get("outcome"))
    r = signal.get("r_net")
    r_txt = "—" if r is None else "%+.2fR" % r
    lines = ["%s\n%s  %s → %s\nصافي %s"
             % (head, _ar(DIRECTION_AR, signal.get("direction")),
                signal.get("entry"), signal.get("exit_price"), r_txt)]
    usd = _result_usd(signal, account_usd)
    if usd is not None:
        lines.append("💵 على حساب %s$ (أصغر حجم 1 أونصة): %s%s"
                     % (_money(account_usd), "+" if usd >= 0 else "",
                        _money(usd)))
    return "\n".join(lines)


def _result_usd(signal: dict, account_usd: float) -> float | None:
    """The dollar result of the minimum position, the honest baseline."""
    r = signal.get("r_net")
    entry, stop = signal.get("entry"), signal.get("stop")
    if r is None or entry is None or stop is None:
        return None
    stop_dist = abs(entry - stop)
    return round(r * stop_dist, 2)      # 1 oz = 1 USD per $1/oz move


def format_no_trade(status: dict) -> str:
    reason = _ar(REASON_AR, status.get("reason"))
    lines = ["⏸️ لا صفقة على هذه الشمعة (M15)",
             "السبب: %s" % reason]
    read = status.get("read")
    if read:
        lines.append("قراءة السوق: %s · الميل %s · RSI %s · ATR %s"
                     % (_ar(REGIME_AR, read.get("regime")),
                        _ar(BIAS_AR, read.get("bias")),
                        read.get("rsi"), read.get("atr")))
        lines.append("السعر: %s" % read.get("close"))
    held = status.get("open")
    if held:
        lines.append("مفتوحة الآن: %s من %s (وقف %s · هدف %s)"
                     % (held.get("direction"), held.get("entry"),
                        held.get("stop"), held.get("target")))
    resolved = status.get("resolved")
    if resolved:
        lines.append(format_result(resolved))
    return "\n".join(lines)


def format_last_bar(status: dict, account_usd: float = 0.0,
                    risk_pct: float = 0.0) -> str:
    """One message per bar, whether or not it carried a trade."""
    if status.get("decision") == "TRADE":
        plan = status.get("plan")
        if plan is None and account_usd > 0 and risk_pct > 0:
            from replay import account_plan  # local import: optional dep
            s = status["signal"]
            plan = account_plan(s["entry"], s["stop"], account_usd, risk_pct)
        return format_new(status["signal"], plan)
    return format_no_trade(status)


def _approach(signal: dict, bar: dict) -> dict | None:
    """How close a live trade is to its stop or target, or None if far.

    Uses the bar's high/low, the honest "did the price get here" test, and
    reports the nearer side so a single message names the real risk.
    """
    entry = signal.get("entry")
    stop = signal.get("stop")
    target = signal.get("target")
    high = bar.get("high")
    low = bar.get("low")
    if (entry is None or stop is None or target is None
            or high is None or low is None):
        return None
    span = abs(target - stop)
    if span <= 0:
        return None
    # Distance to each level, from the side the price would have to travel.
    to_target = abs(target - high) if target >= entry else abs(low - target)
    to_stop = abs(low - stop) if stop <= entry else abs(high - stop)
    side, distance = ("target", to_target) if to_target <= to_stop else ("stop", to_stop)
    if distance > span * APPROACH_FRAC:
        return None
    pct = distance / span * 100.0
    return {"side": side, "distance": round(distance, 2), "pct": round(pct)}


def format_stale(age_min: float) -> str:
    """Said once when the feed goes quiet, so an old bar is never a signal."""
    return "\n".join([
        "⏳ وقفت بيانات السوق",
        "آخر شمعة صار لها %d دقيقة. عادةً يعني السوق مسكّر أو الاتصال انقطع."
        % int(age_min),
        "لحد ما ترجع البيانات: ما رح أرسل إشارات، عشان ما أعطيك صفقة على شمعة قديمة.",
    ])


def format_resumed() -> str:
    return "▶️ رجعت بيانات السوق — رجع الفحص الطبيعي."


def format_approach(signal: dict, bar: dict) -> str | None:
    """A heads-up when a live trade nears its stop or target. None if far."""
    near = _approach(signal, bar)
    if near is None:
        return None
    side = near["side"]
    head = "🎯 الصفقة قربت الهدف" if side == "target" else "⚠️ الصفقة قربت الوقف"
    level = signal.get("target") if side == "target" else signal.get("stop")
    return "\n".join([
        head,
        "%s من %s · %s→%s" % (_ar(DIRECTION_AR, signal.get("direction")),
                              signal.get("entry"), signal.get("entry"),
                              signal.get("target")),
        "المستوى: %s · المسافة المتبقية %s$ (~%s%% من مدى الصفقة)"
        % (level, near["distance"], near["pct"]),
        "لا تتحرّك بسرعة — النظام بيسكّرها لحاله عند الوقف أو الهدف.",
    ])


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
            except Exception as exc:  # noqa: BLE001 - keep the bot alive across a bad cycle
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
