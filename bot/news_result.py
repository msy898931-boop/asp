#!/usr/bin/env python3
"""ASTRA result alert: say what a release actually did, the moment it prints.

The pre-release alert (`news_alert.py`) reads the Forex Factory mirror, which
carries only forecast and previous. Once an event is out, the interesting fact
is the *actual* value — and this job reads it from the keyless TradingView
calendar, which has it.

It sends one Arabic message per released, market-relevant event: the actual
against the forecast (or the previous, and says so), whether that made the
currency stronger or weaker, and — for a US event only — the usual gold
pressure. The read is mechanical and always names its baseline; it never
asserts a direction it cannot compare.

One-shot, scheduler-friendly. State (which events were already sent) rides in a
small file, so a late or skipped cron slot changes latency only.

Run: python3 bot/news_result.py            # send
     python3 bot/news_result.py --dry-run  # print
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for extra in (os.path.join(ROOT, "advisor"), HERE):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import calendar_feed as cal  # noqa: E402
import telegram_bot as bot  # noqa: E402

# How far back to look for a release the alert may have missed. A cron every 15
# minutes with a three-hour window cannot lose an event to a slow slot.
LOOKBACK_H = float(os.environ.get("ASTRA_RESULT_LOOKBACK_H", "3"))
STATE_PATH = os.environ.get("ASTRA_RESULT_STATE",
                            os.path.join(ROOT, "run", "news_result_state.json"))

# Indicators that move gold even when the calendar calls them medium. A high
# print of any of these is worth a message; a trivial medium is not.
RELEVANT_KEYS = (
    "adp", "nonfarm", "payroll", "employment", "unemployment", "jobless",
    "cpi", "ppi", "pce", "inflation", "retail sales", "interest rate",
    "federal funds", "gdp", "ism", "pmi", "consumer confidence",
    "durable goods", "industrial production", "philly fed",
)


def event_key(event: dict) -> str:
    return "%s:%s:%s" % (event.get("country"), event.get("title"),
                         event.get("at_utc"))


def is_relevant(event: dict) -> bool:
    """High-impact, or a medium whose indicator is known to move gold."""
    imp = event.get("importance")
    if isinstance(imp, (int, float)) and imp >= cal.HIGH:
        return True
    low = (event.get("title") or "").lower()
    return any(key in low for key in RELEVANT_KEYS)


def due_results(events: list[dict], alerted: set[str], now: float,
                lookback_h: float = LOOKBACK_H) -> list[dict]:
    """Released, relevant events not yet alerted, newest first."""
    out = []
    for event in cal.released(events, now, lookback_h, min_importance=cal.LOW):
        if not is_relevant(event):
            continue
        if event_key(event) in alerted:
            continue
        out.append(event)
    return out


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
    parser = argparse.ArgumentParser(description="ASTRA released-event alert")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the messages instead of sending them")
    args = parser.parse_args(argv)

    now = time.time()
    events = cal.fetch_events(now - LOOKBACK_H * 3600, now + 60)
    state = load_state()
    alerted = set(state.get("alerted") or [])
    due = due_results(events, alerted, now)

    print(json.dumps({"events": len(events), "due": len(due),
                      "titles": [e.get("title") for e in due]},
                     ensure_ascii=False))

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    live = bool(token and chat) and not args.dry_run
    tg = bot.Telegram(token) if live else None

    sent = list(alerted)
    for event in due:
        text = cal.format_release(event, cal.classify(event) or {})
        if tg is not None:
            if tg.send(chat, text):
                sent.append(event_key(event))
            else:
                sys.stderr.write("[result] send failed for %s\n" % event.get("title"))
        else:
            print(text)
            print("-" * 60)
            sent.append(event_key(event))

    # Keep the store bounded: only the most recent keys matter.
    if live:
        save_state({"alerted": sent[-500:]})
    return 0


if __name__ == "__main__":
    sys.exit(main())
