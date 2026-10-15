"""Per-trade context extraction — the journal's memory of *why* a trade was taken.

The replay engine records a trade's outcome but not the conditions it opened
under, so a loss cannot be explained after the fact. This module turns each
trade into a flat row of context — regime, session, weekday, RSI, ATR scale,
higher-timeframe agreement — so losses can be grouped and compared honestly.

Two rules the honesty of every downstream number depends on:

  * **No lookahead.** Every feature is computed from bars up to and including
    the entry bar, never from bars the decision could not have seen. A feature
    that peeks would manufacture an edge that does not exist live.
  * **Pure and stdlib-only.** No network, no clock, no file access: given the
    same bars and signal, the row is identical, so a backtest and the live
    journal can be joined on the same keys.

`extract` takes the M15 bars and one signal (a ``replay.Signal`` or the plain
dict the tracker serves) and returns a flat, JSON-safe dict.
"""

from __future__ import annotations

import datetime as dt

H4_SECONDS = 14400
_EMA_FAST = 20
_EMA_SLOW = 50

# Session buckets in UTC. Gold's liquidity is not flat across the day, so the
# hour is worth carrying as a named bucket rather than a raw integer.
SESSIONS = ((0, 4, "ASIA_LATE"), (4, 8, "LONDON_PRE"), (8, 12, "LONDON"),
            (12, 16, "LONDON_NY"), (16, 20, "NY"), (20, 24, "ASIA_EARLY"))
WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


def session_of(ts: int) -> str:
    hour = dt.datetime.fromtimestamp(ts, dt.timezone.utc).hour
    for lo, hi, name in SESSIONS:
        if lo <= hour < hi:
            return name
    return "UNKNOWN"


def weekday_of(ts: int) -> str:
    return WEEKDAYS[dt.datetime.fromtimestamp(ts, dt.timezone.utc).weekday()]


def _ema_last(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    result = values[0]
    for v in values[1:]:
        result = alpha * v + (1.0 - alpha) * result
    return result


def h4_bars(bars: list[dict]) -> list[dict]:
    """Resample M15 bars into H4, keeping the same open/high/low/close order."""
    out: list[dict] = []
    current: dict | None = None
    for b in bars:
        key = b["time"] - (b["time"] % H4_SECONDS)
        if current is None or current["time"] != key:
            if current is not None:
                out.append(current)
            current = {"time": key, "open": b["open"], "high": b["high"],
                       "low": b["low"], "close": b["close"]}
        else:
            current["high"] = max(current["high"], b["high"])
            current["low"] = min(current["low"], b["low"])
            current["close"] = b["close"]
    if current is not None:
        out.append(current)
    return out


def h4_alignment(bars_upto_entry: list[dict], direction: str) -> str:
    """Whether the H4 trend agrees with the trade, from bars seen so far.

    ``bars_upto_entry`` must end at the entry bar. The comparison is the same
    fast/slow EMA cross the M15 engine uses, read on the H4 series — extra
    information the M15 decision did not use.
    """
    h4 = h4_bars(bars_upto_entry)
    if len(h4) < _EMA_SLOW:
        return "UNKNOWN"
    closes = [b["close"] for b in h4]
    fast = _ema_last(closes, _EMA_FAST)
    slow = _ema_last(closes, _EMA_SLOW)
    if fast == slow:
        return "FLAT"
    up = fast > slow
    agree = (direction == "LONG" and up) or (direction == "SHORT" and not up)
    return "ALIGNED" if agree else "AGAINST"


def _get(signal, key, default=None):
    if isinstance(signal, dict):
        return signal.get(key, default)
    return getattr(signal, key, default)


def extract(bars: list[dict], signal) -> dict:
    """One flat context row for a trade, from bars up to and including entry.

    ``signal`` carries an ``index`` into ``bars`` and a ``direction``; it may be
    a ``replay.Signal`` or the dict form the tracker serves. The returned row
    has only primitives, so it writes to JSONL unchanged.
    """
    index = int(_get(signal, "index"))
    direction = str(_get(signal, "direction"))
    entry = float(_get(signal, "entry"))
    stop = float(_get(signal, "stop"))
    ts = int(_get(signal, "time"))
    seen = bars[:index + 1]

    risk = abs(entry - stop)
    atr = float(_get(signal, "atr") or 0.0)
    if atr <= 0.0:
        atr = _last_atr(seen)

    row = {
        "time": ts,
        "index": index,
        "direction": direction,
        "regime": _get(signal, "regime"),
        "bias": _get(signal, "bias"),
        "rsi": _get(signal, "rsi"),
        "session": session_of(ts),
        "weekday": weekday_of(ts),
        "hour_utc": dt.datetime.fromtimestamp(ts, dt.timezone.utc).hour,
        "h4_align": h4_alignment(seen, direction),
        "atr": round(atr, 4),
        # ATR as a fraction of price is the "how big is a normal move" scale:
        # a fixed spread hurts far more when this is small.
        "atr_pct": round(atr / entry, 6) if entry else 0.0,
        "risk_usd": round(risk, 4),
    }
    row["outcome"] = _get(signal, "outcome")
    row["r_net"] = _get(signal, "r_net")
    return row


def _last_atr(bars: list[dict], period: int = 14) -> float:
    if len(bars) < 2:
        return 0.0
    start = len(bars) - period if len(bars) > period else 1
    total = 0.0
    count = 0
    for i in range(start, len(bars)):
        prev = bars[i - 1]["close"]
        tr = max(bars[i]["high"] - bars[i]["low"],
                 abs(bars[i]["high"] - prev), abs(bars[i]["low"] - prev))
        total += tr
        count += 1
    return total / count if count else 0.0


# The feature keys a row always carries, in a stable order. The store and the
# gate both iterate this, so a new feature cannot silently change their shape.
FEATURE_KEYS = ("regime", "bias", "direction", "session", "weekday",
                "h4_align", "hour_utc")
NUMERIC_KEYS = ("rsi", "atr_pct", "risk_usd")


def rows_for_signals(bars: list[dict], signals: list, resolved_only: bool = True) -> list[dict]:
    """Turn a replayed signal list into context rows.

    Only resolved trades carry an outcome to learn from, so the default skips
    the still-open tail. Order is preserved, which keeps the rows replayable in
    the same sequence the engine produced them.
    """
    rows: list[dict] = []
    for s in signals:
        if resolved_only and not _get(s, "resolved"):
            continue
        rows.append(extract(bars, s))
    return rows


def group_by(rows: list[dict], key: str) -> dict:
    """Partition rows by one feature's value, skipping None."""
    groups: dict = {}
    for r in rows:
        value = r.get(key)
        if value is None:
            continue
        groups.setdefault(value, []).append(r)
    return groups
