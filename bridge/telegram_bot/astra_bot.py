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

TELEGRAM_API = "https://api.telegram.org"
_POLL_TIMEOUT_SECONDS = 25
_SEND_TIMEOUT_SECONDS = 10

_stop = threading.Event()


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

    def send_message(self, chat_id: str, text: str) -> None:
        self._call("sendMessage",
                   {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
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
    return "yes" if value else "no"


def format_status(snap: Dict[str, Any]) -> str:
    health = snap.get("health", {})
    system = snap.get("system", {})
    bridge = snap.get("bridge", {})

    def err(d: Dict[str, Any]) -> str:
        return f"unavailable ({d['_error']})" if "_error" in d else ""

    lines = ["[ASTRA] Status"]
    if "_error" in health:
        lines.append(f"Backend: {err(health)}")
    else:
        lines.append(f"Health: {health.get('aggregate', 'UNKNOWN')} "
                     f"(decision-grade data: {_yn(health.get('decision_grade_data'))})")
    if "_error" not in system:
        lines.append(f"Mode: {system.get('mode', 'UNKNOWN')} "
                     f"(shadow only: {_yn(system.get('shadow_only'))})")
        lines.append(f"Startup: {system.get('startup_stage', 'UNKNOWN')} "
                     f"(ready: {_yn(system.get('ready'))})")
    if "_error" not in bridge:
        symbol = bridge.get("resolved_symbol") or "(none)"
        lines.append(f"MT5 bridge: {bridge.get('bridge_process_state', 'UNKNOWN')} "
                     f"(mt5 ready: {_yn(bridge.get('mt5_ready'))}, symbol: {symbol})")
    lines.append("Shadow mode. Not a trading signal.")
    return "\n".join(lines)


def format_analysis(data: Dict[str, Any]) -> str:
    signal = data.get("signal", {}) or {}
    context = data.get("context", {}) or {}
    if not data.get("timestamp"):
        return ("[ASTRA] Analysis\nNo decision recorded yet "
                "(waiting for decision-grade data).")
    lines = [
        "[ASTRA] Analysis",
        f"Direction: {signal.get('direction', 'NONE')}",
        f"Regime: {context.get('regime', 'UNKNOWN')} "
        f"(H4 {context.get('h4_bias', 'UNKNOWN')}, M15 {context.get('m15_trigger', 'UNKNOWN')})",
    ]
    score = signal.get("score")
    if score is not None:
        lines.append(f"Score: {score}")
    prob = signal.get("probability")
    if prob is not None:
        lines.append(f"Probability: {prob} (calibrated: {_yn(signal.get('probability_calibrated'))})")
    lines.append("Decision support only. Not financial advice.")
    return "\n".join(lines)


def format_signal(data: Dict[str, Any]) -> str:
    if not data.get("available", True):
        return ("[ASTRA] Signals\nNone available: "
                f"{data.get('reason', 'no decisions recorded yet')}.")
    return ("[ASTRA] Latest signal\n" + json.dumps(data, indent=2, ensure_ascii=False))


def format_bridge(data: Dict[str, Any]) -> str:
    return "\n".join([
        "[ASTRA] MT5 bridge",
        f"Process: {data.get('bridge_process_state', 'UNKNOWN')}",
        f"MT5 ready: {_yn(data.get('mt5_ready'))}",
        f"Symbol: {data.get('resolved_symbol') or '(none)'}",
        f"Last error: {data.get('last_error') or '(none)'}",
    ])


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
                    f"[ASTRA] Health changed: {self._last_health} -> {h} "
                    f"(decision-grade data: {_yn(health.get('decision_grade_data'))})")
            self._last_health = h

        b = str(bridge.get("bridge_process_state", "")) or None
        if b and b != self._last_bridge:
            if emit and self._last_bridge is not None:
                alerts.append(f"[ASTRA] MT5 bridge: {self._last_bridge} -> {b} "
                              f"(mt5 ready: {_yn(bridge.get('mt5_ready'))})")
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
                 default_chat: Optional[str]) -> None:
        self.api = api
        self.subs = subs
        self.default_chat = default_chat

    HELP = (
        "[ASTRA] Commands\n"
        "/status - backend health, mode, bridge\n"
        "/health - health detail\n"
        "/analysis - latest analysis\n"
        "/signal - latest signal\n"
        "/bridge - MT5 bridge status\n"
        "/subscribe - receive push alerts\n"
        "/unsubscribe - stop push alerts\n"
        "/help - this list\n\n"
        "Shadow mode: decision support only, never a trading signal."
    )

    def handle(self, chat_id: str, text: str) -> str:
        cmd = text.strip().split()[0].lower() if text.strip() else ""
        if cmd.startswith("/"):
            cmd = cmd.split("@", 1)[0]  # strip @BotName suffix
        try:
            if cmd in ("/start", "/help"):
                return self.HELP
            if cmd == "/status":
                return format_status(self.api.snapshot())
            if cmd == "/health":
                return ("[ASTRA] Health\n" +
                        json.dumps(self.api.health(), indent=2, ensure_ascii=False))
            if cmd == "/analysis":
                return format_analysis(self.api.latest_analysis())
            if cmd == "/signal":
                return format_signal(self.api.latest_signal())
            if cmd == "/bridge":
                return format_bridge(self.api.bridge_status())
            if cmd == "/subscribe":
                self.subs.add(chat_id)
                return "[ASTRA] Subscribed. You will receive state-change alerts."
            if cmd == "/unsubscribe":
                self.subs.remove(chat_id)
                return "[ASTRA] Unsubscribed. No more push alerts."
            if cmd:
                return "Unknown command. Try /help."
        except ApiError as exc:
            return f"[ASTRA] Backend unavailable: {exc}"
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
                try:
                    client.send_message(chat_id, reply)
                except RuntimeError as exc:
                    print(f"[warn] reply to {chat_id} failed: {exc}", file=sys.stderr)


def monitor_backend(api: AstraApi, subs: Subscriptions, client: TelegramClient,
                    interval: int) -> None:
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
    # The default chat is always subscribed so the operator gets alerts without
    # having to send /subscribe first.
    if args.chat:
        subs.add(args.chat)
    router = CommandRouter(api, subs, args.chat or None)

    for sig in (_signal.SIGINT, _signal.SIGTERM):
        _signal.signal(sig, lambda *_: _stop.set())

    print(f"[OK] ASTRA bot online | api={args.api} | subscribers={len(subs.all())}",
          flush=True)
    if args.chat:
        try:
            client.send_message(args.chat, "[ASTRA] Bot online. Send /help.")
        except RuntimeError as exc:
            print(f"[warn] startup message failed: {exc}", file=sys.stderr)

    monitor = threading.Thread(target=monitor_backend,
                               args=(api, subs, client, args.interval), daemon=True)
    monitor.start()
    try:
        poll_updates(client, router)
    finally:
        _stop.set()
    print("ASTRA bot stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
