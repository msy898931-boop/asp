#!/usr/bin/env python3
"""akoan event ledger: the historical calendar the corpus never had.

`AGENTS.md` states the gap plainly: "We have no historical calendar in the
corpus, so its effect cannot be backtested the way the volatility model's
can." This module closes that gap, honestly.

Two sources feed one append-only ledger:

  1. **Backfill** from `advisor/calendar_feed.py` (the keyless TradingView
     calendar), for the window before today. Past events carry their released
     `actual`, which is exactly what a historical record needs.

  2. **Accrual**: every fetch appends whatever it sees, so the ledger grows by
     itself as the bot runs. Future akoan runs then have real history even
     where the feed does not serve it.

The ledger is append-only and de-duplicated by `(at_utc, title, country)`.
Nothing is ever rewritten or deleted, so a run can always be reproduced from
what was on disk at the time — the same discipline as `run/signal_trades.jsonl`.

What this ledger is **not**: a claim that the backfill is complete. Feeds
prune old rows, so coverage is reported (`coverage`) and every result built on
it carries that caveat rather than hiding it.

Run: python3 akoan/events.py --backfill --days 30
     python3 akoan/events.py --status
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for extra in (HERE, os.path.join(ROOT, "advisor")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import calendar_feed as cal  # noqa: E402

LEDGER_PATH = os.environ.get("ASTRA_AKOAN_EVENTS",
                             os.path.join(ROOT, "run", "akoan_events.jsonl"))
# A release is "high impact" for gold at TradingView importance >= 1.
HIGH = 1

SCHEMA_VERSION = "1.0"


def event_key(event: dict) -> str:
    """Stable identity for de-duplication across fetches and sources."""
    return "%d|%s|%s" % (int(event.get("at_utc") or 0),
                         (event.get("title") or "").strip().lower(),
                         (event.get("country") or "").strip().upper())


def _record(event: dict, source: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "key": event_key(event),
        "at_utc": int(event.get("at_utc") or 0),
        "title": event.get("title") or "",
        "title_ar": event.get("title_ar") or event.get("title") or "",
        "country": (event.get("country") or "").strip().upper(),
        "importance": event.get("importance"),
        "forecast": event.get("forecast"),
        "previous": event.get("previous"),
        "actual": event.get("actual"),
        "source": source,
        "recorded_at_utc": int(time.time()),
    }


def load(path: str = LEDGER_PATH) -> dict[str, dict]:
    """The ledger as {key: record}, last write winning. Empty when absent."""
    out: dict[str, dict] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue  # a torn line must not poison the whole ledger
                if isinstance(rec, dict) and rec.get("key"):
                    out[rec["key"]] = rec
    except OSError:
        pass
    return out


def append(records: list[dict], path: str = LEDGER_PATH) -> int:
    """Append records not already present. Returns how many were new."""
    if not records:
        return 0
    existing = load(path)
    fresh = [r for r in records if r["key"] not in existing]
    if not fresh:
        return 0
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            for rec in fresh:
                fh.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")
    except OSError:
        return 0
    return len(fresh)


def backfill(days: int = 30, countries: str = cal.COUNTRIES,
             now: float | None = None) -> dict:
    """Fetch the recent past from the calendar feed and append it.

    The window ends at `now` so nothing in the future (no `actual` yet) is
    written as history.
    """
    end = now if now is not None else time.time()
    start = end - days * 86400.0
    events = cal.fetch_events(start, end, countries)
    records = [_record(e, "backfill") for e in events]
    added = append(records)
    return {
        "fetched": len(events),
        "added": added,
        "window_days": days,
        "from_utc": int(start),
        "to_utc": int(end),
    }


def coverage(path: str = LEDGER_PATH) -> dict:
    """What the ledger actually contains, so a result can state its reach."""
    led = load(path)
    if not led:
        return {"events": 0, "released": 0, "high_impact": 0, "span_days": 0}
    times = [r["at_utc"] for r in led.values() if r.get("at_utc")]
    released = [r for r in led.values() if r.get("actual") is not None]
    high = [r for r in led.values() if (r.get("importance") or -99) >= HIGH]
    span = 0
    if times:
        span = int((max(times) - min(times)) / 86400.0)
    return {
        "events": len(led),
        "released": len(released),
        "high_impact": len(high),
        "span_days": span,
        "first_utc": min(times) if times else None,
        "last_utc": max(times) if times else None,
    }


def recent_events(now: float, lookback_days: float = 3.0,
                  path: str = LEDGER_PATH) -> list[dict]:
    """Ledger events inside the lookback window, oldest first."""
    since = now - lookback_days * 86400.0
    return sorted(
        (r for r in load(path).values() if since <= r.get("at_utc", 0) <= now),
        key=lambda r: r["at_utc"])


def _fmt_utc(ts) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="akoan event ledger")
    parser.add_argument("--backfill", action="store_true",
                        help="fetch the recent past and append it")
    parser.add_argument("--days", type=int, default=30,
                        help="backfill window in days (default 30)")
    parser.add_argument("--status", action="store_true",
                        help="print ledger coverage")
    args = parser.parse_args(argv)

    if args.backfill:
        print(json.dumps(backfill(args.days), ensure_ascii=False))
    cov = coverage()
    print(json.dumps(cov, ensure_ascii=False))
    if cov["events"]:
        print("from %s to %s UTC" % (_fmt_utc(cov["first_utc"]),
                                     _fmt_utc(cov["last_utc"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
