#!/usr/bin/env python3
"""ASTRA news-quiet window: is right now a bad moment to trade?

High-impact releases (US CPI, NFP, rate decisions) make the next few minutes
violent and directionless — exactly the regime the volatility filter warns
about. This module answers one question for the bot: *is `now` inside the
blackout window around a relevant event, or is one about to print?*

The logic is pure — it takes the normalized event list and a timestamp and
returns a reason string or None. No network, no clock: the caller fetches and
passes `now`, so this is fully unit-testable. `advisor/calendar_feed.py` owns
the fetch.

Honesty note: this is a safety heuristic, not a measured signal. We have no
historical calendar in the corpus, so its effect cannot be backtested the way
the volatility model's can. The wording says "usually violent", never "will
move".
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import calendar_feed as cal  # noqa: E402

# A high-impact print is felt from a few minutes before (positioning stops) to
# well after (the knee-jerk often reverses). These are defaults, overridable by
# env for ops tuning.
BEFORE_MIN = float(os.environ.get("ASTRA_NEWS_BEFORE_MIN", "15"))
AFTER_MIN = float(os.environ.get("ASTRA_NEWS_AFTER_MIN", "15"))
# How far ahead a scheduled event still counts as "about to print".
UPCOMING_MIN = float(os.environ.get("ASTRA_NEWS_UPCOMING_MIN", "45"))


def is_relevant(event: dict) -> bool:
    """High-impact events only — the ones that reliably move gold."""
    imp = event.get("importance")
    return isinstance(imp, (int, float)) and imp >= cal.HIGH


def _label(event: dict) -> str:
    return event.get("title_ar") or event.get("title") or "خبر"


def _minutes(delta_seconds: float) -> int:
    return round(abs(delta_seconds) / 60.0)


def blackout_event(events: list[dict], now: float,
                   before_min: float = BEFORE_MIN,
                   after_min: float = AFTER_MIN) -> dict | None:
    """The relevant event whose blackout window contains `now`, if any.

    When two windows overlap the most recent one wins, so the message names the
    event the market is actually reacting to.
    """
    hit = None
    for event in events:
        if not is_relevant(event):
            continue
        at = event.get("at_utc")
        if at is None:
            continue
        if (at - before_min * 60 <= now <= at + after_min * 60
                and (hit is None or at > hit.get("at_utc", 0))):
            hit = event
    return hit


def upcoming_event(events: list[dict], now: float,
                   horizon_min: float = UPCOMING_MIN) -> dict | None:
    """The nearest relevant event still in the future, within the horizon."""
    best = None
    for event in events:
        if not is_relevant(event):
            continue
        at = event.get("at_utc")
        if at is None or at < now:
            continue
        if (at - now <= horizon_min * 60
                and (best is None or at < best.get("at_utc", 0))):
            best = event
    return best


def blackout_reason(events: list[dict], now: float,
                    before_min: float = BEFORE_MIN,
                    after_min: float = AFTER_MIN,
                    horizon_min: float = UPCOMING_MIN) -> str | None:
    """A one-line Arabic reason to stand aside, or None when the coast is clear."""
    active = blackout_event(events, now, before_min, after_min)
    if active is not None:
        at = active["at_utc"]
        if at > now:
            return "⏸ نافذة خبر: %s بعد ~%d دقيقة — السوق عادةً عنيف لحظة الصدور." % (
                _label(active), _minutes(at - now))
        return "⏸ نافذة خبر: %s صدر قبل ~%d دقيقة — السوق عادةً عنيف بعد الصدور." % (
            _label(active), _minutes(now - at))

    soon = upcoming_event(events, now, horizon_min)
    if soon is not None:
        return "⏳ خبر مهم قريب: %s بعد ~%d دقيقة — راقب التقلّب." % (
            _label(soon), _minutes(soon["at_utc"] - now))
    return None


# A level break is worth explaining with the release that most likely caused it.
RECENT_MIN = float(os.environ.get("ASTRA_NEWS_RECENT_MIN", "120"))


def recent_context(events: list[dict], now: float,
                   lookback_min: float = RECENT_MIN) -> str | None:
    """A short Arabic read of the newest relevant release, for a level alert.

    Reuses `calendar_feed.classify`, so the baseline (forecast or previous) is
    always named and gold direction is only asserted for a US event. Returns
    None when nothing relevant printed inside the lookback window.
    """
    best = None
    for event in events:
        if not is_relevant(event):
            continue
        at = event.get("at_utc")
        if at is None or at > now or at < now - lookback_min * 60:
            continue
        if best is None or at > best.get("at_utc", 0):
            best = event
    if best is None:
        return None

    label = _label(best)
    mins = _minutes(now - best["at_utc"])
    read = cal.classify(best)
    if not read or read.get("surprise") in (None, "unknown"):
        return "📰 آخر خبر مهم: %s (قبل ~%d دقيقة)." % (label, mins)
    if read.get("currency_dir", 0) == 0:
        return ("📰 آخر خبر مهم: %s (قبل ~%d دقيقة) — مطابق للتوقّع، بلا مفاجأة."
                % (label, mins))

    stronger = read["currency_dir"] > 0
    base = "التقدير" if read.get("vs") == "forecast" else "السابق (بلا تقدير)"
    ccy = read.get("currency") or "—"
    if read.get("certain"):
        gold = "ضغط على الذهب (دولار أقوى)" if stronger else "دعم للذهب (دولار أضعف)"
    else:
        gold = "الأثر على الذهب غير مباشر"
    return ("📰 آخر خبر مهم: %s (قبل ~%d دقيقة) — الفعلي %s من %s، %s %s → %s."
            % (label, mins, "أعلى" if read["surprise"] == "above" else "أدنى",
               base, ccy, "أقوى" if stronger else "أضعف", gold))
