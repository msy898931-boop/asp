#!/usr/bin/env python3
"""akoan: the honest bridge between the news read and the statistical result.

akoan answers one question, with numbers and no hype: **does the news explain
the trade results?** It joins the tracker's trade ledger with a causal record
of the economic calendar, slices the results by every news axis it can, and
reports what survives multiple-comparison correction — which, honestly, is
usually nothing yet.

Design rules, all inherited from the project's own honesty discipline:

  * **Causal only.** A trade is described by the news knowable at its open; no
    event after the fact can explain it.
  * **Base rate first.** Every bucket is compared to the overall win rate, not
    to 0.5 — the lesson from `horizon_diagnostic.py`, where a 51.7% "edge" was
    just gold's 51.7% drift.
  * **Correction before claims.** Benjamini-Hochberg across all buckets, so a
    finding must survive the fact that we scanned many.
  * **Default to no.** The verdict is `NO_EDGE_MEASURED` or `INSUFFICIENT_DATA`
    unless something genuinely clears the bar.

Run: python3 akoan/run.py                 # analyse and print
     python3 akoan/run.py --json          # the raw result
     python3 akoan/run.py --dry-run       # print the Telegram text, do not send
     python3 akoan/run.py --send          # send the report to Telegram
     python3 akoan/run.py --backfill 30   # refresh the event ledger first
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for extra in (HERE, os.path.join(ROOT, "advisor"), os.path.join(ROOT, "bot")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import engine as eng  # noqa: E402
import events as ev  # noqa: E402
import link as lk  # noqa: E402
import report as rep  # noqa: E402


def run(backfill_days: int | None = None,
        window_days: float | None = None) -> dict:
    """Produce one akoan result. Backfills first when asked."""
    if backfill_days:
        ev.backfill(backfill_days)
    events = list(ev.load().values())
    trades = lk.load_trades()
    rows = lk.link(trades, events, window_days=window_days)
    result = eng.analyse(rows)
    result["coverage"] = ev.coverage()
    result["trades_seen"] = len(trades)
    result["trades_linked"] = len(rows)
    return result


def _send(text: str) -> bool:
    import telegram_bot as bot
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (token and chat):
        sys.stderr.write("[akoan] no telegram credentials\n")
        return False
    return bool(bot.Telegram(token).send(chat, text))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="akoan news <-> statistics engine")
    parser.add_argument("--backfill", type=int, default=None, metavar="DAYS",
                        help="refresh the event ledger for DAYS before now")
    parser.add_argument("--window", type=float, default=None, metavar="DAYS",
                        help="only link trades inside DAYS of the newest event")
    parser.add_argument("--json", action="store_true", help="print the raw result")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the Telegram text without sending")
    parser.add_argument("--send", action="store_true", help="send to Telegram")
    args = parser.parse_args(argv)

    result = run(backfill_days=args.backfill, window_days=args.window)
    text = rep.format_report(result, result.get("coverage"))

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.dry_run or not args.send:
        print(text)
        print("")
        print(json.dumps(rep.compact(result), ensure_ascii=False))

    if args.send and not args.dry_run:
        ok = _send(text)
        print(json.dumps({"sent": ok, "verdict": result["verdict"]},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
