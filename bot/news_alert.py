#!/usr/bin/env python3
"""ASTRA news alert: ping Telegram shortly before a high-impact release.

One-shot, scheduler-friendly (GitHub Actions cron), no server and no daemon:

  * It reads the same keyless economic calendar the advisor uses, so the alert
    and the analysis can never disagree about what is coming.
  * It alerts once per event, when the event is inside the lead window, and
    remembers the ones already sent in a small state file. A late or skipped
    cron slot changes latency only, never whether the alert goes out.

The point is the "do not trade into the release" habit: the message names the
event in Arabic and tells the reader to wait for the first candle to settle.

Run: python3 bot/news_alert.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for extra in (os.path.join(ROOT, "advisor"), HERE):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import ai_advisor as advisor  # noqa: E402
import telegram_bot as bot  # noqa: E402

# Alert when the event is within this many hours ahead. With a cron every 15
# minutes a one-hour window catches every event at least once.
LEAD_HOURS = float(os.environ.get("ASTRA_NEWS_LEAD_HOURS", "1.0"))
STATE_PATH = os.environ.get("ASTRA_NEWS_STATE",
                            os.path.join(ROOT, "run", "news_alert_state.json"))


def event_key(event: dict) -> str:
    return "%s:%s:%s" % (event.get("country"), event.get("title"),
                         event.get("at_utc"))


def due_alerts(events: list[dict], alerted: set[str],
               lead_hours: float = LEAD_HOURS) -> list[dict]:
    """Events inside the lead window that have not been alerted yet."""
    due = []
    for event in events:
        hours = event.get("in_hours")
        if hours is None or hours <= 0 or hours > lead_hours:
            continue
        if event_key(event) in alerted:
            continue
        due.append(event)
    due.sort(key=lambda e: e.get("at_utc") or 0)
    return due


def format_alert(event: dict) -> str:
    at = event.get("at_utc")
    when = (datetime.fromtimestamp(at, timezone.utc).strftime("%H:%M UTC")
            if at else "—")
    minutes = int(round((event.get("in_hours") or 0) * 60))
    lines = [
        "⏰ تنبيه خبر مهم — بعد %d دقيقة" % minutes,
        "الحدث: %s" % (event.get("title_ar") or event.get("title")),
        "العملة: %s · الوقت: %s" % (event.get("country"), when),
    ]
    if event.get("forecast"):
        lines.append("التوقع: %s · السابق: %s"
                     % (event.get("forecast"), event.get("previous") or "—"))
    lines.append("⚠️ لا تدخل قبل الخبر. انتظر 5–15 دقيقة حتى تهدأ أول شمعة.")
    return "\n".join(lines)


def load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, STATE_PATH)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ASTRA news alert")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the alerts instead of sending them")
    args = parser.parse_args(argv)

    events = advisor.fetch_calendar()
    state = load_state()
    alerted = set(state.get("alerted") or [])
    due = due_alerts(events, alerted)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    print(json.dumps({"events": len(events), "due": len(due),
                      "titles": [e.get("title") for e in due]},
                     ensure_ascii=False))

    if args.dry_run or not token or not chat:
        for event in due:
            print(format_alert(event))
        if not (token and chat) and not args.dry_run:
            sys.stderr.write("[news] no telegram credentials; dry run only\n")
        return 0

    tg = bot.Telegram(token)
    sent = []
    for event in due:
        if tg.send(chat, format_alert(event)):
            sent.append(event_key(event))
    if sent:
        state["alerted"] = sorted(alerted | set(sent))
        save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
