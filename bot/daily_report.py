#!/usr/bin/env python3
"""ASTRA daily report: one Telegram message a day with that day's ideas.

One-shot and scheduler-friendly (GitHub Actions cron at 20:00 UTC). It reads
the signal tracker, keeps the signals whose timestamp falls on the day in
question, and reports the closed ones and any still-open idea. A state file
records which days were already sent, so a late or repeated cron slot changes
latency only, never how many reports go out.

It only reads: the tracker owns the ledger, so this job can never invent a
result. The dollar figure is the minimum position (1 oz), the honest baseline —
never an implied account return.

Run: python3 bot/daily_report.py
     python3 bot/daily_report.py --dry-run
     python3 bot/daily_report.py --date 2026-10-14
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import astra_env  # noqa: E402
import telegram_bot as bot  # noqa: E402

TRACKER_URL = (os.environ.get("ASTRA_DAILY_URL") or
               astra_env.URL + "/tracker/v1/signals")
HTTP_TIMEOUT = float(os.environ.get("ASTRA_DAILY_TIMEOUT", "30"))
STATE_PATH = os.environ.get("ASTRA_DAILY_STATE",
                            os.path.join(ROOT, "run", "daily_report_state.json"))
MIN_SCORED = 30
_USER_AGENT = "ASTRA-daily/1.0"

OUTCOME_AR = {"TARGET": "✅ هدف", "STOP": "❌ وقف", "TIMEOUT": "⌛ انتهت",
              "OPEN": "⏳ مفتوحة"}
BIAS_AR = {"BULLISH": "صاعد", "BEARISH": "هابط", "NEUTRAL": "محايد"}
REGIME_AR = {"TRENDING_UP": "اتجاه صاعد", "TRENDING_DOWN": "اتجاه هابط",
             "QUIET": "هادئ", "VOLATILE": "متقلّب"}


def today_key(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")


def day_of(sec: int) -> str:
    return datetime.fromtimestamp(sec, timezone.utc).strftime("%Y-%m-%d")


def fetch_signals(url: str = TRACKER_URL) -> dict | None:
    try:
        req = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": _USER_AGENT,
                          **astra_env.auth_headers()})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload.get("tracker")


def signals_for_day(tracker: dict, day: str) -> list[dict]:
    rows = tracker.get("signals") or []
    return [r for r in rows if r.get("time") is not None and day_of(r["time"]) == day]


def _usd(row: dict) -> float | None:
    """The minimum position's result: 1 oz moves $1 per $1/oz move."""
    r, entry, stop = row.get("r_net"), row.get("entry"), row.get("stop")
    if r is None or entry is None or stop is None:
        return None
    return round(r * abs(entry - stop), 2)


def summarize(rows: list[dict]) -> dict:
    closed = [r for r in rows if r.get("r_net") is not None]
    wins = [r for r in closed if r["r_net"] > 0]
    net_r = sum(r["r_net"] for r in closed)
    net_usd = sum(_usd(r) or 0.0 for r in closed)
    return {
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(closed) - len(wins),
        "net_r": round(net_r, 2),
        "net_usd": round(net_usd, 2),
        "open": [r for r in rows if r.get("outcome") == "OPEN"],
    }


def _hhmm(sec: int) -> str:
    return datetime.fromtimestamp(sec, timezone.utc).strftime("%H:%M")


def _reason(row: dict) -> str:
    """The basis the engine used: trend, market regime and RSI at the entry.

    It is the same context the signal carries (bias/regime/rsi), stated so the
    reader can see *why* the idea was taken — never a claim of skill. Direction
    has no measured edge (research/PHASE0_VOLATILITY_DECISION.md); this only
    reports what the rules saw.
    """
    parts = []
    bias = bot._ar(BIAS_AR, row.get("bias"))
    regime = bot._ar(REGIME_AR, row.get("regime"))
    rsi = row.get("rsi")
    if bias:
        parts.append("ميل %s" % bias)
    if regime:
        parts.append("سوق %s" % regime)
    if rsi is not None:
        parts.append("RSI %.0f" % rsi)
    return " · ".join(parts)


def _row_line(row: dict) -> str:
    head = bot._ar(OUTCOME_AR, row.get("outcome"))
    r = row.get("r_net")
    if r is None:
        tail = ""
    else:
        usd = _usd(row)
        tail = "  %+.2fR" % r
        if usd is not None:
            tail += " (%s%.2f$)" % ("+" if usd >= 0 else "-", abs(usd))
    line = "%s %s %s  %s%s" % (head, _hhmm(row["time"]), row.get("direction"),
                               row.get("entry"), tail)
    reason = _reason(row)
    if reason:
        line += "\n    ↳ %s" % reason
    return line


def format_report(rows: list[dict], day: str, min_scored: int = MIN_SCORED) -> str:
    if not rows:
        return "📋 تقرير صفقات %s (UTC)\n\nما في صفقات على هاليوم." % day

    s = summarize(rows)
    lines = ["📋 تقرير صفقات %s (UTC)" % day, ""]
    if s["closed"]:
        lines.append("المغلقة: %d · ربح %d · خسارة %d"
                     % (s["closed"], s["wins"], s["losses"]))
        lines.append("صافي: %+.2fR ≈ %s%.2f$ (أصغر حجم 1 أونصة)"
                     % (s["net_r"], "+" if s["net_usd"] >= 0 else "", s["net_usd"]))
    else:
        lines.append("لا صفقات مغلقة بعد.")

    closed = [r for r in rows if r.get("r_net") is not None]
    if closed:
        lines.append("")
        lines.append("التفاصيل:")
        lines.extend(_row_line(r) for r in closed)

    if s["open"]:
        lines.append("")
        for r in s["open"]:
            lines.append("⏳ %s %s مفتوحة · وقف %s · هدف %s"
                         % (_hhmm(r["time"]), r.get("direction"),
                            r.get("stop"), r.get("target")))
            reason = _reason(r)
            if reason:
                lines.append("    ↳ %s" % reason)

    scored = s["wins"] + s["losses"]
    lines.append("")
    if scored < min_scored:
        lines.append("⚠️ المقيّمة %d/%d — ما تكفي للحكم بعد. "
                     "الرقم لا يثبت حافة." % (scored, min_scored))
    lines.append("دعم قرار فقط، وليس نصيحة مالية.")
    return "\n".join(lines)


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
    parser = argparse.ArgumentParser(description="ASTRA daily report")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the report instead of sending it")
    parser.add_argument("--date", default=None,
                        help="report this UTC day (YYYY-MM-DD); default: today")
    parser.add_argument("--force", action="store_true",
                        help="send even if this day was already reported")
    args = parser.parse_args(argv)

    day = args.date or today_key()
    tracker = fetch_signals()
    if tracker is None:
        sys.stderr.write("[daily] tracker unreachable at %s\n" % TRACKER_URL)
        return 1

    rows = signals_for_day(tracker, day)
    report = format_report(rows, day, int(tracker.get("min_scored") or MIN_SCORED))

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    state = load_state()
    sent = set(state.get("sent") or [])

    if day in sent and not args.force:
        print(json.dumps({"day": day, "signals": len(rows), "skipped": True},
                         ensure_ascii=False))
        return 0

    if args.dry_run or not token or not chat:
        print(report)
        if not (token and chat) and not args.dry_run:
            sys.stderr.write("[daily] no telegram credentials; dry run only\n")
        return 0

    if bot.Telegram(token).send(chat, report):
        sent.add(day)
        state["sent"] = sorted(sent)
        save_state(state)
    print(json.dumps({"day": day, "signals": len(rows), "sent": True},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
