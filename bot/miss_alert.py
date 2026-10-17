#!/usr/bin/env python3
"""ASTRA outcome alert: ping Telegram the moment the advisor is scored.

One-shot, scheduler-friendly (GitHub Actions cron, every 15 minutes). It reads
the advisor's self-review endpoint, finds scored calls (hit or miss) that have
not been alerted yet, and sends one short Arabic message each. State (which
calls were alerted) rides in a small file, so a late or skipped slot changes
latency only.

It only ever reads: the outcome log is written by the advisor itself, so this
job can never invent a result.

Run: python3 bot/miss_alert.py            # hits and misses
     python3 bot/miss_alert.py --misses-only
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
for extra in (os.path.join(ROOT, "advisor"), HERE):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import astra_env  # noqa: E402
import self_review  # noqa: E402
import telegram_bot as bot  # noqa: E402

ADVISOR_URL = (os.environ.get("ASTRA_REVIEW_URL") or
               astra_env.URL + "/advisor/v1/review")
HTTP_TIMEOUT = float(os.environ.get("ASTRA_REVIEW_TIMEOUT", "30"))
STATE_PATH = os.environ.get("ASTRA_MISS_STATE",
                            os.path.join(ROOT, "run", "miss_alert_state.json"))
_USER_AGENT = "ASTRA-miss/1.0"


def fetch_outcomes(url: str = ADVISOR_URL) -> list[dict] | None:
    try:
        req = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": _USER_AGENT,
                          **astra_env.auth_headers()})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            # The access gate is on and this run has no (or a wrong) token.
            # That is a missing credential, not a broken advisor: return no
            # outcomes so the job stays quiet instead of failing 96 times a day.
            sys.stderr.write("[miss] access gate without a token (HTTP %d)\n" % exc.code)
            return []
        return None
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    review = payload.get("review") or {}
    summary = review.get("summary") or {}
    outcomes = summary.get("outcomes")
    if isinstance(outcomes, list):
        return outcomes
    # Older advisor builds only exposed the misses; degrade to those.
    wrong = summary.get("wrong")
    return wrong if isinstance(wrong, list) else []


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
    parser = argparse.ArgumentParser(description="ASTRA miss alert")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the alerts instead of sending them")
    parser.add_argument("--misses-only", action="store_true",
                        help="alert only on mistakes, not on hits")
    args = parser.parse_args(argv)

    outcomes = fetch_outcomes()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")

    if outcomes is None:
        sys.stderr.write("[miss] advisor unreachable at %s\n" % ADVISOR_URL)
        return 1

    state = load_state()
    alerted = set(state.get("alerted") or [])
    # First run with no marks primes silently: a fresh install or lost cache
    # must not blast every past call.
    if not state.get("primed"):
        state["alerted"] = sorted({self_review.outcome_key(o) for o in outcomes})
        state["primed"] = True
        save_state(state)
        sys.stderr.write("[miss] priming run: marked %d existing outcomes\n"
                         % len(state["alerted"]))
        return 0

    fresh = self_review.new_outcomes(outcomes, alerted, hits=not args.misses_only)
    hits = sum(1 for o in fresh if o.get("correct"))
    print(json.dumps({"scored": len(outcomes), "new": len(fresh),
                      "hits": hits, "misses": len(fresh) - hits},
                     ensure_ascii=False))

    if args.dry_run or not token or not chat:
        for outcome in fresh:
            print(self_review.format_outcome(outcome))
        if not (token and chat) and not args.dry_run:
            sys.stderr.write("[miss] no telegram credentials; dry run only\n")
        return 0

    tg = bot.Telegram(token)
    sent = []
    for outcome in fresh:
        if tg.send(chat, self_review.format_outcome(outcome)):
            sent.append(self_review.outcome_key(outcome))
    if sent:
        state["alerted"] = sorted(alerted | set(sent))
        save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
