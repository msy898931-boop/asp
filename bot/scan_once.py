#!/usr/bin/env python3
"""ASTRA one-shot scanner: the cloud-side half of the notifier.

Designed to run from a scheduler (GitHub Actions cron), not as a daemon:

  * It fetches M15 candles straight from the same upstream the bridge uses
    (gold-api.com spot + Kraken PAXGUSD), so it needs no local bridge.
  * It replays the engine chain and looks only at the bar that *just closed*.
    A signal that opened on that bar is a new idea; a signal whose outcome is
    stamped on that bar is a fresh result. Every alert therefore fires exactly
    once, with no state file to persist between runs.
  * A missed run simply misses that one bar; the next run is unaffected.

If TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are absent it prints the summary and
exits zero, so the job can run in dry mode.

Run: python3 bot/scan_once.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for extra in (os.path.join(ROOT, "tracker"),
              os.path.join(ROOT, "bridge", "mt5_python"), HERE):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import replay  # noqa: E402
import telegram_bot as bot  # noqa: E402
from live_client import LiveMt5Client  # noqa: E402

TIMEFRAME = os.environ.get("ASTRA_SCAN_TF", "M15")
LOOKBACK = int(os.environ.get("ASTRA_SCAN_LOOKBACK", "200"))
COST_USD = float(os.environ.get("ASTRA_TRACKER_SPREAD", str(replay.DEFAULT_COST_USD)))
HORIZON_BARS = int(os.environ.get("ASTRA_TRACKER_HORIZON",
                                  str(replay.DEFAULT_HORIZON_BARS)))
COOLDOWN_BARS = int(os.environ.get("ASTRA_TRACKER_COOLDOWN",
                                   str(replay.DEFAULT_COOLDOWN_BARS)))
MIN_SCORED = int(os.environ.get("ASTRA_TRACKER_MIN_SCORED",
                                str(replay.DEFAULT_MIN_SCORED)))
DIGEST_HOUR_UTC = int(os.environ.get("ASTRA_DIGEST_HOUR_UTC", "7"))
ACCOUNT_USD = float(os.environ.get("ASTRA_ACCOUNT_USD", "200"))
RISK_PCT = float(os.environ.get("ASTRA_RISK_PCT", "1.0"))
# A message for every closed bar, trade or not, is the default. Set
# ASTRA_HEARTBEAT=0 to go back to alerting only on trades.
HEARTBEAT = os.environ.get("ASTRA_HEARTBEAT", "1") not in ("0", "false", "no")
# A bar that only holds an open trade (or sits in its cooldown) says nothing
# new, so by default it stays quiet instead of repeating the same line every
# bar. Set ASTRA_HOLD_PING=1 to hear about every bar again.
HOLD_PING = os.environ.get("ASTRA_HOLD_PING", "0") in ("1", "true", "yes")
STATE_PATH = os.environ.get("ASTRA_SCAN_STATE",
                            os.path.join(ROOT, "run", "scan_state.json"))


# Reasons that mean "nothing happened, the routine is just working": an open
# trade being held, or the cooldown after one resolved.
QUIET_REASONS = {"IN_POSITION", "COOLDOWN"}


def should_heartbeat(status: dict) -> bool:
    """Whether this bar's status is worth a message of its own.

    A trade, a resolution, or a genuine no-edge read all say something new.
    Merely holding an open trade (or sitting out its cooldown) does not — that
    would repeat the same line every bar until the trade closes.
    """
    if status.get("decision") == "TRADE":
        return True
    return HOLD_PING or status.get("reason") not in QUIET_REASONS


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


def pending(signals: list, state: dict) -> tuple[list, list, bool]:
    """Signals to alert on, and whether this is the priming run.

    Everything with a bar time beyond the last alerted bar is a candidate, not
    just the newest: then the schedule only sets latency, never completeness. A
    run every 30 minutes still catches both M15 bars it slept through.

    The first run (no marks yet) primes silently: it records where the board
    stands and sends nothing, so a fresh install or a lost cache never blasts
    the whole back-catalogue.
    """
    if not state.get("primed"):
        return [], [], True
    last_idea = state.get("last_idea_bar", -1)
    last_result = state.get("last_result_bar", -1)
    ideas = [s for s in signals if s.time > last_idea]
    results = [s for s in signals if s.resolved and s.resolved_time > last_result]
    return ideas, results, False


def fetch_bars() -> list[dict]:
    """Closed bars from the same upstream the bridge reads."""
    client = LiveMt5Client()
    result = client.read_candles("XAUUSD", TIMEFRAME, LOOKBACK, closed_only=True)
    if not result.ok:
        raise RuntimeError(result.message or "read_candles failed")
    return (result.data or {}).get("candles") or []


def select_events(signals: list, newest: int) -> tuple[list, list]:
    """Split signals into (new ideas, fresh results) for the newest bar.

    Each alert is tied to one bar time, so a stateless run fires it once. An
    idea and its result can never share a bar: an idea opens on the close of
    its bar and is judged on later bars.
    """
    ideas = [s for s in signals if s.time == newest]
    results = [s for s in signals if s.resolved and s.resolved_time == newest]
    return ideas, results


def due_daily_digest(state: dict, now: datetime, hour: int) -> bool:
    """True once per UTC day, on the first run at or after `hour`.

    A cron that runs every 15 minutes will hit the hour many times; the state
    mark keeps it to one message, and a missed hour still sends on the next run
    rather than skipping the day.
    """
    if now.hour < hour:
        return False
    return state.get("last_digest_day") != now.strftime("%Y-%m-%d")


def digest_text(summary: dict) -> str:
    """The tracker scoreboard, in the same honest voice as the app.

    A win rate is only stated once there are enough scored trades to mean
    anything; until then it says so, instead of dressing up a small sample.
    """
    wins, losses = summary.get("wins", 0), summary.get("losses", 0)
    lines = [
        "📊 ملخّص ASTRA اليومي",
        "الإشارات: %s" % summary.get("total_signals", 0),
        "أصابت: %s · خسرت: %s" % (wins, losses),
        "صافي: %+.2fR · إجمالي: %+.2fR" % (
            summary.get("total_r_net") or 0.0, summary.get("total_r_gross") or 0.0),
    ]
    if summary.get("sufficient"):
        lines.append("نسبة الإصابة: %s%%" % summary.get("win_rate"))
    else:
        lines.append("نسبة الإصابة: — (لا بيانات كافية، %s/%s)"
                     % (wins + losses, summary.get("min_scored", 30)))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ASTRA one-shot scanner")
    parser.add_argument("--test-message", action="store_true",
                        help="send a confirmation ping and exit; no scan")
    args = parser.parse_args(argv)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")

    if args.test_message:
        if not token or not chat:
            sys.stderr.write("[scan] test ping skipped: telegram credentials unset\n")
            return 1
        text = ("✅ فحص الاتصال: بوت ASTRA يستقبل ويعمل.\n"
                "سأرسل الإشارات الجديدة ونتائجها هنا.")
        ok = bot.Telegram(token).send(chat, text)
        print(json.dumps({"test_message_sent": ok}))
        return 0 if ok else 1

    try:
        bars = fetch_bars()
    except Exception as exc:
        sys.stderr.write("[scan] feed unavailable: %r\n" % (exc,))
        return 1
    if len(bars) < replay.MIN_BARS + 1:
        sys.stderr.write("[scan] not enough bars: %d\n" % len(bars))
        return 1

    signals = replay.build_signals(bars, cost_usd=COST_USD,
                                   horizon_bars=HORIZON_BARS,
                                   cooldown_bars=COOLDOWN_BARS)
    summary = replay.summarize(signals, MIN_SCORED)
    status = replay.explain_last_bar(bars, cost_usd=COST_USD,
                                     horizon_bars=HORIZON_BARS,
                                     cooldown_bars=COOLDOWN_BARS,
                                     account_usd=ACCOUNT_USD, risk_pct=RISK_PCT)

    state = load_state()
    new_ideas, fresh_results, priming = pending(signals, state)
    newest = bars[-1]["time"]
    # One message per bar: the first time this bar is seen, the heartbeat
    # reports it (trade or no trade). A bar already reported is not repeated.
    bar_is_new = state.get("last_bar_time") != newest

    print(json.dumps({"newest_bar": newest, "bars": len(bars), "priming": priming,
                      "new_ideas": len(new_ideas), "fresh_results": len(fresh_results),
                      "last_bar": status, "summary": summary}, ensure_ascii=False))
    for s in new_ideas:
        print("NEW  " + json.dumps(s.as_dict(), ensure_ascii=False))
    for s in fresh_results:
        print("DONE " + json.dumps(s.as_dict(), ensure_ascii=False))

    digest_sent = False
    if token and chat and not priming:
        tg = bot.Telegram(token)
        if HEARTBEAT and bar_is_new and should_heartbeat(status):
            tg.send(chat, bot.format_last_bar(status, ACCOUNT_USD, RISK_PCT))
        else:
            # Heartbeat off (or the bar was already reported): still alert on a
            # genuinely new idea or a fresh result, so nothing is ever missed.
            for s in new_ideas:
                tg.send(chat, bot.format_new(s.as_dict()))
            for s in fresh_results:
                tg.send(chat, bot.format_result(s.as_dict(), ACCOUNT_USD))
        if due_daily_digest(state, datetime.now(timezone.utc), DIGEST_HOUR_UTC):
            tg.send(chat, digest_text(summary))
            digest_sent = True
    elif priming:
        sys.stderr.write("[scan] priming run: no alerts sent\n")
    else:
        sys.stderr.write("[scan] no telegram credentials; dry run only\n")

    state["primed"] = True
    # Advance each mark to the newest bar time we have seen. Both ideas and
    # results are keyed on distinct signals, so shifting the result mark to the
    # newest bar cannot hide a later result for an earlier idea.
    state["last_idea_bar"] = newest
    state["last_result_bar"] = newest
    state["last_bar_time"] = newest
    if digest_sent:
        state["last_digest_day"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
