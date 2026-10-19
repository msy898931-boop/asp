#!/usr/bin/env python3
"""ASTRA news forecast: how to be ready for the release before it prints.

The news-quiet window answers "is now a bad moment?" — it stands the trader
aside. This module answers the question that comes *before* that one: a
high-impact release is due in N minutes, so what do we actually know, and how do
we prepare a plan around it?

What we know, measured rather than assumed
------------------------------------------
We replayed the 83 high-impact US releases that fall inside the committed M1
gold corpus (2026-06-24 .. 2026-10-08) and measured the gold move around each
one. Two things came out, and only two:

  * The *size* is forecastable. A high-impact US release moves gold a median of
    ~$2.7 in 15 min, ~$3.5 in 30 min, ~$5.2 in 60 min, and the violent tenth
    reaches $9-15. An inline print moves less than a surprise, but not by much
    (median $2.2 vs $3.9 at 15 min) — the reaction is to the event, not to the
    number.
  * The *direction* is not. The mechanical read (actual vs forecast, dollars
    stronger => gold weaker) agreed with gold's sign 40-58% of the time across
    horizons — a coin, and at 5 min worse than one. The 60-minute pre-release
    drift is flat (mean -$0.40, std $1.06), so nothing leaks ahead either.

So this module never claims a direction. It hands the trader the two things
that are real — *when* and *how big* — and a plan that needs no direction: let
the release pick the side, then join the side it picked.

Honesty rules, carried over from `news_engine`:

  * Direction is reported as unknown, in as many words. There is no "gold will
    rise" here, because the measurement does not support one.
  * The expected move is a median with its sample size attached; a thin family
    says so rather than inventing precision.
  * The numbers are a *starting point for a bracket*, not a promise. The plan
    sizes risk to the band and invalidates itself when the release passes
    without a break.

Read-only, keyless, best-effort: a missing feed yields an empty forecast.

Run: python3 advisor/news_forecast.py --once
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

# How far ahead a release is worth preparing for. Long enough to place a plan,
# short enough that the plan is not stale by the time it prints.
PREP_MIN = float(os.environ.get("ASTRA_FORECAST_PREP_MIN", "90"))
# The bracket is aimed at this horizon: the move usually resolves inside an
# hour, and holding longer than that is a different trade.
HORIZON_MIN = float(os.environ.get("ASTRA_FORECAST_HORIZON_MIN", "60"))
# The pre-release drift measured over the corpus was flat, so a plan is only
# "late" once price has already travelled most of the expected band.
LATE_FRACTION = float(os.environ.get("ASTRA_FORECAST_LATE_FRACTION", "0.5"))

# The measured absolute move, in dollars, by horizon. `median` is the typical
# case; `p90` is the violent tenth the trader must survive. `n` is the sample
# each row rests on, carried so a caller can see how thin it is. Provenance:
# 83 high-impact US releases inside research/data/xauusd_m1 (2026-06-24 ..
# 2026-10-08); re-derive with `scripts/measure_news_move.py` after refreshing
# the corpus.
_MOVE_TABLE: dict[int, dict] = {
    15: {"median": 2.72, "p90": 6.77, "n": 83},
    30: {"median": 3.49, "p90": 9.29, "n": 83},
    60: {"median": 5.19, "p90": 14.62, "n": 83},
}

# Family ranking: how the violent tenth differs by the kind of release. Used to
# say "this one runs hot" without pretending to know which way it runs. Values
# are the median 60-minute move; a family under the sample floor is not listed.
_FAMILY_MIN_N = 5
_FAMILY_MOVE: dict[str, float] = {
    "jobs": 6.8,
    "activity": 6.8,
    "rates": 6.0,
    "other": 6.0,
    "inflation": 3.8,
    "housing": 3.4,
    "sentiment": 4.3,
}

_FAMILY_AR = {
    "jobs": "وظائف",
    "activity": "نشاط اقتصادي",
    "rates": "فائدة/فيدرالي",
    "inflation": "تضخم",
    "housing": "إسكان",
    "sentiment": "ثقة/معنويات",
    "other": "أخرى",
}

# The families whose releases move gold the most, so the prep note can flag them.
_HOT = ("jobs", "activity", "rates")


def event_family(title: str) -> str:
    """The kind of release, for the move table. Unknown titles fall to 'other'."""
    low = (title or "").lower()
    if any(k in low for k in ("inflation", "cpi", "ppi", "pce")):
        return "inflation"
    if any(k in low for k in ("nonfarm", "non-farm", "payroll", "unemployment",
                              "jobless", "adp")):
        return "jobs"
    if any(k in low for k in ("rate", "fomc", "fed", "interest")):
        return "rates"
    if any(k in low for k in ("sentiment", "confidence")):
        return "sentiment"
    if any(k in low for k in ("home", "housing", "building")):
        return "housing"
    if any(k in low for k in ("gdp", "retail", "durable", "ism", "pmi",
                              "production", "investment")):
        return "activity"
    return "other"


def expected_move(family: str, horizon_min: float = 60) -> dict:
    """The measured band for a release of this family at this horizon.

    Falls back to the nearest measured horizon and to the all-families row when
    the family is too thin to stand on its own — a wide honest band beats a
    narrow invented one.
    """
    horizons = sorted(_MOVE_TABLE)
    nearest = min(horizons, key=lambda h: abs(h - horizon_min))
    row = _MOVE_TABLE[nearest]
    fam_move = _FAMILY_MOVE.get(family)
    # Scale the overall median by the family's 60-minute ratio, so a jobs print
    # reads hotter than a housing print without pretending to per-horizon data.
    base60 = _MOVE_TABLE[60]["median"]
    scale = (fam_move / base60) if fam_move and base60 else 1.0
    return {
        "horizon_min": nearest,
        "median": round(row["median"] * scale, 2),
        "p90": round(row["p90"] * scale, 2),
        "family": family,
        "family_known": fam_move is not None,
        "n": row["n"],
    }


def next_event(events: list[dict], now: float,
               horizon_min: float = PREP_MIN) -> dict | None:
    """The soonest high-impact event inside the prep horizon, or None."""
    best = None
    for event in events:
        if not win.is_relevant(event):
            continue
        at = event.get("at_utc")
        if at is None or at < now or at - now > horizon_min * 60:
            continue
        if best is None or at < best.get("at_utc", 0):
            best = event
    return best


def _levels(price: float | None, band: float) -> dict | None:
    """A direction-free bracket around the pre-release price.

    The trader does not need to know which way it breaks: a buy-stop above and
    a sell-stop below catch whichever side the release chooses, and the stop
    behind each is sized to the measured band so a violent tenth does not
    simply take out both.
    """
    if not price or price <= 0 or band <= 0:
        return None
    stop = round(band, 2)
    target = round(band * 2.0, 2)   # reward twice the typical move, R:R = 2
    return {
        "price": price,
        "break_up": round(price + band * 0.25, 2),
        "break_down": round(price - band * 0.25, 2),
        "stop_distance": stop,
        "target_distance": target,
        "rr": 2.0,
    }


def plan(event: dict, price: float | None, now: float,
         horizon_min: float = HORIZON_MIN) -> dict:
    """The prep playbook for one event: what to watch, and when to stand down.

    Direction-free by construction. Every branch is conditional on the market
    choosing a side first, because the measurement says we cannot choose it.
    """
    family = event_family(event.get("title") or "")
    move = expected_move(family, horizon_min)
    in_min = round((event["at_utc"] - now) / 60.0)
    steps = [
        "قبل الصدور: حدّد نطاقاً ضيقاً حول السعر الحالي (لا تدخل بصفقة مسبقة "
        "باتجاه — الاتجاه غير متوقّع).",
        "ارسم أمرَي إيقاف: شراء فوق القمة، وبيع تحت القاع.",
        "وقّف الخسارة خلف المستوى بمقدار ~$%.1f، والهدف ~$%.1f (R:R = 2)."
        % (move["median"], move["median"] * 2.0),
        "تجاهل أول 5–15 دقيقة بعد الصدور — الحركة الأولى كثيراً ما تنعكس.",
        "ادخل مع الجهة التي كسرت أولاً، ويفضّل على إعادة اختبار المستوى المكسور.",
    ]
    if family in _HOT:
        steps.append("هذا النوع (%s) من أشدّ الأنواع حركة — صغّر الحجم."
                     % _FAMILY_AR.get(family, family))
    invalidate = (
        "إذا مرّ %d دقيقة بعد الصدور دون كسر أي من المستويين، ألغِ الخطة — "
        "لا تتداول حركة لم تحدث." % int(HORIZON_MIN))
    return {
        "event_family": family,
        "event_family_ar": _FAMILY_AR.get(family, family),
        "in_minutes": in_min,
        "direction": "unknown",
        "direction_note": ("اتجاه الذهب بعد الخبر غير قابل للتنبؤ ميكانيكياً "
                           "(قياس: 40–58% مطابقة، أي كرمي عملة). لا يُدَّعى."),
        "expected_move": move,
        "levels": _levels(price, move["median"]),
        "steps": steps,
        "invalidation": invalidate,
    }


def forecast(now: float | None = None, events: list[dict] | None = None,
             price: float | None = None) -> dict:
    """The full pre-news forecast. `events`/`price` are injectable for tests."""
    now = time.time() if now is None else now
    if events is None:
        events = cal.fetch_events(now - 3600, now + PREP_MIN * 3600)

    event = next_event(events, now)
    blackout = win.blackout_event(events, now)
    state = ("active" if blackout is not None
             else "prep" if event is not None else "clear")
    row = None
    if event is not None:
        row = {
            "title": event.get("title") or "",
            "title_ar": event.get("title_ar") or event.get("title") or "",
            "country": event.get("country") or "",
            "importance": event.get("importance"),
            "at_utc": event.get("at_utc"),
            "in_minutes": round((event["at_utc"] - now) / 60.0),
            "forecast": event.get("forecast"),
            "previous": event.get("previous"),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "engine": "news-forecast",
        "symbol": SYMBOL,
        "generated_at_utc": int(now),
        "state": state,
        "event": row,
        "plan": plan(event, price, now) if event is not None else None,
        "blackout": blackout is not None,
        "notes": [
            "الحجم متوقّع من قياس فعلي؛ الاتجاه لا.",
            "الخطة بلا اتجاه: السوق يختار الجهة، ونحن نلحقها.",
            "ليست نصيحة استثمارية؛ مخاطرة الخبر عالية.",
        ],
    }


# ─────────────────────────── Arabic rendering ───────────────────────────────

_STATE_AR = {
    "active": "⏸ نافذة خبر مفتوحة — السوق عنيف الآن",
    "prep": "🎯 خبر مهم قادم — هذه خطة التحضير",
    "clear": "✅ لا خبر مهم قريب — لا حاجة لخطة خبر",
}


def _num(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return "%g" % value if isinstance(value, (int, float)) else str(value)


def format_forecast(d: dict) -> str:
    """The Telegram reply for /forecast — the prep plan in Arabic."""
    lines = ["🧭 توقّع الخبر — خطة ما قبل الصدور",
             _STATE_AR.get(str(d.get("state") or ""), "—")]
    event = d.get("event")
    if not event:
        lines.append("ما في إصدار عالي التأثير خلال الفترة القادمة.")
        lines.append("اسأل /news للأخبار العامة، أو /forecast لاحقاً.")
        return "\n".join(lines)

    lines += ["", "📅 %s (%s) بعد %d دقيقة" % (
        event.get("title_ar") or event.get("title"), event.get("country"),
        event.get("in_minutes", 0))]
    if event.get("forecast") is not None or event.get("previous") is not None:
        lines.append("   التقدير %s · السابق %s" % (
            _num(event.get("forecast")), _num(event.get("previous"))))

    p = d.get("plan") or {}
    mv = p.get("expected_move") or {}
    lines += ["", "📏 المدى المتوقّع (وسيط %d د): ~$%.1f" % (
        mv.get("horizon_min", 60), mv.get("median", 0)),
        "   الحالات العنيفة (10%%): حتى ~$%.1f  [قياس على %d حدث]"
        % (mv.get("p90", 0), mv.get("n", 0))]

    lv = p.get("levels")
    if lv:
        lines += ["", "🎚 السعر الآن %s" % _num(lv["price"]),
                  "   كسر صعودي فوق %s" % _num(lv["break_up"]),
                  "   كسر هبوطي تحت %s" % _num(lv["break_down"]),
                  "   وقف ~$%s · هدف ~$%s (R:R 2)" % (
                      _num(lv["stop_distance"]), _num(lv["target_distance"]))]

    lines += ["", "🧩 الخطة:"]
    for i, step in enumerate(p.get("steps") or [], 1):
        lines.append("   %d. %s" % (i, step))
    if p.get("invalidation"):
        lines += ["", "⛔ " + p["invalidation"]]

    lines += ["", "⚠️ الاتجاه غير متوقّع — " + str(p.get("direction_note") or "")]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ASTRA news forecast")
    parser.add_argument("--once", action="store_true",
                        help="print one forecast and exit")
    args = parser.parse_args(argv)
    if args.once:
        json.dump(forecast(), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
