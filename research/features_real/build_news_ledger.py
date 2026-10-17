#!/usr/bin/env python3
"""Build a historical economic-event ledger from the keyless TradingView calendar.

`AGENTS.md` records the gap this closes: "We have no historical calendar in the
corpus, so its effect cannot be backtested the way the volatility model's can."
That was true when the only source was the Forex Factory mirror, which serves
one week and no released `actual`. The TradingView calendar the advisor already
reads serves the *past* too, with `actual` populated, so the gap can be filled
without a key and without scraping.

The ledger is written in the same shape `akoan/events.py` reads (append-only,
de-duplicated by `(at_utc, title, country)`), so the existing join in
`akoan/link.py` and the state machine in `akoan/state.py` work on it unchanged.

Fetching is chunked by month: a single multi-year request can be trimmed by the
server, and a chunk that fails costs one month, not the whole history. Nothing
is invented — an event with no released value keeps `actual: null`.

Run: python3 research/features_real/build_news_ledger.py [--years 2021 2026]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
for extra in (os.path.join(REPO, "advisor"), os.path.join(REPO, "akoan")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import calendar_feed as cal  # noqa: E402

DEFAULT_OUT = os.path.join(HERE, "news_ledger.jsonl.gz")
SCHEMA_VERSION = "1.0"
COUNTRIES = os.environ.get("ASTRA_NEWS_LEDGER_CCY", "US,EU")


def _open_write(path: str):
    if path.endswith(".gz"):
        import gzip
        return gzip.open(path, "wt", encoding="utf-8")
    return open(path, "w", encoding="utf-8")


def event_key(event: dict) -> str:
    """Same identity akoan uses, so a row from either source de-dupes together."""
    return "%d|%s|%s" % (int(event.get("at_utc") or 0),
                         (event.get("title") or "").strip().lower(),
                         (event.get("country") or "").strip().upper())


def _record(event: dict) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "key": event_key(event),
        "at_utc": int(event.get("at_utc") or 0),
        "title": event.get("title") or "",
        "title_ar": event.get("title_ar") or event.get("title") or "",
        "country": (event.get("country") or "").strip().upper(),
        "importance": event.get("importance"),
        "forecast": event.get("forecast"),
        "previous": event.get("previous"),
        "actual": event.get("actual"),
        "source": "tradingview",
    }


def month_windows(year: int) -> list[tuple[float, float]]:
    out = []
    for month in range(1, 13):
        start = dt.datetime(year, month, 1, tzinfo=dt.timezone.utc)
        end = (dt.datetime(year + 1, 1, 1, tzinfo=dt.timezone.utc) if month == 12
               else dt.datetime(year, month + 1, 1, tzinfo=dt.timezone.utc))
        out.append((start.timestamp(), end.timestamp()))
    return out


def fetch_years(years: list[int], sleep_s: float = 0.15,
                log=sys.stderr) -> list[dict]:
    seen: dict[str, dict] = {}
    for year in years:
        got = 0
        for _i, (a, b) in enumerate(month_windows(year)):
            events = cal.fetch_events(a, b, COUNTRIES)
            for e in events:
                rec = _record(e)
                seen.setdefault(rec["key"], rec)
            got += len(events)
            if sleep_s:
                time.sleep(sleep_s)
        log.write("[ledger] %d: %d events (unique so far %d)\n"
                  % (year, got, len(seen)))
    return sorted(seen.values(), key=lambda r: r["at_utc"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Historical news ledger")
    parser.add_argument("--years", nargs="*", type=int,
                        default=list(range(2021, 2027)))
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--sleep", type=float, default=0.15)
    args = parser.parse_args(argv)

    records = fetch_years(args.years, args.sleep)
    if not records:
        sys.stderr.write("[ledger] no events fetched; nothing written\n")
        return 1
    with _open_write(args.out) as fh:
        for rec in records:
            fh.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")

    high = [r for r in records if (r.get("importance") or -99) >= 1]
    released = [r for r in records if r.get("actual") is not None]
    span = (records[-1]["at_utc"] - records[0]["at_utc"]) / 86400.0
    sys.stderr.write(
        "[ledger] wrote %d events (%d high-impact, %d released) over %.0f days "
        "to %s\n" % (len(records), len(high), len(released), span, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
