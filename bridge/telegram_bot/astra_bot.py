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
from entitlements import Entitlements, from_env as entitlements_from_env
from price_levels import (DIR_ABOVE, DIR_BELOW, DIR_CROSS, Level, LevelStore,
                          side_of)

TELEGRAM_API = "https://api.telegram.org"
_POLL_TIMEOUT_SECONDS = 25
_SEND_TIMEOUT_SECONDS = 10

_stop = threading.Event()

# ── the user-facing surface (Arabic) ────────────────────────────────────────
# The reply keyboard the user taps. Each label maps to a command token, and the
# router understands the label as well as the token, so a tap works like typing.
REPLY_KEYBOARD: List[List[str]] = [
    ["💰 سعر الذهب", "📊 حالة السوق"],
    ["🔔 مستوى جديد", "📋 مستوياتي"],
    ["🗑 مسح المستويات", "⭐ خطتي"],
    ["❓ مساعدة"],
]

BUTTON_COMMANDS: Dict[str, str] = {
    "💰 سعر الذهب": "/price",
    "📊 حالة السوق": "/status",
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
    ("/level", "إضافة مستوى سعري للتنبيه"),
    ("/levels", "مستوياتي وحالتها"),
    ("/clearlevels", "حذف كل مستوياتي"),
    ("/status", "حالة النظام والوضع"),
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
        """Register the command menu. Overrides anything set in BotFather."""
        payload = {"commands": [{"command": c, "description": d}
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
                 ent: Optional[Entitlements] = None) -> None:
        self.api = api
        self.subs = subs
        self.default_chat = default_chat
        self.levels = levels
        self.ent = ent

    HELP = (
        "[ASTRA] الأوامر\n"
        "💰 /price - سعر الذهب الحالي\n"
        "🔔 /level <سعر> [above|below|cross] - تنبيه عند اختراق مستوى\n"
        "📋 /levels - مستوياتك\n"
        "🗑 /clearlevels - حذف كل مستوياتك\n"
        "🔕 /subscribe - تشغيل التنبيهات\n"
        "🔔 /unsubscribe - إيقاف التنبيهات\n"
        "⭐ /plan - خطتك وحدودها\n"
        "📊 /status - حالة النظام والوضع\n"
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


def _broadcast(client: TelegramClient, subs: Subscriptions, text: str) -> None:
    for chat in subs.all():
        try:
            client.send_message(chat, text)
        except RuntimeError as exc:
            print(f"[warn] send to {chat} failed: {exc}", file=sys.stderr)


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


def monitor_backend(api: AstraApi, subs: Subscriptions, client: TelegramClient,
                    interval: int, levels: Optional[LevelStore] = None,
                    ent: Optional[Entitlements] = None) -> None:
    engine = AlertEngine(api)
    engine.prime()
    while not _stop.is_set():
        if _stop.wait(interval):
            break
        try:
            for alert in engine.poll():
                _broadcast(client, subs, alert)
        except Exception as exc:  # never let the monitor thread die silently
            print(f"[warn] monitor: {exc}", file=sys.stderr)
        if levels is not None:
            try:
                _check_levels(api, levels, ent, client)
            except Exception as exc:
                print(f"[warn] levels: {exc}", file=sys.stderr)


def _check_levels(api: AstraApi, levels: LevelStore,
                  ent: Optional[Entitlements], client: TelegramClient) -> None:
    """Poll the live price once and alert every chat whose level was crossed."""
    price = api.last_price()
    if price is None:
        return
    for chat_id in levels.chats():
        for lv in levels.observe(chat_id, price):
            arrow = "صعودًا عبر" if lv.last_side == "above" else "هبوطًا عبر"
            line = (f"🔔 تنبيه سعري: XAUUSD تحرّك {arrow} {lv.price:g}\n"
                    f"الآن {price:,.2f}. هذا المستوى انتهى؛ أضف غيره بـ /level.")
            try:
                client.send_message(chat_id, line)
            except RuntimeError as exc:
                print(f"[warn] level alert to {chat_id} failed: {exc}",
                      file=sys.stderr)


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
    # The default chat is always subscribed so the operator gets alerts without
    # having to send /subscribe first.
    if args.chat:
        subs.add(args.chat)
    router = CommandRouter(api, subs, args.chat or None, levels=levels, ent=ent)

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

    monitor = threading.Thread(target=monitor_backend,
                               args=(api, subs, client, args.interval, levels, ent),
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
