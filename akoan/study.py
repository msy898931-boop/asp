#!/usr/bin/env python3
"""akoan study: the same analysis over real history, not just the live ledger.

The live ledger is honest but thin — as of this writing it holds three days of
trades that happened to fall in a news-quiet stretch, so every dimension reads
"not comparable". That is a true answer, but not a useful one.

This module gives the engine real depth: it resamples the committed M1 gold
corpus (`research/data/xauusd_m1/xauusd_m1_real.csv`) to M15, replays the
project's own signal chain over it (`tracker/replay.py`, the same rules the
engine uses), and joins the resulting trades with the event ledger. The study
result is therefore the same shape as the live one, on months instead of days.

Honesty rules, kept from the rest of akoan:

  * The replay is the *project's* chain, unmodified — so the trades studied are
    the trades the engine would have taken, not a convenient substitute.
  * Coverage is reported, not assumed: the event ledger spans a fixed window,
    and trades outside it are counted as `no_news` rather than invented into a
    category.
  * A news-quiet study still yields a verdict. "No difference measured" is a
    result; it is not dressed up as one.

Run: python3 akoan/study.py
     python3 akoan/study.py --json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for extra in (HERE, os.path.join(ROOT, "tracker"), os.path.join(ROOT, "advisor")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import engine as eng  # noqa: E402
import events as ev  # noqa: E402
import link as lk  # noqa: E402
import report as rep  # noqa: E402
from replay import build_signals  # noqa: E402

CORPUS = os.environ.get(
    "ASTRA_AKOAN_CORPUS",
    os.path.join(ROOT, "research", "data", "xauusd_m1", "xauusd_m1_real.csv"))
M15_SECONDS = 900


def _epoch(stamp: str) -> int | None:
    text = (stamp or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return int(datetime.strptime(text, fmt)
                       .replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    return None


def load_m1(path: str = CORPUS) -> list[dict]:
    """The M1 corpus as rows with an epoch `time`. Empty when absent."""
    rows: list[dict] = []
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                ts = _epoch(row.get("timestamp") or row.get("time") or "")
                if ts is None:
                    continue
                try:
                    rows.append({
                        "time": ts,
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                    })
                except (KeyError, TypeError, ValueError):
                    continue
    except OSError:
        return []
    rows.sort(key=lambda r: r["time"])
    return rows


def resample(bars: list[dict], seconds: int = M15_SECONDS) -> list[dict]:
    """Aggregate M1 into fixed buckets of `seconds`, stamped at bucket start.

    The bucket stamp is what the news join uses, so it must be the *start* of
    the bar (the instant the trade could open), never the close.
    """
    out: list[dict] = []
    current: dict | None = None
    for bar in bars:
        start = bar["time"] - (bar["time"] % seconds)
        if current is None or current["time"] != start:
            if current is not None:
                out.append(current)
            current = {"time": start, "open": bar["open"], "high": bar["high"],
                       "low": bar["low"], "close": bar["close"]}
        else:
            current["high"] = max(current["high"], bar["high"])
            current["low"] = min(current["low"], bar["low"])
            current["close"] = bar["close"]
    if current is not None:
        out.append(current)
    return out


def _signals_to_trades(signals) -> list[dict]:
    """Resolved signals in the ledger's shape, so `link.py` can consume them."""
    return [s.as_dict() for s in signals if s.resolved]


def study(corpus: str = CORPUS) -> dict:
    """Replay the chain over the corpus and analyse it against the news."""
    m1 = load_m1(corpus)
    bars = resample(m1)
    signals = build_signals(bars) if len(bars) >= 60 else []
    trades = _signals_to_trades(signals)
    events = list(ev.load().values())
    rows = lk.link(trades, events)
    result = eng.analyse(rows)

    cov = ev.coverage()
    result["coverage"] = cov
    result["source"] = "study"
    result["corpus_bars_m1"] = len(m1)
    result["corpus_bars_m15"] = len(bars)
    if bars:
        result["corpus_from_utc"] = bars[0]["time"]
        result["corpus_to_utc"] = bars[-1]["time"]
    # How much of the studied period the event ledger actually covers.
    if bars and cov.get("first_utc") and cov.get("last_utc"):
        covered = [b for b in bars
                   if cov["first_utc"] <= b["time"] <= cov["last_utc"]]
        result["bars_with_news_coverage"] = len(covered)
        result["news_coverage_pct"] = round(100.0 * len(covered) / len(bars), 1)
    result["trades_total"] = len(trades)
    result["trades_linked"] = len(rows)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="akoan historical study")
    parser.add_argument("--json", action="store_true", help="print the raw result")
    parser.add_argument("--corpus", default=CORPUS)
    args = parser.parse_args(argv)

    result = study(args.corpus)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(rep.format_report(result, result.get("coverage")))
    print("")
    print("المصدر: دراسة تاريخية على الكوربس (%d شمعة M1 → %d شمعة M15)"
          % (result.get("corpus_bars_m1", 0), result.get("corpus_bars_m15", 0)))
    if result.get("news_coverage_pct") is not None:
        print("نسبة الفترة المغطّاة بسجل الأخبار: %.1f%%"
              % result["news_coverage_pct"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
