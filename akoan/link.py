#!/usr/bin/env python3
"""akoan link: join the trade ledger with the news state it lived through.

Reads `run/signal_trades.jsonl` (the tracker's append-only ledger) and stamps
every resolved trade with the causal news state from `state.py`. The join is on
the trade's own bar timestamp, so a trade is described only by what was
knowable when it opened.

A trade's label for the statistics is its *net* outcome: `TARGET` is a win,
`STOP` a loss, `TIMEOUT` neither (it is carried but excluded from win rates,
because a timeout is not a decision the news made). This mirrors
`tracker/replay.py summarize`, which counts only TARGET/STOP as scored.

The result is a plain list of dicts, so it can be written out and re-analysed
without re-reading either source. Nothing is filtered out or dropped: trades
without news are kept and labelled `no_news`, which is itself a comparison
group.
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for extra in (HERE, os.path.join(ROOT, "advisor")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import state as news_state  # noqa: E402

TRADES_PATH = os.environ.get("ASTRA_AKOAN_TRADES",
                             os.path.join(ROOT, "run", "signal_trades.jsonl"))

_SCORED = ("TARGET", "STOP")

# Identity of a trade idea: same open time, side and levels is the same idea,
# however many polls appended it. The tracker appends on every cycle, so a
# single trade appears many times in the file.
_TRADE_KEY = ("time", "direction", "entry", "stop", "target")


def trade_key(trade: dict) -> tuple:
    return tuple(trade.get(k) for k in _TRADE_KEY)


def load_trades(path: str = TRADES_PATH) -> list[dict]:
    """Resolved trades from the tracker ledger, oldest first, de-duplicated.

    Open trades are skipped: akoan explains outcomes, and an open idea has no
    outcome yet. The tracker re-appends a trade on every poll, so identical
    (time, direction, levels) rows are collapsed to one — otherwise a single
    trade would be counted hundreds of times and every rate would be fiction.
    Unreadable lines are skipped, never fatal.
    """
    unique: dict[tuple, dict] = {}
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
                if not isinstance(rec, dict) or rec.get("time") is None:
                    continue
                if not rec.get("resolved"):
                    continue
                unique.setdefault(trade_key(rec), rec)
    except OSError:
        return []
    rows = list(unique.values())
    rows.sort(key=lambda r: r["time"])
    return rows


def label_of(trade: dict) -> str:
    outcome = (trade.get("outcome") or "").upper()
    if outcome == "TARGET":
        return "WIN"
    if outcome == "STOP":
        return "LOSS"
    if outcome == "TIMEOUT":
        return "TIMEOUT"
    return "OTHER"


def win_flag(trade: dict) -> bool | None:
    """True/False for a scored trade, None when it is not scored (timeout)."""
    label = label_of(trade)
    if label == "WIN":
        return True
    if label == "LOSS":
        return False
    return None


def link(trades: list[dict], events: list[dict],
         window_days: float | None = None) -> list[dict]:
    """Stamp each trade with its causal news state and labels.

    `window_days` optionally restricts to trades inside N days of the newest
    event, which is how the backfill's real coverage is respected rather than
    silently assumed.
    """
    if window_days is not None and events:
        newest = max(e.get("at_utc") or 0 for e in events)
        oldest = newest - window_days * 86400.0
        trades = [t for t in trades if t["time"] >= oldest]

    joined: list[dict] = []
    for trade in trades:
        st = news_state.state_at(events, trade["time"], direction=trade.get("direction"))
        row = {
            "time": trade["time"],
            "direction": trade.get("direction"),
            "regime": trade.get("regime"),
            "outcome": trade.get("outcome"),
            "r_net": trade.get("r_net"),
            "label": label_of(trade),
            "win": win_flag(trade),
        }
        row.update(news_state.dimension_keys(st))
        row["_state"] = st
        joined.append(row)
    return joined


def scored(rows: list[dict]) -> list[dict]:
    """Only the trades a win rate may be computed over (TARGET/STOP)."""
    return [r for r in rows if r.get("win") is not None]
