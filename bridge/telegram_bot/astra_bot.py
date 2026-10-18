"""TEL-0101 - ASTRA Telegram bot.

A loopback-driven Telegram bot for the ASTRA backend. It reads the same
frontend-facing API the desktop UI reads (127.0.0.1:8790/api/v1) and:

  * answers operator commands (/status, /health, /analysis, ...);
  * pushes state-change alerts (health transitions, new signals, bridge
    online/offline) to subscribed chats.

It is strictly a read-and-notify surface. It carries no execution authority
and never places trades. Standard library only, so it runs next to the MT5
Python bridge without extra dependencies.

Usage:
    astra_bot.py --token <BOT_TOKEN> [--chat <CHAT_ID>] [--api <BASE_URL>]
                 [--interval 15] [--state ~/.astra/telegram_subscribers.json]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal as _signal
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from astra_api import ApiError, AstraApi
from entitlements import Entitlements
from entitlements import from_env as entitlements_from_env
from price_levels import DIR_ABOVE, DIR_BELOW, DIR_CROSS, LevelStore
from volatility import (
    TIMEFRAMES,
    VolatilityModel,
    format_volatility_alert,
)

# The news-quiet window lives in the advisor package. Its location differs by
# deployment: the repo keeps it at <root>/advisor, while the container copies it
# to /app/advisor next to (not inside) resources/. Probe the known shapes and
# import best-effort, so the bot still runs (volatility only) without it.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ADVISOR_CANDIDATES = (
    os.environ.get("ASTRA_ADVISOR_DIR", ""),
    os.path.join(_HERE, "..", "..", "advisor"),        # repo: <root>/bridge/telegram_bot
    os.path.join(_HERE, "..", "..", "..", "advisor"),  # container: /app/resources/bridge/telegram_bot
    os.path.join(os.getcwd(), "advisor"),
    os.path.join(os.getcwd(), "..", "advisor"),
)
for _cand in _ADVISOR_CANDIDATES:
    _abs = os.path.abspath(_cand) if _cand else ""
    if _abs and os.path.isfile(os.path.join(_abs, "calendar_feed.py")):
        if _abs not in sys.path:
            sys.path.insert(0, _abs)
        break
try:
    import calendar_feed
except ImportError:  # pragma: no cover - deployment-shape dependent
    calendar_feed = None
try:
    import news_window
except ImportError:  # pragma: no cover - deployment-shape dependent
    news_window = None
try:
    import news_engine
except ImportError:  # pragma: no cover - deployment-shape dependent
    news_engine = None
# The trade-card renderer lives with the advisor too. Imported best-effort: the
# bot still runs (minus card alerts) when the advisor package is absent.
try:
    import mtf_read
except ImportError:  # pragma: no cover - deployment-shape dependent
    mtf_read = None

# Web Push mirrors the phone app: the mobile package sits beside (repo) or one
# level up from (container) the bot. Loaded best-effort, so the bot runs
# unchanged when it is absent or when no VAPID keys are set.
_WEBPUSH_CANDIDATES = (
    os.environ.get("ASTRA_WEBPUSH_DIR", ""),
    os.path.join(_HERE, "..", "..", "mobile"),         # repo: <root>/bridge/telegram_bot
    os.path.join(_HERE, "..", "..", "..", "mobile"),   # container: /app/resources/bridge/telegram_bot
)
for _cand in _WEBPUSH_CANDIDATES:
    _abs = os.path.abspath(_cand) if _cand else ""
    if _abs and os.path.isfile(os.path.join(_abs, "webpush.py")):
        if _abs not in sys.path:
            sys.path.insert(0, _abs)
        break
try:
    import webpush
except ImportError:  # pragma: no cover - deployment-shape dependent
    webpush = None

TELEGRAM_API = "https://api.telegram.org"
_POLL_TIMEOUT_SECONDS = 25
_SEND_TIMEOUT_SECONDS = 10

_stop = threading.Event()

# ── the user-facing surface (Arabic) ────────────────────────────────────────
# The reply keyboard the user taps. Each label maps to a command token, and the
# router understands the label as well as the token, so a tap works like typing.
REPLY_KEYBOARD: List[List[str]] = [
    ["💰 سعر الذهب", "📊 حالة السوق"],
    ["🎯 بطاقة الصفقة", "📰 الأخبار والذهب"],
    ["🔔 مستوى جديد", "📋 مستوياتي"],
    ["🗑 مسح المستويات", "⭐ خطتي"],
    ["❓ مساعدة"],
]

BUTTON_COMMANDS: Dict[str, str] = {
    "💰 سعر الذهب": "/price",
    "📊 حالة السوق": "/status",
    "🎯 بطاقة الصفقة": "/card",
    "📰 الأخبار والذهب": "/news",
    "🔔 مستوى جديد": "/level",
    "📋 مستوياتي": "/levels",
    "🗑 مسح المستويات": "/clearlevels",
    "⭐ خطتي": "/plan",
    "❓ مساعدة": "/help",
}

# Registered with Telegram on startup, so the menu button lists the real
# commands. This replaces whatever was set by hand in BotFather.
BOT_COMMANDS: List[tuple] = [
    ("/start", "بدء الاستخدام والقائمة"),
    ("/price", "سعر الذهب الحي"),
    ("/news", "الأخبار وتأثيرها على الذهب الآن"),
    ("/level", "إضافة مستوى سعري للتنبيه"),
    ("/levels", "مستوياتي وحالتها"),
    ("/clearlevels", "حذف كل مستوياتي"),
    ("/status", "حالة النظام والوضع"),
    ("/card", "بطاقة صفقة متعدّدة الفريمات"),
    ("/plan", "خطتي وحدودي"),
    ("/myid", "معرّف محادثتي"),
    ("/subscribe", "تشغيل التنبيهات"),
    ("/unsubscribe", "إيقاف التنبيهات"),
    ("/help", "المساعدة"),
]

# Commands whose reply should also re-show the keyboard.
_SHOW_KEYBOARD = ("/start", "/help", "/menu")

# Arabic direction words map to the stored English tokens.
_DIRECTION_ALIASES: Dict[str, str] = {
    "فوق": DIR_ABOVE, "أعلى": DIR_ABOVE, "above": DIR_ABOVE,
    "تحت": DIR_BELOW, "أسفل": DIR_BELOW, "below": DIR_BELOW,
    "أي": DIR_CROSS, "اي": DIR_CROSS, "cross": DIR_CROSS,
}

_DIRECTION_AR: Dict[str, str] = {
    DIR_ABOVE: "فوق", DIR_BELOW: "تحت", DIR_CROSS: "أي اتجاه",
}


def _dir_ar(direction: str) -> str:
    return _DIRECTION_AR.get(direction, direction)


# ───────────────────────────── Telegram client ──────────────────────────────


class TelegramClient:
    """Minimal Bot API client (sendMessage + getUpdates)."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.base = f"{TELEGRAM_API}/bot{token}"

    def _call(self, method: str, payload: Dict[str, Any],
              timeout: float) -> Dict[str, Any]:
        url = f"{self.base}/{method}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"telegram {method} HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError(f"telegram {method} failed: {exc}") from exc
        if not body.get("ok"):
            raise RuntimeError(f"telegram {method} error: {body.get('description')}")
        return body

    def send_message(self, chat_id: str, text: str,
                     keyboard: bool = False) -> None:
        payload: Dict[str, Any] = {
            "chat_id": chat_id, "text": text, "disable_web_page_preview": True,
        }
        if keyboard:
            payload["reply_markup"] = {
                "keyboard": [[{"text": label} for label in row]
                             for row in REPLY_KEYBOARD],
                "resize_keyboard": True,
                "is_persistent": True,
            }
        self._call("sendMessage", payload, _SEND_TIMEOUT_SECONDS)

    def set_my_commands(self) -> None:
        """Register the command menu. Overrides anything set in BotFather.

        Telegram rejects a command name that carries the leading slash (it
        returns BOT_COMMAND_INVALID and drops the whole list), so the stored
        "/price" is registered as "price". The slash is how the user types it,
        not how the menu stores it.
        """
        payload = {"commands": [{"command": c.lstrip("/"), "description": d}
                                for c, d in BOT_COMMANDS]}
        self._call("setMyCommands", payload, _SEND_TIMEOUT_SECONDS)

    def set_menu_button(self) -> None:
        """Make the menu button open the command list (default: all chats)."""
        self._call("setChatMenuButton", {"menu_button": {"type": "commands"}},
                   _SEND_TIMEOUT_SECONDS)

    def get_updates(self, offset: Optional[int]) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "timeout": _POLL_TIMEOUT_SECONDS,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        body = self._call("getUpdates", payload, _POLL_TIMEOUT_SECONDS + 10)
        result = body.get("result")
        return result if isinstance(result, list) else []


# ────────────────────────────── subscriptions ───────────────────────────────


class Subscriptions:
    """Persisted set of chat ids that receive push alerts."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._chats: Set[str] = set()
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text("utf-8"))
            if isinstance(raw, list):
                self._chats = {str(c) for c in raw}
        except (OSError, ValueError):
            self._chats = set()

    def _save_locked(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(sorted(self._chats)), "utf-8")
        except OSError as exc:
            print(f"[warn] could not persist subscribers: {exc}", file=sys.stderr)

    def add(self, chat_id: str) -> None:
        with self._lock:
            self._chats.add(str(chat_id))
            self._save_locked()

    def remove(self, chat_id: str) -> None:
        with self._lock:
            self._chats.discard(str(chat_id))
            self._save_locked()

    def all(self) -> List[str]:
        with self._lock:
            return sorted(self._chats)


# ─────────────────────────────── formatting ─────────────────────────────────


def _yn(value: Any) -> str:
    return "نعم" if value else "لا"


def format_status(snap: Dict[str, Any]) -> str:
    health = snap.get("health", {})
    system = snap.get("system", {})
    bridge = snap.get("bridge", {})

    def err(d: Dict[str, Any]) -> str:
        return f"غير متاح ({d['_error']})" if "_error" in d else ""

    lines = ["[ASTRA] الحالة"]
    if "_error" in health:
        lines.append(f"الخادم: {err(health)}")
    else:
        lines.append(f"الصحة: {health.get('aggregate', 'UNKNOWN')} "
                     f"(بيانات موثوقة: {_yn(health.get('decision_grade_data'))})")
    if "_error" not in system:
        lines.append(f"الوضع: {system.get('mode', 'UNKNOWN')} "
                     f"(ظل فقط: {_yn(system.get('shadow_only'))})")
        lines.append(f"الإقلاع: {system.get('startup_stage', 'UNKNOWN')} "
                     f"(جاهز: {_yn(system.get('ready'))})")
    if "_error" not in bridge:
        symbol = bridge.get("resolved_symbol") or "(لا شيء)"
        lines.append(f"جسر MT5: {bridge.get('bridge_process_state', 'UNKNOWN')} "
                     f"(MT5 جاهز: {_yn(bridge.get('mt5_ready'))}, الرمز: {symbol})")
        broker = bridge.get("broker") or ""
        if broker:
            lines.append(f"المصدر: {broker}")
    lines.append("وضع الظل. ليست إشارة تداول.")
    return "\n".join(lines)


def format_analysis(data: Dict[str, Any]) -> str:
    signal = data.get("signal", {}) or {}
    context = data.get("context", {}) or {}
    if not data.get("timestamp"):
        return ("[ASTRA] التحليل\nلا قرار مسجّل بعد "
                "(بانتظار بيانات موثوقة).")
    lines = [
        "[ASTRA] التحليل",
        f"الاتجاه: {signal.get('direction', 'NONE')}",
        f"النمط: {context.get('regime', 'UNKNOWN')} "
        f"(H4 {context.get('h4_bias', 'UNKNOWN')}, M15 {context.get('m15_trigger', 'UNKNOWN')})",
    ]
    score = signal.get("score")
    if score is not None:
        lines.append(f"الدرجة: {score}")
    prob = signal.get("probability")
    if prob is not None:
        lines.append(f"الاحتمال: {prob} (مُعاير: {_yn(signal.get('probability_calibrated'))})")
    lines.append("دعم قرار فقط. ليست نصيحة مالية.")
    return "\n".join(lines)


def format_signal(data: Dict[str, Any]) -> str:
    if not data.get("available", True):
        return ("[ASTRA] الإشارات\nلا يوجد: "
                f"{data.get('reason', 'لا قرارات مسجّلة بعد')}.")
    return ("[ASTRA] آخر إشارة\n" + json.dumps(data, indent=2, ensure_ascii=False))


def format_bridge(data: Dict[str, Any]) -> str:
    broker = data.get("broker") or ""
    lines = [
        "[ASTRA] جسر MT5",
        f"العملية: {data.get('bridge_process_state', 'UNKNOWN')}",
        f"MT5 جاهز: {_yn(data.get('mt5_ready'))}",
        f"الرمز: {data.get('resolved_symbol') or '(لا شيء)'}",
    ]
    if broker:
        lines.append(f"المصدر: {broker}")
    lines.append(f"آخر خطأ: {data.get('last_error') or '(لا شيء)'}")
    return "\n".join(lines)


# ─────────────────────────── news formatting ────────────────────────────────

_NEWS_WINDOW_AR: Dict[str, str] = {
    "active": "⏸ نافذة خبر مفتوحة — السوق عادةً عنيف",
    "upcoming": "⏳ خبر مهم قريب",
    "clear": "✅ السوق خالٍ من الأخبار المهمة",
}


def _num(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return "%g" % value if isinstance(value, (int, float)) else str(value)


def _release_read(event: Dict[str, Any]) -> str:
    """The mechanical gold read for one released event, as one Arabic line."""
    surprise = event.get("surprise")
    if surprise in (None, "unknown"):
        return "القراءة غير متوفرة (بلا تقدير ولا سابق)."
    if event.get("currency_dir", 0) == 0:
        return "مطابق للتوقّع — بلا مفاجأة."
    stronger = event["currency_dir"] > 0
    ccy = event.get("currency") or "—"
    if event.get("certain"):
        gold = ("ضغط على الذهب (دولار أقوى)" if stronger
                else "دعم للذهب (دولار أضعف)")
    else:
        gold = "الأثر على الذهب غير مباشر"
    return ("الفعلي %s → %s %s → %s" % (
        "أعلى" if surprise == "above" else "أدنى", ccy,
        "أقوى" if stronger else "أضعف", gold))


def format_news(d: Dict[str, Any]) -> str:
    """The /news reply: the window, the schedule, the tape, the headlines.

    Reads the same digest the web panel reads, so the bot, the advisor and the
    page cannot disagree about the same event.
    """
    win = d.get("window") or {}
    lines = ["📰 الأخبار والذهب — الآن",
             _NEWS_WINDOW_AR.get(str(win.get("state") or ""), "—")]
    if win.get("reason"):
        lines.append(win["reason"])

    upcoming = d.get("upcoming") or []
    if upcoming:
        lines += ["", "⏳ أحداث قادمة عالية التأثير:"]
        for e in upcoming[:5]:
            lines.append("  • %s (%s) بعد %d د" % (
                e.get("title_ar") or e.get("title"), e.get("country"),
                e.get("in_minutes", 0)))

    released = d.get("released") or []
    if released:
        lines += ["", "💱 صدر للتو:"]
        for e in released[:5]:
            lines.append("  • %s (%s) قبل %d د" % (
                e.get("title_ar") or e.get("title"), e.get("country"),
                e.get("minutes_ago", 0)))
            lines.append("    الفعلي %s · التقدير %s · السابق %s" % (
                _num(e.get("actual")), _num(e.get("forecast")),
                _num(e.get("previous"))))
            lines.append("    " + _release_read(e))

    headlines = d.get("headlines") or []
    if headlines:
        lines += ["", "🗞 عناوين الذهب:"]
        for h in headlines[:5]:
            src = h.get("source") or ""
            title = h.get("title") or ""
            # The RSS title often already ends with " - Source"; drop the
            # repeat rather than printing the source twice.
            if src and title.endswith(" - " + src):
                title = title[:-(len(src) + 3)]
            lines.append("  • %s%s" % (title, (" — " + src) if src else ""))

    if not (upcoming or released or headlines):
        lines += ["", "لا أخبار عالية التأثير في المدى القريب، ولا عناوين الآن."]

    lines += ["", "قراءة ميكانيكية: الفعلي مقابل التقدير، والأثر على الذهب "
                  "مباشر مع الدولار فقط. أثر الأخبار على نتائج النظام ما انقاس "
                  "بعد — لا يُدَّعى حافة."]
    return "\n".join(lines)


# ────────────────────────────── alert engine ────────────────────────────────


class AlertEngine:
    """Detects state transitions worth notifying and formats the alert."""

    def __init__(self, api: AstraApi) -> None:
        self.api = api
        self._last_health: Optional[str] = None
        self._last_bridge: Optional[str] = None
        self._last_signal_ts: Optional[str] = None

    def prime(self) -> None:
        """Record the current state without emitting alerts."""
        self._collect(emit=False)

    def poll(self) -> List[str]:
        return self._collect(emit=True)

    def _collect(self, emit: bool) -> List[str]:
        alerts: List[str] = []
        try:
            health = self.api.health()
        except ApiError:
            health = {}
        try:
            bridge = self.api.bridge_status()
        except ApiError:
            bridge = {}
        try:
            analysis = self.api.latest_analysis()
        except ApiError:
            analysis = {}

        h = str(health.get("aggregate", "")) or None
        if h and h != self._last_health:
            if emit and self._last_health is not None:
                alerts.append(
                    f"[ASTRA] تغيّرت الصحة: {self._last_health} ← {h} "
                    f"(بيانات موثوقة: {_yn(health.get('decision_grade_data'))})")
            self._last_health = h

        b = str(bridge.get("bridge_process_state", "")) or None
        if b and b != self._last_bridge:
            if emit and self._last_bridge is not None:
                alerts.append(f"[ASTRA] جسر MT5: {self._last_bridge} ← {b} "
                              f"(MT5 جاهز: {_yn(bridge.get('mt5_ready'))})")
            self._last_bridge = b

        ts = analysis.get("timestamp")
        if ts and ts != self._last_signal_ts:
            if emit:
                alerts.append(format_analysis(analysis))
            self._last_signal_ts = ts

        return alerts


# ─────────────────────────────── command router ─────────────────────────────


class CommandRouter:
    def __init__(self, api: AstraApi, subs: Subscriptions,
                 default_chat: Optional[str],
                 levels: Optional[LevelStore] = None,
                 ent: Optional[Entitlements] = None,
                 vol_model: Optional[VolatilityModel] = None) -> None:
        self.api = api
        self.subs = subs
        self.default_chat = default_chat
        self.levels = levels
        self.ent = ent
        self.vol_model = vol_model

    HELP = (
        "[ASTRA] الأوامر\n"
        "💰 /price - سعر الذهب الحالي\n"
        "📰 /news - الأخبار العالمية وأثرها على الذهب الآن\n"
        "🔔 /level <سعر> [فوق|تحت|أي] - تنبيه عند اختراق مستوى\n"
        "📋 /levels - مستوياتك\n"
        "🗑 /clearlevels - حذف كل مستوياتك\n"
        "🔕 /subscribe - تشغيل التنبيهات\n"
        "🔔 /unsubscribe - إيقاف التنبيهات\n"
        "⭐ /plan - خطتك وحدودها\n"
        "📊 /status - حالة النظام والوضع\n"
        "🌊 /vol - توقّع تقلّب السوق (لا يتنبّأ بالاتجاه)\n"
        "🎯 /card - بطاقة صفقة متعدّدة الفريمات (أرقام محسوبة)\n"
        "🩺 /health - تفاصيل الصحة\n"
        "📈 /analysis - آخر تحليل\n"
        "📶 /bridge - حالة جسر MT5\n"
        "🆔 /myid - معرّف محادثتك (لتفعيل Pro)\n"
        "❓ /help - هذه القائمة\n\n"
        "تنبيهات سعرية وحالة السوق فقط. لا تنبؤات ولا إشارات تداول."
    )

    def _gated(self, chat_id: str) -> Optional[str]:
        """Return an upsell line when the chat may not use paid features."""
        if self.ent is None or self.ent.is_pro(chat_id):
            return None
        return self.ent.upgrade_line()

    def _add_level(self, chat_id: str, text: str) -> str:
        parts = text.split()
        if len(parts) < 2:
            return ("الصيغة: /level <السعر> [فوق|تحت|أي]\n"
                    "مثال: /level 4180 فوق")
        try:
            price = float(parts[1].replace(",", ""))
        except ValueError:
            return f"«{parts[1]}» ليس رقمًا. مثال: /level 4180 فوق"
        raw_dir = parts[2].lower() if len(parts) > 2 else DIR_CROSS
        direction = _DIRECTION_ALIASES.get(raw_dir, raw_dir)
        if direction not in (DIR_ABOVE, DIR_BELOW, DIR_CROSS):
            return "الاتجاه يجب أن يكون: فوق أو تحت أو أي."

        gate = self._gated(chat_id)
        if gate and self.levels is not None:
            cap = self.ent.free_levels if self.ent else 0
            if self.levels.count(chat_id) >= cap:
                return (f"الخطة المجانية تسمح بـ {cap} مستوى.\n"
                        f"احذف مستوى بـ /clearlevels، أو:\n{gate}")

        assert self.levels is not None
        self.levels.add(chat_id, price, direction)
        n = self.levels.count(chat_id)
        return (f"تم ضبط المستوى: {price:g} ({_dir_ar(direction)}).\n"
                f"سينبّهك مرة واحدة عند اختراقه. لديك {n} مستوى.")

    def _news_blackout(self) -> Optional[str]:
        """The news-quiet reason for right now, or None (also when unavailable)."""
        return news_blackout_now()

    def _vol_reading(self) -> Optional[float]:
        """Calibrated probability of high volatility, or None when unavailable."""
        if self.vol_model is None:
            return None
        try:
            bars_by_tf = {tf: self.api.candles(tf, limit=220).get("payload", {})
                          .get("bars", []) for tf in TIMEFRAMES}
        except ApiError:
            return None
        return self.vol_model.predict(bars_by_tf)

    def _vol_command(self) -> str:
        if self.vol_model is None:
            return "🌊 توقّع التقلّب غير مفعّل على هذا الخادم."
        p = self._vol_reading()
        if p is None:
            return ("🌊 تقلّب السوق\nلا بيانات كافية الآن (الجسر غير متاح أو "
                    "الشمعة الأخيرة لم تُغلق بعد).")
        pct = round(p * 100)
        tier = "مرتفع" if p >= self.vol_model.threshold else (
            "متوسط" if p >= 0.4 else "هادئ")
        lines = [f"🌊 توقّع التقلّب: {pct}% ({tier})",
                 "هذا يقيس *حجم* الحركة القادمة، لا اتجاهها."]
        if p >= self.vol_model.threshold:
            lines.append(f"عند {round(self.vol_model.threshold * 100)}%+ يُفضَّل "
                         "تجنّب الصفقات الضيقة.")
        news = self._news_blackout()
        if news:
            lines.append(news)
        return "\n".join(lines)

    def _card_command(self) -> str:
        """The current multi-timeframe trade card, on demand.

        The body is the advisor's own ``mtf_read.format_card`` — the same
        renderer the push alert reuses — so the on-demand reply and the pushed
        text cannot drift apart.
        """
        if mtf_read is None:
            return "🎯 بطاقة الصفقة غير متاحة على هذا الخادم (المستشار غير منصّب)."
        try:
            card = self.api.trade_card()
        except ApiError:
            return "🎯 تعذّر قراءة بطاقة الصفقة (المستشار غير متاح الآن)."
        return "🎯 بطاقة الصفقة\n" + mtf_read.format_card(card)

    def _news_command(self) -> str:
        """The live news read: window, schedule, tape and headlines."""
        d = news_digest()
        if d is None:
            return "📰 الأخبار غير متاحة على هذا الخادم (المستشار غير منصّب)."
        return format_news(d)

    def _list_levels(self, chat_id: str) -> str:
        if self.levels is None:
            return "المستويات السعرية غير متاحة."
        items = self.levels.list(chat_id)
        if not items:
            return "لا مستويات بعد. أضف واحدًا: /level 4180 فوق"
        lines = ["[ASTRA] مستوياتك السعرية"]
        for lv in items:
            state = "مفعّل" if not lv.fired else "انطلق"
            lines.append(f"  {lv.price:g} ({_dir_ar(lv.direction)}) - {state}")
        return "\n".join(lines)

    def handle(self, chat_id: str, text: str) -> str:
        raw = text.strip()
        cmd = raw.split()[0].lower() if raw else ""
        if cmd.startswith("/"):
            cmd = cmd.split("@", 1)[0]  # strip @BotName suffix
        # A tapped keyboard label works exactly like typing its command, so the
        # sub-parsers (e.g. /level) see a clean command and not the label text.
        if raw in BUTTON_COMMANDS:
            cmd = BUTTON_COMMANDS[raw]
            text = cmd
        try:
            if cmd in ("/start", "/help"):
                return self.HELP
            if cmd == "/myid":
                return (f"🆔 معرّف محادثتك: {chat_id}\n"
                        "أرسله مع الدفع لتفعيل Pro.")
            if cmd == "/grant" and self.ent is not None and self.ent.is_admin(chat_id):
                parts = text.split()
                if len(parts) < 3:
                    return "الصيغة: /grant <chat_id> <pro|free>"
                target, tier = parts[1], parts[2].lower()
                try:
                    self.ent.set_tier(target, tier)
                except ValueError:
                    return "الخطة يجب أن تكون pro أو free."
                return f"{target} صار {tier}."
            if cmd == "/plan":
                if self.ent is None:
                    return "الخطة: (مستوى واحد)"
                tier = self.ent.tier(chat_id)
                cap = self.ent.max_levels(chat_id)
                tier_ar = "مدفوع (Pro)" if tier == "pro" else "مجاني"
                lines = [f"[ASTRA] خطتك: {tier_ar}",
                         f"المستويات السعرية: حتى {cap}"]
                if not self.ent.is_pro(chat_id):
                    lines.append(self.ent.upgrade_line())
                return "\n".join(l for l in lines if l)
            if cmd == "/price":
                price = self.api.last_price()
                if price is None:
                    return "💰 سعر الذهب\nلا يوجد سعر حي الآن."
                return f"💰 XAUUSD {price:,.2f}"
            if cmd == "/level":
                return self._add_level(chat_id, text)
            if cmd == "/levels":
                return self._list_levels(chat_id)
            if cmd == "/clearlevels":
                n = self.levels.clear(chat_id) if self.levels else 0
                return f"🗑 تم مسح {n} مستوى."
            if cmd == "/status":
                return format_status(self.api.snapshot())
            if cmd == "/vol":
                return self._vol_command()
            if cmd == "/card":
                return self._card_command()
            if cmd == "/news":
                return self._news_command()
            if cmd == "/health":
                return ("[ASTRA] الصحة\n" +
                        json.dumps(self.api.health(), indent=2, ensure_ascii=False))
            if cmd == "/analysis":
                return format_analysis(self.api.latest_analysis())
            if cmd == "/signal":
                return format_signal(self.api.latest_signal())
            if cmd == "/bridge":
                return format_bridge(self.api.bridge_status())
            if cmd == "/subscribe":
                gate = self._gated(chat_id)
                if gate:
                    return ("التنبيهات الفورية جزء من Pro. مستوياتك السعرية "
                            "تعمل بـ /levels.\n" + gate)
                self.subs.add(chat_id)
                return "🔕 تم الاشتراك. ستصلك تنبيهات تغيّر الحالة."
            if cmd == "/unsubscribe":
                self.subs.remove(chat_id)
                return "🔔 تم إيقاف الاشتراك. لا مزيد من التنبيهات."
            if cmd:
                return "أمر غير معروف. جرّب /help."
        except ApiError as exc:
            return f"[ASTRA] الخادم غير متاح: {exc}"
        return ""


# ──────────────────────────────── main loop ─────────────────────────────────


def _push_mirror(text: str) -> None:
    """Mirror an alert to Web Push subscribers, best-effort.

    The phone app subscribes with /push/subscribe; this delivers the same alert
    it would get in Telegram as a real push, so a backgrounded app still
    notifies. Silently a no-op when webpush is missing or unconfigured — the
    Telegram path is the one that must never fail.
    """
    if webpush is None:
        return
    try:
        webpush.broadcast({"title": "ASTRA", "body": text, "tag": "astra-alert"})
    except Exception as exc:  # noqa: BLE001 - push must never break the bot
        print(f"[warn] web push: {exc}", file=sys.stderr)


def _broadcast(client: TelegramClient, subs: Subscriptions, text: str) -> None:
    for chat in subs.all():
        try:
            client.send_message(chat, text)
        except RuntimeError as exc:
            print(f"[warn] send to {chat} failed: {exc}", file=sys.stderr)
    _push_mirror(text)


def poll_updates(client: TelegramClient, router: CommandRouter) -> None:
    offset: Optional[int] = None
    while not _stop.is_set():
        try:
            updates = client.get_updates(offset)
        except RuntimeError as exc:
            print(f"[warn] getUpdates: {exc}", file=sys.stderr)
            _stop.wait(3)
            continue
        for upd in updates:
            offset = upd.get("update_id", 0) + 1
            message = upd.get("message") or {}
            chat = message.get("chat") or {}
            chat_id = str(chat.get("id", ""))
            text = message.get("text", "")
            if not chat_id or not text:
                continue
            reply = router.handle(chat_id, text)
            if reply:
                show_kb = text.strip().lower().split()[0] in _SHOW_KEYBOARD \
                    if text.strip() else False
                try:
                    client.send_message(chat_id, reply, keyboard=show_kb)
                except RuntimeError as exc:
                    print(f"[warn] reply to {chat_id} failed: {exc}", file=sys.stderr)


def _fetch_news_events(now: float):
    """One calendar fetch covering both the blackout window and the digest."""
    if calendar_feed is None:
        return []
    lookback = getattr(news_engine, "LOOKBACK_H", 12) if news_engine else 12
    lookahead = getattr(news_engine, "LOOKAHEAD_H", 48) if news_engine else 48
    try:
        return calendar_feed.fetch_events(now - lookback * 3600,
                                          now + lookahead * 3600)
    except Exception:  # noqa: BLE001
        return []


def news_digest(now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """The full news read (window, schedule, tape, headlines), or None.

    Shares `news_engine.digest` with the web panel, so the bot and the page
    cannot disagree. Best-effort: a missing engine yields None, never a crash.
    """
    if news_engine is None:
        return None
    now = time.time() if now is None else now
    try:
        return news_engine.digest(now, events=_fetch_news_events(now))
    except Exception:  # noqa: BLE001
        return None


def news_snapshot(now: Optional[float] = None):
    """One calendar fetch -> (blackout_reason, recent_context).

    Both features read the same event list, so the monitor pays for one fetch
    per cycle. Either element is None when there is nothing to say.
    """
    if news_window is None:
        return None, None
    now = time.time() if now is None else now
    events = _fetch_news_events(now)
    if not events:
        return None, None
    return (news_window.blackout_reason(events, now),
            news_window.recent_context(events, now))


def news_blackout_now(now: Optional[float] = None) -> Optional[str]:
    """The news-quiet reason for `now` (default: the clock), or None.

    Best-effort: a dead calendar yields no reason, never a crash and never a
    fabricated warning. Returns None when the advisor is not deployed.
    """
    return news_snapshot(now)[0]


class NewsWatch:
    """Edge-triggered alerts around a high-impact release.

    Two alerts, each fired once per event: when the blackout window *opens*
    (a release is imminent — stop trading), and when a release *prints* (the
    actual is out — here is the mechanical gold read). Keyed by event identity,
    so a release is never announced twice, and primed on first poll so starting
    the bot mid-window does not replay an alert the operator already saw.

    The calendar is fetched by the monitor and passed in, so the watch adds no
    network cost of its own.
    """

    def __init__(self) -> None:
        self._primed = False
        self._warned: Set[Any] = set()
        self._reported: Set[Any] = set()

    @staticmethod
    def _key(event: Dict[str, Any]) -> Any:
        return (event.get("at_utc"), event.get("title"), event.get("country"))

    def poll(self, events: Optional[List[Dict[str, Any]]] = None,
             now: Optional[float] = None) -> List[str]:
        if news_window is None:
            return []
        now = time.time() if now is None else now
        if events is None:
            events = _fetch_news_events(now)

        active = news_window.blackout_event(events, now)
        if not self._primed:
            # Learn the current state silently: an already-open window (or an
            # already-printed release) must not fire the moment the bot starts.
            if active is not None:
                self._warned.add(self._key(active))
            for event in events:
                if calendar_feed is not None and calendar_feed.classify(event):
                    self._reported.add(self._key(event))
            self._primed = True
            return []

        out: List[str] = []
        # The warning is for a release still ahead (an open window already
        # announced, or one that just printed, is covered by the print alert
        # below). Keying both alerts by event identity means one release never
        # produces two messages.
        if active is not None:
            at = active.get("at_utc")
            if at is not None and at > now:
                key = self._key(active)
                if key not in self._warned:
                    self._warned.add(key)
                    label = active.get("title_ar") or active.get("title") or "خبر"
                    mins = round((at - now) / 60.0)
                    out.append(
                        "⏸ نافذة خبر: %s (%s) بعد ~%d دقيقة.\n"
                        "السوق عادةً عنيف حول الصدور — يُفضَّل عدم فتح صفقة "
                        "ضيقة الآن." % (label, active.get("country") or "—", mins))

        if calendar_feed is not None:
            for event in events:
                read = calendar_feed.classify(event)
                if not read or read.get("surprise") == "unknown":
                    continue
                key = self._key(event)
                if key in self._reported:
                    continue
                self._reported.add(key)
                if news_window.is_relevant(event):
                    out.append("💱 صدر للتو — %s\n%s" % (
                        event.get("title_ar") or event.get("title"),
                        calendar_feed.format_release(event, read)))
        return out


class TradeCardWatch:
    """Edge-triggered alerts when the advisor publishes a *new* trade card.

    The advisor recomputes its multi-timeframe read on its own TTL, so polling
    it every cycle would re-announce the same setup. A card is identified by its
    direction and the exact levels it was built from; only a card whose identity
    differs from the last one announced fires, so a still-open setup is quiet
    and a fresh one (a new entry/stop/target) is news. A card is only announced
    when a direction holds — a NEUTRAL read has no numbers and never alerts.

    Read-only, like everything else here: it announces a level, it never acts on
    one, and it carries no execution authority.
    """

    def __init__(self) -> None:
        self._primed = False
        self._last: Optional[tuple] = None

    @staticmethod
    def _key(card: Dict[str, Any]) -> Optional[tuple]:
        if not card.get("card"):
            return None
        return (card.get("verdict"), card["card"].get("entry"),
                card["card"].get("stop"), card["card"].get("target"))

    def poll(self, card: Optional[Dict[str, Any]] = None) -> List[str]:
        if card is None:
            return []
        key = self._key(card)
        if not self._primed:
            # Learn the current card silently: a setup already open when the
            # bot starts must not fire the moment it comes online.
            self._last = key
            self._primed = True
            return []
        if key is None or key == self._last:
            return []
        self._last = key
        return [format_trade_card(card)]


def format_trade_card(card: Dict[str, Any]) -> str:
    """Arabic push alert for a genuinely new card.

    Delegates the body to the advisor's own ``mtf_read.format_card`` so the
    pushed text and the ``/card`` reply can never drift apart, and adds a
    header plus an explicit no-execution line. A reward:risk below 1 is
    surfaced rather than hidden, so a poor setup is never dressed up as good.
    """
    lines = ["🎯 بطاقة صفقة جديدة", mtf_read.format_card(card)]
    rr = (card.get("card") or {}).get("rr")
    if isinstance(rr, (int, float)) and rr < 1:
        lines.append("⚠️ العائد/المخاطرة أقل من 1 — المخاطرة أكبر من العائد.")
    lines.append("لا تنفيذ تلقائي — إشعار فقط.")
    return "\n".join(lines)


def monitor_backend(api: AstraApi, subs: Subscriptions, client: TelegramClient,
                    interval: int, levels: Optional[LevelStore] = None,
                    ent: Optional[Entitlements] = None,
                    vol_model: Optional[VolatilityModel] = None,
                    card_watch: Optional["TradeCardWatch"] = None) -> None:
    engine = AlertEngine(api)
    engine.prime()
    watch = NewsWatch()
    vol_was_high = False
    while not _stop.is_set():
        if _stop.wait(interval):
            break
        try:
            for alert in engine.poll():
                _broadcast(client, subs, alert)
        except Exception as exc:  # noqa: BLE001 - never let the monitor thread die silently
            print(f"[warn] monitor: {exc}", file=sys.stderr)
        news_reason, news_context = news_snapshot()
        try:
            for alert in watch.poll():
                _broadcast(client, subs, alert)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] news watch: {exc}", file=sys.stderr)
        if levels is not None:
            try:
                _check_levels(api, levels, ent, client, news_context)
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] levels: {exc}", file=sys.stderr)
        if vol_model is not None:
            try:
                vol_was_high = _check_volatility(api, vol_model, subs, client,
                                                 vol_was_high, news_reason)
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] volatility: {exc}", file=sys.stderr)
        if card_watch is not None:
            try:
                _check_trade_card(api, card_watch, subs, client, news_context)
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] trade card: {exc}", file=sys.stderr)


def _check_volatility(api: AstraApi, model: VolatilityModel,
                      subs: Subscriptions, client: TelegramClient,
                      was_high: bool, news_reason: Optional[str] = None) -> bool:
    """Alert once when volatility crosses the threshold (edge-triggered).

    Returns the new high/low state so the caller can re-arm after the market
    calms down, instead of re-alerting every cycle. ``news_reason`` is appended
    when a high-impact release is imminent or just printed.
    """
    try:
        bars_by_tf = {tf: api.candles(tf, limit=220).get("payload", {})
                      .get("bars", []) for tf in TIMEFRAMES}
    except ApiError:
        return was_high
    p = model.predict(bars_by_tf)
    if p is None:
        return was_high
    is_high = p >= model.threshold
    if is_high and not was_high:
        text = format_volatility_alert(p, model.threshold)
        if news_reason:
            text = text + "\n" + news_reason
        _broadcast(client, subs, text)
    return is_high


def _check_trade_card(api: AstraApi, watch: "TradeCardWatch",
                      subs: Subscriptions, client: TelegramClient,
                      news_context: Optional[str] = None) -> None:
    """Fetch the advisor's card and broadcast it when it is genuinely new.

    A NEUTRAL read carries no levels and is never announced; an unreachable
    advisor is silent (the watch keeps its last identity, so the same setup is
    not re-sent when it comes back). ``news_context`` explains *why* a fresh
    setup may be unsafe right now.
    """
    try:
        card = api.trade_card()
    except ApiError:
        return
    for alert in watch.poll(card):
        if news_context:
            alert = alert + "\n" + news_context
        _broadcast(client, subs, alert)


def _check_levels(api: AstraApi, levels: LevelStore,
                  ent: Optional[Entitlements], client: TelegramClient,
                  news_context: Optional[str] = None) -> None:
    """Poll the live price once and alert every chat whose level was crossed.

    ``news_context`` is appended to the alert so a break is explained by the
    release that likely caused it — the level feature's "interpretation 1".
    """
    price = api.last_price()
    if price is None:
        return
    for chat_id in levels.chats():
        for lv in levels.observe(chat_id, price):
            arrow = "صعودًا عبر" if lv.last_side == "above" else "هبوطًا عبر"
            line = (f"🔔 تنبيه سعري: XAUUSD تحرّك {arrow} {lv.price:g}\n"
                    f"الآن {price:,.2f}. هذا المستوى انتهى؛ أضف غيره بـ /level.")
            if news_context:
                line = line + "\n" + news_context
            try:
                client.send_message(chat_id, line)
            except RuntimeError as exc:
                print(f"[warn] level alert to {chat_id} failed: {exc}",
                      file=sys.stderr)
            _push_mirror(line)


def parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ASTRA Telegram bot")
    p.add_argument("--token", default=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
                   help="Telegram bot token (default: $TELEGRAM_BOT_TOKEN)")
    p.add_argument("--chat", default=os.environ.get("TELEGRAM_CHAT_ID", ""),
                   help="default chat id to notify (default: $TELEGRAM_CHAT_ID)")
    p.add_argument("--api", default=os.environ.get("ASTRA_API_BASE",
                                                   "http://127.0.0.1:8790/api/v1"),
                   help="ASTRA backend API base URL")
    p.add_argument("--interval", type=int, default=15,
                   help="backend poll interval in seconds")
    p.add_argument("--state", default=str(Path.home() / ".astra" / "telegram_subscribers.json"),
                   help="subscriber store path")
    p.add_argument("--no-card-alerts", action="store_true",
                   default=os.environ.get("ASTRA_CARD_ALERTS", "1") == "0",
                   help="disable push alerts for new advisor trade cards")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if not args.token:
        print("[ERROR] no bot token (--token or $TELEGRAM_BOT_TOKEN)", file=sys.stderr)
        return 2

    client = TelegramClient(args.token)
    api = AstraApi(args.api)
    subs = Subscriptions(Path(args.state))
    state_dir = Path(args.state).parent
    levels = LevelStore(state_dir / "price_levels.json")
    ent = entitlements_from_env(state_dir)
    # Liveness beacon: the phone server reads this file to answer /bot.json, so
    # "is the bot actually polling?" is observable. Without it a dead poller is
    # silent — the watchdog only watched the GitHub scanner, never this process.
    beat = state_dir / "bot_heartbeat.json"
    try:
        beat.parent.mkdir(parents=True, exist_ok=True)
        beat.write_text(json.dumps({"pid": os.getpid(), "at": time.time()}),
                        encoding="utf-8")
    except OSError as exc:
        print(f"[warn] heartbeat unavailable: {exc}", file=sys.stderr)
    try:
        vol_model = VolatilityModel.load()
    except (OSError, ValueError, KeyError) as exc:
        print(f"[warn] volatility model unavailable: {exc}", file=sys.stderr)
        vol_model = None
    # The default chat is always subscribed so the operator gets alerts without
    # having to send /subscribe first.
    if args.chat:
        subs.add(args.chat)
    router = CommandRouter(api, subs, args.chat or None, levels=levels, ent=ent,
                           vol_model=vol_model)
    card_watch = None if args.no_card_alerts else TradeCardWatch()

    for sig in (_signal.SIGINT, _signal.SIGTERM):
        _signal.signal(sig, lambda *_: _stop.set())

    print(f"[OK] ASTRA bot online | api={args.api} | subscribers={len(subs.all())}"
          f" | selling={ent.selling}", flush=True)

    # Register the command menu and the menu button. This overwrites anything
    # that was set by hand in BotFather, so the app always shows the real list.
    try:
        client.set_my_commands()
        client.set_menu_button()
    except RuntimeError as exc:
        print(f"[warn] could not register commands: {exc}", file=sys.stderr)

    if args.chat:
        try:
            client.send_message(args.chat, "🤖 ASTRA جاهز. أرسل /help أو استخدم الأزرار.",
                                keyboard=True)
        except RuntimeError as exc:
            print(f"[warn] startup message failed: {exc}", file=sys.stderr)

    def _heartbeat() -> None:
        while not _stop.wait(60):
            with contextlib.suppress(OSError):
                beat.write_text(
                    json.dumps({"pid": os.getpid(), "at": time.time()}),
                    encoding="utf-8")

    threading.Thread(target=_heartbeat, daemon=True).start()

    monitor = threading.Thread(target=monitor_backend,
                               args=(api, subs, client, args.interval, levels, ent,
                                     vol_model, card_watch),
                               daemon=True)
    monitor.start()
    try:
        poll_updates(client, router)
    finally:
        _stop.set()
    print("ASTRA bot stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
