#!/usr/bin/env python3
"""ASTRA self-review digest: push the advisor's accuracy to Telegram, daily.

One-shot, scheduler-friendly (GitHub Actions cron). It reads the advisor's
self-review endpoint (the same one the app shows) and sends the Arabic
scoreboard: how often the advisor was right, split by stance, and the recent
misses with their lesson.

It is a reader, never a decider: if the advisor is unreachable it says so and
exits non-zero, rather than inventing numbers.

Run: python3 bot/review_digest.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import telegram_bot as bot  # noqa: E402

# Where the advisor lives. Defaults to the public Render deployment; point it at
# loopback for local runs with ASTRA_REVIEW_URL.
ADVISOR_URL = os.environ.get("ASTRA_REVIEW_URL",
                             "https://astra-xlp9.onrender.com/advisor/v1/review")
HTTP_TIMEOUT = float(os.environ.get("ASTRA_REVIEW_TIMEOUT", "30"))
_USER_AGENT = "ASTRA-review/1.0"


def fetch_review(url: str = ADVISOR_URL) -> dict | None:
    try:
        req = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload.get("review") or None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ASTRA self-review digest")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the digest instead of sending it")
    args = parser.parse_args(argv)

    review = fetch_review()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")

    if review is None:
        sys.stderr.write("[review] advisor unreachable at %s\n" % ADVISOR_URL)
        if args.dry_run:
            return 0
        if token and chat:
            bot.Telegram(token).send(
                chat, "⚠️ تعذّر جلب المراجعة الذاتية: المستشار الذكي غير متاح.")
        return 1

    message = review.get("message") or ""
    print(json.dumps({"summary": review.get("summary"), "message": message},
                     ensure_ascii=False))

    if args.dry_run or not token or not chat:
        if not (token and chat) and not args.dry_run:
            sys.stderr.write("[review] no telegram credentials; dry run only\n")
        return 0

    ok = bot.Telegram(token).send(chat, message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
