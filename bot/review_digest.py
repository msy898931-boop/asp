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
import astra_env  # noqa: E402

# Where the advisor lives. Defaults to the deployed service; point it at
# loopback for local runs with ASTRA_REVIEW_URL.
ADVISOR_URL = (os.environ.get("ASTRA_REVIEW_URL") or
               astra_env.URL + "/advisor/v1/review")
HTTP_TIMEOUT = float(os.environ.get("ASTRA_REVIEW_TIMEOUT", "30"))
_USER_AGENT = "ASTRA-review/1.0"

# Returned when the access gate answers 401: the advisor may be perfectly
# healthy, this run just has no key. Distinct from None ("unreachable").
_GATE = object()


def _get_json(url: str):
    try:
        req = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": _USER_AGENT,
                          **astra_env.auth_headers()})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            # The access gate is on and this run has no (or a wrong) token.
            sys.stderr.write("[review] access gate without a token (HTTP %d)\n" % exc.code)
            return _GATE
        return None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def fetch_review(url: str = ADVISOR_URL) -> dict | None:
    payload = _get_json(url)
    if payload is _GATE:
        return _GATE
    if not isinstance(payload, dict):
        return None
    return payload.get("review") or None


def poke(url: str = ADVISOR_URL) -> bool:
    """Ask the advisor for a fresh call so today lands in the review log.

    The advisor only logs a call when someone asks for one, and the app is the
    only other caller. Nudging it once a day keeps the learning loop fed even
    when the app is never opened. Best-effort: a failure never blocks the digest.
    """
    base = url.rsplit("/review", 1)[0]
    res = _get_json(base + "/advisor?refresh=1")
    return res is not None and res is not _GATE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ASTRA self-review digest")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the digest instead of sending it")
    parser.add_argument("--no-poke", action="store_true",
                        help="do not ask the advisor for a fresh call first")
    args = parser.parse_args(argv)

    review = fetch_review()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")

    poked = False
    if not args.no_poke:
        poked = poke()
    if poked:
        # A fresh call just landed in the log, so re-read for the newest numbers.
        review = fetch_review() or review

    # The gate is on but this run has no token: the advisor may be fine, so do
    # not alert "advisor unavailable" — just stay quiet and exit zero.
    if review is _GATE:
        sys.stderr.write("[review] skipping: access gate is on without a token\n")
        return 0

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
