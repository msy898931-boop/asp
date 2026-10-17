#!/usr/bin/env python3
"""ASTRA paper trading: the pre-specified rule, run on live bars, no capital.

Plan C found one rule that cleared a significance test but not the promotion
gate (too few trades to separate from break-even):

    take a trade only when it is H4-aligned AND within 120 minutes
    after a high-impact release.

The only honest way to settle it is out-of-sample on data it has never seen —
live bars, forward in time. This module does exactly that, and nothing else:

  * It fetches the same closed bars the bot reads, replays the engine, and keeps
    the signals the rule would have taken. Those are written to an append-only
    ledger (`run/paper_trades.jsonl`), de-duplicated by (time, direction,
    entry), so repeated runs never double-count.
  * It never places an order, never touches the live tracker ledger, and never
    changes the engine. It is a shadow observer.
  * It reports the rule's book against the full book on the same bars, with the
    same "no rate without a big-enough sample" discipline the rest of the
    project uses.

The rule's parameters are fixed here and are not tuned by this module. If the
rule is wrong, the ledger will show it; that is the point.

Run: python3 bot/paper_trade.py            # update the ledger, print the read
     python3 bot/paper_trade.py --dry-run  # do not write or send
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for extra in (os.path.join(ROOT, "tracker"), os.path.join(ROOT, "advisor"),
              os.path.join(ROOT, "bridge", "mt5_python"), HERE):
    if extra in sys.path:
        sys.path.remove(extra)
    sys.path.insert(0, extra)

import calendar_feed as cal  # noqa: E402

import features  # noqa: E402
import replay  # noqa: E402


def _load_news_state():
    path = os.path.join(ROOT, "akoan", "state.py")
    spec = importlib.util.spec_from_file_location("akoan_news_state", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


news_state = _load_news_state()

LEDGER_PATH = os.environ.get("ASTRA_PAPER_LEDGER",
                             os.path.join(ROOT, "run", "paper_trades.jsonl"))
STATE_PATH = os.environ.get("ASTRA_PAPER_STATE",
                            os.path.join(ROOT, "run", "paper_state.json"))

# The rule, fixed by Plan C. Not tuned here.
POST_NEWS_MIN = float(os.environ.get("ASTRA_PAPER_POST_NEWS_MIN", "120"))
ALIGN = "ALIGNED"

TIMEFRAME = os.environ.get("ASTRA_PAPER_TF", "M15")
LOOKBACK = int(os.environ.get("ASTRA_PAPER_LOOKBACK", "400"))
COST_USD = float(os.environ.get("ASTRA_TRACKER_SPREAD", str(replay.DEFAULT_COST_USD)))
HORIZON_BARS = int(os.environ.get("ASTRA_TRACKER_HORIZON",
                                  str(replay.DEFAULT_HORIZON_BARS)))
COOLDOWN_BARS = int(os.environ.get("ASTRA_TRACKER_COOLDOWN",
                                   str(replay.DEFAULT_COOLDOWN_BARS)))
MIN_SCORED = int(os.environ.get("ASTRA_PAPER_MIN_SCORED", "30"))
# How far back to keep fetched events in the state cache, so a transient
# calendar outage does not blind the rule on the next run.
EVENT_CACHE_DAYS = float(os.environ.get("ASTRA_PAPER_EVENT_CACHE_DAYS", "6"))

_ID_KEYS = ("time", "direction", "entry")


def trade_id(row: dict) -> str:
    return "%s|%s|%s" % (row.get("time"), row.get("direction"), row.get("entry"))


def read_ledger(path: str = LEDGER_PATH) -> dict[str, dict]:
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
                    continue
                if isinstance(rec, dict) and rec.get("time"):
                    out[trade_id(rec)] = rec
    except OSError:
        pass
    return out


def append_ledger(rows: list[dict], path: str = LEDGER_PATH) -> int:
    if not rows:
        return 0
    existing = read_ledger(path)
    fresh = [r for r in rows if trade_id(r) not in existing]
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


def cached_events(state: dict, now: float) -> list[dict]:
    """Events for the rule, from a fresh fetch merged over a short cache.

    The calendar is keyless and usually available, but a transient 429 must not
    silently blind the rule. A successful fetch replaces the cache; a failed
    one falls back to what was last seen, and the caller is told which happened
    so the read can say so honestly.
    """
    fetched = cal.fetch_events(now - EVENT_CACHE_DAYS * 86400.0, now,
                               cal.COUNTRIES)
    cache = state.get("events") or []
    if fetched:
        merged = {e["at_utc"]: e for e in cache}
        merged.update({e["at_utc"]: e for e in fetched})
        cutoff = now - EVENT_CACHE_DAYS * 86400.0
        state["events"] = [e for e in merged.values() if e["at_utc"] >= cutoff]
        state["events_fetched_at"] = int(now)
        state["events_live"] = True
        return state["events"]
    state["events_live"] = False
    return cache


def rule_trades(bars: list[dict], signals: list, events: list[dict]) -> list[dict]:
    """The resolved signals the rule would have taken, stamped with their context."""
    out: list[dict] = []
    for s in signals:
        if not s.resolved:
            continue
        row = features.extract(bars, s)
        if row.get("h4_align") != ALIGN:
            continue
        st = news_state.state_at(events, s.time, direction=s.direction,
                                 recent_min=POST_NEWS_MIN)
        if not st["recent_release"]:
            continue
        row["recent_release"] = True
        row["recent_title"] = st.get("recent_title")
        row["recent_gold_dir"] = st.get("recent_gold_dir")
        row["alignment"] = st.get("alignment")
        row["year"] = str(datetime.fromtimestamp(s.time, timezone.utc).year)
        out.append(row)
    return out


def scoreboard(rows: list[dict], min_scored: int = MIN_SCORED) -> dict:
    """Win/loss/net-R over a set of resolved rows, stating a rate only when earned."""
    scored = [r for r in rows if r.get("outcome") in ("TARGET", "STOP")]
    wins = [r for r in scored if r["outcome"] == "TARGET"]
    rs = [r["r_net"] for r in rows if r.get("r_net") is not None]
    total_r = round(sum(rs), 2)
    n = len(scored)
    out = {
        "signals": len(rows),
        "scored": n,
        "wins": len(wins),
        "losses": n - len(wins),
        "net_r": total_r,
        "r_per_trade": round(total_r / len(rows), 4) if rows else 0.0,
        "sufficient": n >= min_scored,
    }
    if out["sufficient"]:
        out["win_rate"] = round(100.0 * len(wins) / n, 1)
    return out


def fmt_book(name: str, sb: dict, min_scored: int) -> str:
    rate = ("%s%%" % sb["win_rate"]) if sb.get("sufficient") else \
        "— (لا بيانات كافية، %s/%s)" % (sb["wins"] + sb["losses"], min_scored)
    return "%s: %s صفقة · إصابة %s · صافي %+.1fR · %.3fR/صفقة" % (
        name, sb["signals"], rate, sb["net_r"], sb["r_per_trade"])


def message(rule_sb: dict, book_sb: dict, new_open: int, live: bool,
            min_scored: int) -> str:
    lines = ["📝 تجربة الورق — القاعدة: متوافق H4 + خلال %d دقيقة بعد خبر" %
             int(POST_NEWS_MIN)]
    lines.append(fmt_book("القاعدة", rule_sb, min_scored))
    lines.append(fmt_book("الكتاب الكامل", book_sb, min_scored))
    if new_open:
        lines.append("جديد هذه الدورة: %d صفقة" % new_open)
    if not live:
        lines.append("⚠️ التقويم غير متاح مؤقتاً — استُخدمت آخر بيانات محفوظة")
    lines.append("وضع الظل: لا تُنفَّذ أي أوامر حقيقية.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ASTRA paper-trade observer")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the read; do not write the ledger or send")
    parser.add_argument("--notify", action="store_true",
                        help="send the read to Telegram (default: only on change)")
    args = parser.parse_args(argv)

    from scan_once import fetch_bars  # reuse the exact upstream the bot reads

    now = time.time()
    state = load_state()
    try:
        bars = fetch_bars()
    except Exception as exc:  # noqa: BLE001 - feed down: say so, change nothing
        sys.stderr.write("[paper] feed unavailable: %r\n" % (exc,))
        print(json.dumps({"error": "feed_unavailable"}))
        return 1
    if len(bars) < replay.MIN_BARS + 1:
        sys.stderr.write("[paper] not enough bars: %d\n" % len(bars))
        return 1

    signals = replay.build_signals(bars, cost_usd=COST_USD,
                                   horizon_bars=HORIZON_BARS,
                                   cooldown_bars=COOLDOWN_BARS)
    events = cached_events(state, now)
    rows = rule_trades(bars, signals, events)

    added = 0
    if not args.dry_run:
        added = append_ledger(rows)
    ledger = read_ledger() if not args.dry_run else {trade_id(r): r for r in rows}

    rule_sb = scoreboard(list(ledger.values()))
    book_sb = scoreboard([features.extract(bars, s) for s in signals if s.resolved])
    live = bool(state.get("events_live"))
    newest = bars[-1]["time"]
    today = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
    due_daily = state.get("last_paper_day") != today
    # Speak when something happened (a new rule trade was recorded) or once a
    # day, so the channel stays a trial record and not a heartbeat.
    changed = added > 0 or due_daily
    state["last_bar_time"] = newest
    if due_daily:
        state["last_paper_day"] = today
    if not args.dry_run:
        save_state(state)

    report = {"newest_bar": newest, "bars": len(bars), "events": len(events),
              "events_live": live, "rule_trades_in_window": len(rows),
              "ledger_added": added, "ledger_total": len(ledger),
              "rule": rule_sb, "book": book_sb}
    print(json.dumps(report, ensure_ascii=False))

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if token and chat and not args.dry_run and (args.notify or changed):
        import telegram_bot as bot
        bot.Telegram(token).send(chat, message(rule_sb, book_sb, added, live,
                                               MIN_SCORED))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
