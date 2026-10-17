#!/usr/bin/env python3
"""ASTRA calendar feed: released economic events, with a directional read.

The Forex Factory mirror the advisor already reads carries only *forecast* and
*previous* — never a released value. That is enough to warn before a release,
but not to say what actually happened. TradingView's keyless calendar carries
`actual`, so an event can be judged the moment it prints.

The read is mechanical and always names what it compares:

    actual vs forecast   -> the surprise (falls back to previous, and says so)
    higher-is-stronger   -> sign per indicator (jobless claims invert it)
    country              -> USD for US events, EUR for EU

For a US event the dollar read is then inverted for gold: a stronger dollar
usually pressures gold. That is a tendency, not a law, so the wording says
"ضغط" (pressure), never "سيهبط". For a non-US event the gold link is indirect
and is labelled as such rather than asserted.

Read-only and keyless. Every fetch is best-effort: a dead feed yields no events.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

CALENDAR_URL = os.environ.get(
    "ASTRA_CALENDAR_URL",
    "https://economic-calendar.tradingview.com/events")
COUNTRIES = os.environ.get("ASTRA_CALENDAR_CCY", "US,EU")
_USER_AGENT = os.environ.get("ASTRA_CALENDAR_USER_AGENT", "ASTRA-calendar/1.0")
HTTP_TIMEOUT = float(os.environ.get("ASTRA_CALENDAR_TIMEOUT", "20"))

# TradingView importance: 1 high, 0 medium, -1 low. Gold reacts to high and
# medium releases; the low ones are noise for this purpose.
HIGH, MEDIUM, LOW = 1, 0, -1

CURRENCY = {"US": "USD", "EU": "EUR"}

# Most indicators are currency-positive when they print high. These invert:
# more people unemployed is weaker, not stronger.
INVERSE_KEYS = ("unemployment rate", "jobless claims", "continuing claims",
                "initial jobless", "unemployment claims")

# Known indicators that the advisor's Arabic naming already covers.
_EVENT_AR = (
    ("adp employment change weekly", "التغيّر الأسبوعي في التوظيف (ADP)"),
    ("adp employment change", "التغيّر في التوظيف (ADP)"),
    ("adp", "تقرير ADP للوظائف"),
    ("nonfarm payroll", "التوظيف غير الزراعي"),
    ("non-farm payroll", "التوظيف غير الزراعي"),
    ("nonfarm", "التوظيف غير الزراعي"),
    ("unemployment rate", "معدل البطالة"),
    ("jobless claims", "طلبات إعانة البطالة"),
    ("continuing jobless", "طلبات البطالة المستمرة"),
    ("core cpi", "مؤشر أسعار المستهلك الأساسي (Core CPI)"),
    ("cpi", "مؤشر أسعار المستهلك (CPI)"),
    ("core ppi", "مؤشر أسعار المنتجين الأساسي (Core PPI)"),
    ("ppi", "مؤشر أسعار المنتجين (PPI)"),
    ("core pce", "مؤشر أسعار الاستهلاك الأساسي (Core PCE)"),
    ("pce", "مؤشر أسعار الاستهلاك (PCE)"),
    ("retail sales", "مبيعات التجزئة"),
    ("gdp", "الناتج المحلي الإجمالي (GDP)"),
    ("interest rate", "قرار الفائدة"),
    ("federal funds", "سعر الفائدة الفيدرالي"),
    ("ism", "مؤشر ISM"),
    ("pmi", "مؤشر مديري المشتريات (PMI)"),
    ("consumer confidence", "ثقة المستهلك"),
    ("durable goods", "السلع المعمّرة"),
    ("industrial production", "الإنتاج الصناعي"),
    ("philly fed", "مؤشر فيلادلفيا الفيدرالي"),
)


def arabic_event_name(title: str) -> str:
    low = (title or "").lower()
    for needle, arabic in _EVENT_AR:
        if needle in low:
            return arabic
    return title


def _parse_iso(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


def _num(value) -> float | None:
    """TradingView sends numbers or null; keep null as None, never 0."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize(raw: list[dict]) -> list[dict]:
    """Turn TradingView rows into the small, stable shape we reason over."""
    out: list[dict] = []
    for row in raw or []:
        at = _parse_iso(row.get("date"))
        if at is None:
            continue
        title = (row.get("title") or "").strip()
        out.append({
            "id": row.get("id"),
            "title": title,
            "title_ar": arabic_event_name(title),
            "country": (row.get("country") or "").strip().upper(),
            "importance": row.get("importance"),
            "at_utc": int(at),
            "forecast": _num(row.get("forecast")),
            "previous": _num(row.get("previous")),
            "actual": _num(row.get("actual")),
        })
    out.sort(key=lambda e: e["at_utc"])
    return out


def fetch_events(from_ts: float, to_ts: float,
                 countries: str = COUNTRIES) -> list[dict]:
    """Normalized events in the window. Best-effort: [] on any failure."""
    frm = datetime.fromtimestamp(from_ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    to = datetime.fromtimestamp(to_ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    url = "%s?from=%s&to=%s&countries=%s" % (CALENDAR_URL, frm, to, countries)
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
            "Origin": "https://www.tradingview.com",
            "Referer": "https://www.tradingview.com/",
        })
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return []
    rows = payload.get("result") if isinstance(payload, dict) else payload
    return normalize(rows if isinstance(rows, list) else [])


def higher_is_stronger(title: str) -> bool:
    """False for the indicators where a high print is currency-negative."""
    low = (title or "").lower()
    return not any(key in low for key in INVERSE_KEYS)


def classify(event: dict) -> dict | None:
    """The directional read for a released event, or None if not released.

    Returns the surprise, the currency it moves, and the gold read. The gold
    read is only asserted for a US event; elsewhere it is 0 and the caller says
    the link is indirect.
    """
    actual = event.get("actual")
    if actual is None:
        return None
    if event.get("forecast") is not None:
        base, vs = event["forecast"], "forecast"
    elif event.get("previous") is not None:
        base, vs = event["previous"], "previous"
    else:
        return {"surprise": "unknown", "vs": None, "currency": None,
                "currency_dir": 0, "gold_dir": 0, "certain": False}

    cmp = (actual > base) - (actual < base)          # 1 above, -1 below, 0 inline
    surprise = {1: "above", -1: "below", 0: "inline"}[cmp]
    # A high print is currency-strong except where the indicator inverts it.
    currency_dir = cmp if higher_is_stronger(event.get("title", "")) else -cmp
    ccy = CURRENCY.get(event.get("country", ""), event.get("country") or "")
    certain = event.get("country") == "US"
    gold_dir = -currency_dir if certain else 0
    return {"surprise": surprise, "vs": vs, "currency": ccy,
            "currency_dir": currency_dir, "gold_dir": gold_dir, "certain": certain}


def released(events: list[dict], now: float, lookback_h: float,
             min_importance: int = MEDIUM) -> list[dict]:
    """Released events inside the lookback window, newest first.

    ``min_importance`` keeps the low-impact rows out; TradingView uses
    1/0/-1, so a medium floor is 0.
    """
    since = now - lookback_h * 3600.0
    out = []
    for event in events:
        if event.get("actual") is None:
            continue
        if event.get("at_utc", 0) < since or event.get("at_utc", 0) > now:
            continue
        imp = event.get("importance")
        if isinstance(imp, (int, float)) and imp < min_importance:
            continue
        out.append(event)
    out.sort(key=lambda e: e["at_utc"], reverse=True)
    return out


def _fmt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return ("%g" % value)


def format_release(event: dict, read: dict) -> str:
    """The Telegram message for one released event."""
    lines = [
        "💱 نتيجة خبر — %s" % (event.get("title_ar") or event.get("title")),
        "الفعلي: %s · التقدير: %s · السابق: %s" % (
            _fmt(event.get("actual")), _fmt(event.get("forecast")),
            _fmt(event.get("previous"))),
    ]
    ccy = read.get("currency") or "—"
    direction = read.get("currency_dir", 0)
    surprise = read.get("surprise")

    if surprise == "unknown":
        lines.append("⚠️ ما في تقدير ولا سابق — ما فيك تقارن، فالقراءة مو متوفرة.")
    elif direction == 0:
        lines.append("🎯 مطابق تماماً لـ%s → ما في مفاجأة." %
                     ("التقدير" if read.get("vs") == "forecast" else "السابق"))
    else:
        stronger = direction > 0
        lines.append("🎯 الفعلي %s من %s → %s %s." % (
            "أعلى" if surprise == "above" else "أدنى",
            "التقدير" if read.get("vs") == "forecast" else "السابق (بلا تقدير)",
            ccy, "أقوى" if stronger else "أضعف"))
        if read.get("certain"):
            lines.append("📊 يعني: %s" % (
                "ضغط على الذهب (دولار أقوى)" if stronger
                else "دعم للذهب (دولار أضعف)"))
        else:
            lines.append("ℹ️ الأثر على الذهب غير مباشر — ربط العملة بالذهب "
                         "مباشر فقط مع الدولار.")
    lines.append("قراءة ميكانيكية للمقارنة، مو توصية تداول.")
    return "\n".join(lines)
