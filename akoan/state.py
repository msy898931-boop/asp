#!/usr/bin/env python3
"""akoan news state: what the news looked like *at the moment of a trade*.

This is the join key between the two halves of akoan. A trade in
`run/signal_trades.jsonl` carries only its own bar data; this module answers,
for one instant, the four questions the statistics then test:

  * **blackout** — was a high-impact release inside its violent window
    (±15 min)? `advisor/news_window.py` asks this for *now*; here it is asked
    for a past timestamp, which is what makes it backtestable.
  * **recent release** — did a release just print, and which way did the
    surprise point for gold?
  * **upcoming** — is one about to print?
  * **alignment** — did the trade lean *with* or *against* the news read?

Two rules keep this honest:

  1. **Causal only.** Only events at or before the trade's timestamp are ever
     consulted. A release that printed after the trade cannot explain it, so it
     is invisible here by construction, not by convention.

  2. **Pure.** The event list and the timestamp are passed in; there is no
     network and no clock in this file, so every branch is unit-testable and a
     result is reproducible from the ledger on disk.

The alignment read is deliberately *directional bookkeeping*, not a claim of
predictive power. Whether it predicts anything is exactly what `link.py` and
the statistics are for; this module only records the state.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for extra in (HERE, os.path.join(ROOT, "advisor")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import calendar_feed as cal  # noqa: E402

BEFORE_MIN = float(os.environ.get("ASTRA_AKOAN_BEFORE_MIN", "15"))
AFTER_MIN = float(os.environ.get("ASTRA_AKOAN_AFTER_MIN", "15"))
UPCOMING_MIN = float(os.environ.get("ASTRA_AKOAN_UPCOMING_MIN", "45"))
RECENT_MIN = float(os.environ.get("ASTRA_AKOAN_RECENT_MIN", "120"))

# Trade directions, and how they line up with a gold read (+1 up, -1 down).
_TRADE_DIR = {"LONG": 1, "SHORT": -1}


def is_high_impact(event: dict) -> bool:
    imp = event.get("importance")
    return isinstance(imp, (int, float)) and imp >= cal.HIGH


def _gold_dir(event: dict) -> int:
    """The gold read for a released event, reusing the advisor's classifier."""
    read = cal.classify(event)
    if not read:
        return 0
    return read.get("gold_dir", 0) or 0


def state_at(events: list[dict], ts: float,
             before_min: float = BEFORE_MIN,
             after_min: float = AFTER_MIN,
             upcoming_min: float = UPCOMING_MIN,
             recent_min: float = RECENT_MIN,
             direction: str | None = None) -> dict:
    """The causal news state at `ts`. Only events <= ts are ever considered."""
    past = [e for e in events if (e.get("at_utc") or 0) <= ts]

    # Blackout: a high-impact release inside its violent window. Most recent wins.
    active = None
    for e in past:
        if not is_high_impact(e):
            continue
        at = e["at_utc"]
        if at - before_min * 60 <= ts <= at + after_min * 60:
            if active is None or at > active["at_utc"]:
                active = e

    # Recent release: the newest high-impact print inside the lookback window.
    recent = None
    for e in past:
        if not is_high_impact(e):
            continue
        if e.get("actual") is None:
            continue
        at = e["at_utc"]
        if ts - recent_min * 60 <= at <= ts:
            if recent is None or at > recent["at_utc"]:
                recent = e

    # Upcoming is the one exception to "past only": a scheduled event the trade
    # could *see coming*. Its `actual` is never used, so there is no lookahead.
    upcoming = None
    for e in events:
        if not is_high_impact(e):
            continue
        at = e.get("at_utc") or 0
        if ts < at <= ts + upcoming_min * 60:
            if upcoming is None or at < upcoming["at_utc"]:
                upcoming = e

    gold_dir = _gold_dir(recent) if recent is not None else 0
    trade_dir = _TRADE_DIR.get((direction or "").upper(), 0)
    if gold_dir and trade_dir:
        alignment = "WITH_NEWS" if gold_dir == trade_dir else "AGAINST_NEWS"
    else:
        alignment = "NEUTRAL"

    return {
        "blackout": active is not None,
        "blackout_title": (active or {}).get("title_ar") or (active or {}).get("title"),
        "blackout_seconds": int(ts - active["at_utc"]) if active else None,
        "recent_release": recent is not None,
        "recent_title": (recent or {}).get("title_ar") or (recent or {}).get("title"),
        "recent_minutes": int(round((ts - recent["at_utc"]) / 60.0)) if recent else None,
        "recent_gold_dir": gold_dir,
        "upcoming": upcoming is not None,
        "upcoming_title": (upcoming or {}).get("title_ar") or (upcoming or {}).get("title"),
        "upcoming_minutes": (int(round((upcoming["at_utc"] - ts) / 60.0))
                             if upcoming else None),
        "alignment": alignment,
        "news_in_play": bool(active is not None or recent is not None
                             or upcoming is not None),
    }


def dimension_keys(state: dict) -> dict:
    """The categorical labels a state contributes to the statistics."""
    return {
        "blackout": "blackout" if state["blackout"] else "quiet",
        "recent_release": "after_release" if state["recent_release"] else "no_release",
        "upcoming": "before_release" if state["upcoming"] else "no_upcoming",
        "alignment": state["alignment"],
        "news_in_play": "news_in_play" if state["news_in_play"] else "no_news",
    }
