#!/usr/bin/env python3
"""ASTRA news engine: the world's news, read for gold, right now.

This is the live counterpart to the news study. It does not predict; it
assembles, from keyless public sources, the three things a gold trader needs
before deciding:

  * the blackout window — is a high-impact release printing, or about to?
  * the schedule — what high-impact events are due, and how soon?
  * the tape — what just printed, and which way it mechanically leans for gold?

Every piece is reused, not re-implemented: `calendar_feed` owns the release
fetch and the mechanical surprise read, `news_window` owns the blackout logic.
The gold read is the same one the bot and the advisor already use, so the three
surfaces cannot disagree.

Honesty rules, carried over deliberately:

  * A release with no forecast and no previous has no baseline, so no direction
    is asserted — the record says so.
  * The gold link is only asserted for a US release. Elsewhere it is labelled
    indirect, because only the dollar has a direct, mechanical link to gold.
  * The wording is "pressure" / "support", never "will fall" / "will rise".
  * An absent feed yields empty lists, never a fabricated event. The caller can
    always tell "no news" from "no feed" by the counts.

Read-only, keyless, and best-effort: every fetch failure degrades to an empty
list, and the digest is still a valid document.

Run: python3 advisor/news_engine.py --once
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import calendar_feed as cal  # noqa: E402
import news_window as win  # noqa: E402

SCHEMA_VERSION = "1.0"
SYMBOL = os.environ.get("ASTRA_NEWS_SYMBOL", "XAUUSD")

# How far back a release still counts as "just printed", and how far ahead the
# schedule looks. Both are env-tunable so ops can widen them without a rebuild.
LOOKBACK_H = float(os.environ.get("ASTRA_NEWS_LOOKBACK_H", "12"))
LOOKAHEAD_H = float(os.environ.get("ASTRA_NEWS_LOOKAHEAD_H", "48"))
UPCOMING_LIMIT = int(os.environ.get("ASTRA_NEWS_UPCOMING_LIMIT", "8"))
RELEASED_LIMIT = int(os.environ.get("ASTRA_NEWS_RELEASED_LIMIT", "8"))
HEADLINE_LIMIT = int(os.environ.get("ASTRA_NEWS_HEADLINE_LIMIT", "10"))

# Medium and above: the low-impact rows are noise for a gold read.
MIN_IMPORTANCE = cal.MEDIUM


def _headlines() -> list[dict]:
    """Recent gold headlines. Reuses the advisor's RSS reader when importable.

    The advisor owns the query and the cache; importing it keeps one RSS
    contract instead of a second copy that can drift. A missing advisor is not
    an error — the digest simply carries no headlines.
    """
    try:
        import ai_advisor
    except ImportError:
        return []
    try:
        rows = ai_advisor.recent_news()
    except Exception:  # noqa: BLE001 - a feed failure must not break the digest
        return []
    out = []
    for row in rows[:HEADLINE_LIMIT]:
        out.append({
            "title": row.get("title") or "",
            "source": row.get("source") or "",
            "published": row.get("published") or "",
        })
    return out


def _relevant(event: dict) -> bool:
    imp = event.get("importance")
    return isinstance(imp, (int, float)) and imp >= MIN_IMPORTANCE


def _upcoming(events: list[dict], now: float) -> list[dict]:
    """High-impact events due within the lookahead, soonest first."""
    out = []
    for event in events:
        at = event.get("at_utc")
        if at is None or at < now or at - now > LOOKAHEAD_H * 3600:
            continue
        if not _relevant(event):
            continue
        out.append({
            "title": event.get("title") or "",
            "title_ar": event.get("title_ar") or event.get("title") or "",
            "country": event.get("country") or "",
            "importance": event.get("importance"),
            "at_utc": at,
            "in_minutes": round((at - now) / 60.0),
            "forecast": event.get("forecast"),
            "previous": event.get("previous"),
        })
    out.sort(key=lambda e: e["at_utc"])
    return out[:UPCOMING_LIMIT]


def _released(events: list[dict], now: float) -> list[dict]:
    """Releases inside the lookback with their mechanical gold read."""
    rows = cal.released(events, now, LOOKBACK_H, MIN_IMPORTANCE)
    out = []
    for event in rows[:RELEASED_LIMIT]:
        read = cal.classify(event) or {}
        out.append({
            "title": event.get("title") or "",
            "title_ar": event.get("title_ar") or event.get("title") or "",
            "country": event.get("country") or "",
            "at_utc": event.get("at_utc"),
            "minutes_ago": round((now - (event.get("at_utc") or now)) / 60.0),
            "actual": event.get("actual"),
            "forecast": event.get("forecast"),
            "previous": event.get("previous"),
            "surprise": read.get("surprise"),
            "vs": read.get("vs"),
            "currency": read.get("currency"),
            "currency_dir": read.get("currency_dir", 0),
            "gold_dir": read.get("gold_dir", 0),
            "certain": bool(read.get("certain")),
        })
    return out


def _window(events: list[dict], now: float) -> dict:
    """The blackout state, structured for the UI plus the Arabic one-liner."""
    active = win.blackout_event(events, now)
    if active is not None:
        at = active.get("at_utc") or now
        state = "active"
        event = {
            "title": active.get("title") or "",
            "title_ar": active.get("title_ar") or active.get("title") or "",
            "country": active.get("country") or "",
            "at_utc": at,
            "in_minutes": round((at - now) / 60.0),
        }
    else:
        soon = win.upcoming_event(events, now)
        if soon is not None:
            state = "upcoming"
            event = {
                "title": soon.get("title") or "",
                "title_ar": soon.get("title_ar") or soon.get("title") or "",
                "country": soon.get("country") or "",
                "at_utc": soon.get("at_utc"),
                "in_minutes": round((soon.get("at_utc") - now) / 60.0),
            }
        else:
            state, event = "clear", None
    return {"state": state,
            "reason": win.blackout_reason(events, now),
            "event": event}


def digest(now: float | None = None, events: list[dict] | None = None) -> dict:
    """The full news read. `events` is injectable so tests need no network."""
    now = time.time() if now is None else now
    if events is None:
        events = cal.fetch_events(now - LOOKBACK_H * 3600, now + LOOKAHEAD_H * 3600)

    upcoming = _upcoming(events, now)
    released = _released(events, now)
    headlines = _headlines()
    return {
        "schema_version": SCHEMA_VERSION,
        "engine": "news",
        "symbol": SYMBOL,
        "generated_at_utc": int(now),
        "window": _window(events, now),
        "upcoming": upcoming,
        "released": released,
        "headlines": headlines,
        "counts": {
            "upcoming": len(upcoming),
            "released": len(released),
            "headlines": len(headlines),
            "events": len(events),
        },
        "notes": [
            "القراءة ميكانيكية: الفعلي مقابل التقدير، مو توصية تداول.",
            "الأثر على الذهب مباشر مع الدولار فقط؛ لغيره غير مباشر.",
            "أثر الأخبار على نتائج النظام ما انقاس بعد — لا يُدَّعى حافة.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ASTRA news engine")
    parser.add_argument("--once", action="store_true",
                        help="print one digest and exit")
    args = parser.parse_args(argv)
    if args.once:
        json.dump(digest(), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
