#!/usr/bin/env python3
"""ASTRA weekly akoan report: one Telegram message a week with the engine's read.

The daily report answers "what did today do". This one answers the slower
question the akoan engine exists for: *over the record so far, does the news
explain the results?* It runs the same engine the CLI runs, so the message and
the phone panel cannot disagree.

It is weekly because the answer moves slowly: the study replays the committed
corpus, and a week is the shortest interval over which the verdict could
plausibly change. Sending it daily would be noise.

Two guards, both borrowed from `bot/daily_report.py`:

  * A state file records which ISO weeks were sent, so a repeated cron slot
    changes latency only, never the message count.
  * With no Telegram credentials it prints the report and exits zero, so CI and
    a local run never fail for want of a secret.

Honesty is inherited from the engine: the message names its baseline, its
calendar coverage, and its verdict, and it never claims an edge on thin data.
The wording is Arabic, matching every other user-facing message.

Run: python3 bot/weekly_akoan.py --dry-run
     python3 bot/weekly_akoan.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for extra in (HERE, os.path.join(ROOT, "akoan"), os.path.join(ROOT, "advisor")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import telegram_bot as bot  # noqa: E402

STATE_PATH = os.environ.get("ASTRA_WEEKLY_AKOAN_STATE",
                            os.path.join(ROOT, "run", "weekly_akoan_state.json"))

# The verdicts the engine can return, and the one-line Arabic reading of each.
VERDICT_AR = {
    "EDGE_CANDIDATE": "🔎 مرشّح لحافة — في فرق نجا من تصحيح التعدّد، بدو تأكيد.",
    "NO_EDGE_MEASURED": "🔎 ما انقاس تأثير للخبر على النتائج. لا حافة.",
    "INSUFFICIENT_DATA": "🔎 بيانات غير كافية للحكم بعد.",
}

DIM_AR = {
    "blackout": "نافذة الخبر",
    "recent_release": "بعد صدور خبر",
    "upcoming": "قبل صدور خبر",
    "alignment": "اتجاه الصفقة مقابل الخبر",
    "news_in_play": "وجود خبر",
}

VALUE_AR = {
    "blackout": "محظور", "quiet": "هادئ",
    "after_release": "بعد صدور", "no_release": "بلا خبر حديث",
    "before_release": "قبل صدور", "no_upcoming": "بلا خبر قريب",
    "WITH_NEWS": "مع الخبر", "AGAINST_NEWS": "ضد الخبر", "NEUTRAL": "محايد",
    "news_in_play": "الخبر مؤثّر", "no_news": "بلا خبر",
}


def iso_week(now: datetime | None = None) -> str:
    """The ISO week key, e.g. 2026-W42."""
    now = now or datetime.now(timezone.utc)
    year, week, _ = now.isocalendar()
    return "%d-W%02d" % (year, week)


def _pct(x) -> str:
    return "—" if x is None else "%.1f%%" % (100.0 * x)


def _r(x) -> str:
    return "—" if x is None else "%+.2fR" % x


def format_weekly(result: dict, week: str) -> str:
    """The Arabic weekly message from an akoan study result."""
    v = result.get("verdict", "INSUFFICIENT_DATA")
    lines = ["🗓 تقرير akoan الأسبوعي (%s)" % week, ""]
    lines.append("السؤال: هل يفسّر الخبر نتائج الصفقات؟")
    lines.append("الصفقات المقيّمة: %d (ربح %d · خسارة %d) · صافي %s"
                 % (result.get("scored", 0), result.get("wins", 0),
                    result.get("losses", 0), _r(result.get("net_r"))))
    base = result.get("base_rate")
    if base is not None:
        lines.append("خط الأساس: %s — كل مقارنة تحته." % _pct(base))
    lines.append("")

    for dim in result.get("dimensions", []):
        rows = [b for b in dim.get("buckets", []) if b.get("n", 0) > 0]
        if not rows:
            continue
        name = DIM_AR.get(dim["name"], dim["name"])
        if not dim.get("comparable"):
            lines.append("• %s: جهة واحدة فقط — بلا مجموعة مقارنة." % name)
            continue
        parts = []
        for b in rows:
            label = VALUE_AR.get(b["value"], b["value"])
            if b.get("win_rate") is None:
                parts.append("%s: غير كافية" % label)
                continue
            edge = b.get("edge_vs_base")
            mark = "⭐" if b.get("significant") else ""
            parts.append("%s %s%s%s"
                         % (label, _pct(b.get("win_rate")), mark,
                            "" if edge is None else " (%s)" % _pct(edge)))
        lines.append("• %s: %s" % (name, " · ".join(parts)))
    lines.append("")

    lines.append(VERDICT_AR.get(v, v))
    if result.get("survivors"):
        lines.append("⭐ = نجا من تصحيح التعدّد (FDR q=%.2f)." % result.get("fdr_q", 0.10))
    else:
        lines.append("ولا بُعد نجا من تصحيح التعدّد.")

    cov = result.get("coverage") or {}
    if cov:
        lines.append("")
        lines.append("تغطية سجل الأخبار: %d حدث (%d صدر) خلال ~%d يوم."
                     % (cov.get("events", 0), cov.get("released", 0),
                        cov.get("span_days", 0)))
        lines.append("⚠️ التغطية جزئية — النتيجة بقدر ما في السجل، مو أكثر.")
    lines.append("")
    lines.append("محرك akoan — دعم قرار فقط، وليس نصيحة مالية.")
    return "\n".join(lines)


def load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            state = json.load(fh)
            return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False)
        os.replace(tmp, STATE_PATH)
    except OSError:
        pass  # best-effort: a lost state file re-sends one week, nothing worse


def build_result(backfill_days: int | None = 120) -> dict:
    """Run the akoan study (and refresh the ledger when asked)."""
    import study  # from akoan/
    if backfill_days:
        import events
        events.backfill(backfill_days)
    return study.study()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ASTRA weekly akoan report")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the report instead of sending it")
    parser.add_argument("--week", default=None,
                        help="label this ISO week (YYYY-Www); default: this week")
    parser.add_argument("--force", action="store_true",
                        help="send even if this week was already reported")
    parser.add_argument("--no-backfill", action="store_true",
                        help="use the ledger as-is; do not refresh from the feed")
    args = parser.parse_args(argv)

    week = args.week or iso_week()
    try:
        result = build_result(None if args.no_backfill else 120)
    except Exception as exc:
        sys.stderr.write("[weekly] study failed: %r\n" % (exc,))
        return 1

    report = format_weekly(result, week)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    state = load_state()
    sent = set(state.get("sent") or [])

    if week in sent and not args.force:
        print(json.dumps({"week": week, "verdict": result.get("verdict"),
                          "skipped": True}, ensure_ascii=False))
        return 0

    if args.dry_run or not token or not chat:
        print(report)
        if not (token and chat) and not args.dry_run:
            sys.stderr.write("[weekly] no telegram credentials; dry run only\n")
        return 0

    if bot.Telegram(token).send(chat, report):
        sent.add(week)
        state["sent"] = sorted(sent)
        save_state(state)
    print(json.dumps({"week": week, "verdict": result.get("verdict"),
                      "sent": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
