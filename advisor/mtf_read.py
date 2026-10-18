"""Multi-timeframe read: a rule-based trade card with exact numbers.

The advisor's model writes prose; it must never be the source of an entry,
stop or target. This module computes those numbers itself from closed bars, so
a hallucinated price cannot reach the reader. The model explains the card; it
does not author it.

Design, kept deliberately small and deterministic (pure stdlib, no network, no
LLM, no clock — the caller passes the bars):

  * Five timeframes are read: M5, M15, M30, H1, H4. M15 is the project's
    operational/setup timeframe and H4 its structural authority (see
    bridge/mt5_python/schemas.py); the others confirm or contradict.
  * Each timeframe votes from three independent things — EMA fast vs slow
    (trend), RSI (momentum), and swing structure (higher-highs/higher-lows vs
    lower-highs/lower-lows). A tie is neutral.
  * A direction needs at least ``MIN_AGREE`` of the five, and the structural
    timeframe (H4) must not oppose. Otherwise the verdict is NEUTRAL and no
    numbers are emitted — a fabricated plan is worse than no plan.
  * Entry is the freshest close. The stop sits just beyond the last M15 swing,
    buffered by a fraction of ATR; the target is a fixed R multiple. Reward:risk
    is reported, never assumed.

Honesty: this is deterministic arithmetic, not a measured edge. The pipeline's
own out-of-sample edge is ~0 (AUC 0.50); nothing here claims better. Every card
carries ``validated: false``.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

# Timeframes read, coarsest last. M15 operational, H4 structural.
TIMEFRAMES = ("M5", "M15", "M30", "H1", "H4")
OPERATIONAL_TF = "M15"
STRUCTURAL_TF = "H4"
ENTRY_TF = "M5"

EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14
ATR_PERIOD = 14
MIN_BARS = EMA_SLOW + RSI_PERIOD  # enough history for both indicators

# A direction needs this many of the five timeframes, and H4 must not oppose.
MIN_AGREE = 3

# Swing pivot: a bar is a pivot if it is the extreme of SWING_LOOKBACK bars on
# each side (matches the engine's StructureEngine default).
SWING_LOOKBACK = 2

# Stop placement: beyond the last swing on the operational timeframe, plus a
# fraction of ATR so ordinary noise does not tag it out.
STOP_SWING_BARS = 12
STOP_BUFFER_ATR = 0.25

# Target as a reward:risk multiple of the risked distance.
REWARD_R = 2.0

RSI_LONG = 52.0
RSI_SHORT = 48.0


def _closes(bars: Sequence[dict]) -> List[float]:
    out: List[float] = []
    for b in bars:
        try:
            out.append(float(b["close"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _ema(values: Sequence[float], period: int) -> Optional[float]:
    """Exponential moving average, seeded with the SMA of the first period."""
    if len(values) < period:
        return None
    k = 2.0 / (period + 1.0)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1.0 - k)
    return ema


def _rsi(values: Sequence[float], period: int) -> Optional[float]:
    """Wilder's RSI over the close series."""
    if len(values) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    avg_gain, avg_loss = gains / period, losses / period
    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _true_ranges(bars: Sequence[dict]) -> List[float]:
    out: List[float] = []
    for i in range(1, len(bars)):
        try:
            high, low = float(bars[i]["high"]), float(bars[i]["low"])
            prev_close = float(bars[i - 1]["close"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return out


def _atr(bars: Sequence[dict], period: int) -> Optional[float]:
    """Simple mean of the last `period` true ranges (engine's meanTrueRange)."""
    trs = _true_ranges(bars)
    if len(trs) < period:
        return None
    window = trs[-period:]
    return sum(window) / len(window)


def _pivots(bars: Sequence[dict], field: str) -> List[int]:
    """Indices of swing pivots for `field` ('high' or 'low')."""
    values: List[float] = []
    for b in bars:
        try:
            values.append(float(b[field]))
        except (KeyError, TypeError, ValueError):
            values.append(float("nan"))
    n = len(values)
    out: List[int] = []
    for i in range(SWING_LOOKBACK, n - SWING_LOOKBACK):
        centre = values[i]
        if math.isnan(centre):
            continue
        window = values[i - SWING_LOOKBACK:i + SWING_LOOKBACK + 1]
        if any(math.isnan(v) for v in window):
            continue
        is_high = field == "high" and centre == max(window) and window.count(centre) == 1
        is_low = field == "low" and centre == min(window) and window.count(centre) == 1
        if is_high or is_low:
            out.append(i)
    return out


def _structure_bias(bars: Sequence[dict]) -> int:
    """+1 higher-highs & higher-lows, -1 lower-highs & lower-lows, else 0."""
    highs = _pivots(bars, "high")
    lows = _pivots(bars, "low")
    if len(highs) < 2 or len(lows) < 2:
        return 0
    last_high, prev_high = float(bars[highs[-1]]["high"]), float(bars[highs[-2]]["high"])
    last_low, prev_low = float(bars[lows[-1]]["low"]), float(bars[lows[-2]]["low"])
    if last_high > prev_high and last_low > prev_low:
        return 1
    if last_high < prev_high and last_low < prev_low:
        return -1
    return 0


def timeframe_read(bars: Sequence[dict]) -> Optional[Dict]:
    """One timeframe's read, or None when there is not enough closed history."""
    closes = _closes(bars)
    if len(closes) < MIN_BARS:
        return None
    ema_fast = _ema(closes, EMA_FAST)
    ema_slow = _ema(closes, EMA_SLOW)
    rsi = _rsi(closes, RSI_PERIOD)
    atr = _atr(bars, ATR_PERIOD)
    if ema_fast is None or ema_slow is None or rsi is None or atr is None:
        return None

    votes = 0
    if ema_fast > ema_slow:
        votes += 1
    elif ema_fast < ema_slow:
        votes -= 1
    if rsi >= RSI_LONG:
        votes += 1
    elif rsi <= RSI_SHORT:
        votes -= 1
    structure = _structure_bias(bars)
    votes += structure

    if votes > 0:
        direction = "LONG"
    elif votes < 0:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"

    return {
        "direction": direction,
        "rsi": round(rsi, 1),
        "ema_fast": round(ema_fast, 2),
        "ema_slow": round(ema_slow, 2),
        "atr": round(atr, 2),
        "structure": structure,
        "votes": votes,
    }


def _round(value: float) -> float:
    return round(value, 2)


def _build_card(bars_by_tf: Dict[str, Sequence[dict]],
                direction: str, long_count: int, short_count: int) -> Optional[Dict]:
    """Exact entry/stop/target from closed bars, or None if it cannot be grounded."""
    entry_bars = bars_by_tf.get(ENTRY_TF) or bars_by_tf.get(OPERATIONAL_TF)
    op_bars = bars_by_tf.get(OPERATIONAL_TF)
    if not entry_bars or not op_bars:
        return None
    try:
        entry = float(entry_bars[-1]["close"])
    except (KeyError, TypeError, ValueError):
        return None

    atr = _atr(op_bars, ATR_PERIOD)
    if atr is None or atr <= 0.0:
        return None

    swing = op_bars[-STOP_SWING_BARS:]
    try:
        if direction == "LONG":
            anchor = min(float(b["low"]) for b in swing)
            stop = anchor - STOP_BUFFER_ATR * atr
            risk = entry - stop
        else:
            anchor = max(float(b["high"]) for b in swing)
            stop = anchor + STOP_BUFFER_ATR * atr
            risk = stop - entry
    except (KeyError, TypeError, ValueError):
        return None

    if risk <= 0.0:
        return None
    reward = REWARD_R * risk
    target = entry + reward if direction == "LONG" else entry - reward

    return {
        "direction": direction,
        "entry": _round(entry),
        "stop": _round(stop),
        "target": _round(target),
        "risk": _round(risk),
        "reward": _round(reward),
        "rr": _round(reward / risk),
        "basis": ("stop beyond the last %s swing (%.2f ATR buffer); target at "
                  "%.1fR" % (OPERATIONAL_TF, STOP_BUFFER_ATR, REWARD_R)),
        "entry_timeframe": ENTRY_TF if ENTRY_TF in bars_by_tf else OPERATIONAL_TF,
        "agreement": "%d/%d" % (long_count if direction == "LONG" else short_count,
                                len(TIMEFRAMES)),
    }


def build(bars_by_tf: Dict[str, Sequence[dict]]) -> Dict:
    """The full multi-timeframe read plus a trade card when a direction holds.

    ``bars_by_tf`` maps timeframe label -> closed bars (oldest first), the same
    shape ``volatility.timeframe_features`` consumes.
    """
    read: Dict[str, Optional[Dict]] = {}
    for tf in TIMEFRAMES:
        bars = bars_by_tf.get(tf)
        read[tf] = timeframe_read(bars) if bars else None

    missing = [tf for tf in TIMEFRAMES if read[tf] is None]
    directions: Dict[str, str] = {}
    for tf in TIMEFRAMES:
        one = read[tf]
        if one is not None:
            directions[tf] = one["direction"]
    long_count = sum(1 for d in directions.values() if d == "LONG")
    short_count = sum(1 for d in directions.values() if d == "SHORT")
    structural = directions.get(STRUCTURAL_TF)

    result: Dict = {
        "available": bool(directions),
        "timeframes": {tf: read[tf] for tf in directions},
        "missing": missing,
        "agreement": {
            "long": long_count,
            "short": short_count,
            "neutral": sum(1 for d in directions.values() if d == "NEUTRAL"),
            "of": len(TIMEFRAMES),
        },
        "verdict": "NEUTRAL",
        "card": None,
        "validated": False,
        "note": "rule-based multi-timeframe read; unvalidated, not a measured edge",
    }

    if not result["available"]:
        result["reason"] = "no timeframe had enough closed bars"
        return result

    direction = None
    if long_count >= MIN_AGREE and long_count > short_count:
        direction = "LONG"
    elif short_count >= MIN_AGREE and short_count > long_count:
        direction = "SHORT"

    # The structural timeframe is the authority: if it opposes, stand aside.
    if direction is not None and structural is not None and structural not in (direction, "NEUTRAL"):
        direction = None
        result["reason"] = "H4 structure opposes the lower-timeframe agreement"
    elif direction is None:
        result["reason"] = "no majority across timeframes"

    if direction is None:
        return result

    card = _build_card(bars_by_tf, direction, long_count, short_count)
    if card is None:
        result["reason"] = "direction found but the card could not be grounded in bars"
        return result

    result["verdict"] = direction
    result["card"] = card
    return result


_AR = {"LONG": "شراء", "SHORT": "بيع", "NEUTRAL": "حياد"}


def format_card(result: Dict) -> str:
    """A compact Arabic rendering of the read, numbers verbatim."""
    if not result.get("available"):
        return "لا تتوفّر بيانات كافية للقراءة متعدّدة الأُطر."
    card = result.get("card")
    if not card:
        return "لا صفقة: %s." % result.get("reason", "لا اتجاه راجح")
    lines = [
        "XAUUSD — %s (%s فريمات متوافقة)" % (_AR.get(card["direction"], card["direction"]),
                                              card["agreement"]),
        "الدخول: %s" % card["entry"],
        "الوقف: %s" % card["stop"],
        "الهدف: %s" % card["target"],
        "R:R = %s" % card["rr"],
    ]
    tf_bits = []
    for tf, read in result["timeframes"].items():
        arrow = {"LONG": "▲", "SHORT": "▼", "NEUTRAL": "–"}[read["direction"]]
        tf_bits.append("%s %s RSI %s" % (tf, arrow, read["rsi"]))
    lines.append(" | ".join(tf_bits))
    lines.append("ملاحظة: قراءة قاعدية غير مُعتمدة، ليست نصيحة.")
    return "\n".join(lines)
