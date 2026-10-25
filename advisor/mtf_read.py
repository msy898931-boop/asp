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
    buffered by a fraction of ATR. Targets are a ladder of five R multiples of
    that same risked distance — close together, so a partial exit is always
    reachable — and the ladder's reach scales with how many timeframes agree.
    Reward:risk is reported, never assumed.

Honesty: this is deterministic arithmetic, not a measured edge. The pipeline's
own out-of-sample edge is ~0 (AUC 0.50); nothing here claims better. Every card
carries ``validated: false``.
"""

from __future__ import annotations

import math
import os
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

# Targets: a ladder of R multiples of the risked distance, close together so a
# partial exit is reachable. The ladder's reach scales with agreement — a 3/5
# read takes profit sooner, a 5/5 read lets it run. At 4/5 agreement the last
# target is exactly REWARD_R, preserving the single-target card's geometry.
REWARD_R = 2.0
TARGET_LADDER = (0.6, 1.0, 1.4, 1.75, 2.0)
LADDER_BY_AGREEMENT = {
    3: (0.5, 0.85, 1.2, 1.5, 1.8),
    4: TARGET_LADDER,
    5: (0.75, 1.25, 1.75, 2.25, 2.75),
}

RSI_LONG = 52.0
RSI_SHORT = 48.0


def _env_flag(name: str, default: str = "1") -> bool:
    """A boolean env flag; anything but an explicit off value counts as on."""
    return os.environ.get(name, default).strip().lower() not in ("0", "false", "no", "")


# The structural frame (H4) is the authority by default: when the lower frames
# agree but H4 opposes, the read stands aside. That is the honest default — a
# counter-trend setup is genuinely lower quality. An operator who wants the
# lower-timeframe agreement to produce a card anyway (with the opposition
# recorded as a note) can set ASTRA_MTF_STRUCTURAL_VETO=0. The card is still
# unvalidated either way; only the standing-aside rule changes.
STRUCTURAL_VETO = _env_flag("ASTRA_MTF_STRUCTURAL_VETO")


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

    # The ladder: five R multiples of the same risked distance, direction-aware.
    agree_n = long_count if direction == "LONG" else short_count
    mults = LADDER_BY_AGREEMENT.get(agree_n, TARGET_LADDER)
    sign = 1.0 if direction == "LONG" else -1.0
    targets = [_round(entry + sign * m * risk) for m in mults]

    # ``target``/``rr`` are the furthest rung, so the singular pair the older
    # surfaces read stays consistent with the ladder beside it.
    final_r = mults[-1]
    reward = final_r * risk
    target = entry + reward if direction == "LONG" else entry - reward

    return {
        "direction": direction,
        "entry": _round(entry),
        "stop": _round(stop),
        "target": _round(target),
        "targets": targets,
        "risk": _round(risk),
        "reward": _round(reward),
        "rr": _round(reward / risk),
        "basis": ("stop beyond the last %s swing (%.2f ATR buffer); five targets "
                  "at %s R (agreement %d/%d)"
                  % (OPERATIONAL_TF, STOP_BUFFER_ATR,
                     "/".join("%.2f" % m for m in mults), agree_n, len(TIMEFRAMES))),
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
    # With the veto off, the opposition is recorded instead and the card is
    # built anyway, so a counter-trend setup still surfaces with its warning.
    structural_note = None
    if direction is not None and structural is not None and structural not in (direction, "NEUTRAL"):
        if STRUCTURAL_VETO:
            direction = None
            result["reason"] = "H4 structure opposes the lower-timeframe agreement"
        else:
            structural_note = "H4 structure opposes the lower-timeframe agreement"
    elif direction is None:
        result["reason"] = "no majority across timeframes"

    if direction is None:
        return result

    card = _build_card(bars_by_tf, direction, long_count, short_count)
    if card is None:
        result["reason"] = "direction found but the card could not be grounded in bars"
        return result

    if structural_note:
        card["structural_warning"] = structural_note
    result["verdict"] = direction
    result["card"] = card
    return result


_AR = {"LONG": "شراء", "SHORT": "بيع", "NEUTRAL": "حياد"}


def direction_label(direction: str) -> str:
    """The Arabic label for a direction, falling back to the raw value."""
    return _AR.get(direction, direction)


def _no_trade_line(result: Dict) -> str:
    """Why a read produced no card, in the reader's language.

    The two reasons that matter are named precisely, because both can look
    like "the system sees nothing" when they are really "the system saw
    something and stood aside":
      * ``H4 structure opposes ...`` — the lower timeframes agreed on a
        direction but the structural frame was against it. That is the one
        case where a real setup is suppressed, so the opposing read is shown.
      * ``no majority ...`` — the frames genuinely disagreed.
    """
    reason = result.get("reason") or "لا اتجاه راجح"
    reads = result.get("timeframes") or {}
    if reason.startswith("H4 structure opposes"):
        lower = [tf for tf in ("M5", "M15", "M30", "H1") if tf in reads]
        longs = sum(1 for tf in lower if reads[tf]["direction"] == "LONG")
        shorts = sum(1 for tf in lower if reads[tf]["direction"] == "SHORT")
        direction = "بيع" if shorts > longs else "شراء"
        opposite = "صاعد" if direction == "بيع" else "هابط"
        return ("لا صفقة الآن: الفريمات الصغيرة تميل إلى %s (%s/%s)، "
                "لكن الإطار البنيوي H4 %s ويعارض الاتجاه — فوقفة احتياطًا."
                % (direction, max(longs, shorts), len(lower), opposite))
    return "لا صفقة: %s." % reason


def format_card(result: Dict) -> str:
    """A compact Arabic rendering of the read, numbers verbatim."""
    if not result.get("available"):
        return "لا تتوفّر بيانات كافية للقراءة متعدّدة الأُطر."
    card = result.get("card")
    if not card:
        return _no_trade_line(result)
    lines = [
        "XAUUSD — %s (%s فريمات متوافقة)" % (direction_label(card["direction"]),
                                              card["agreement"]),
        "الدخول: %s" % card["entry"],
        "الوقف: %s" % card["stop"],
    ]
    targets = card.get("targets") or ([card["target"]] if card.get("target") is not None else [])
    if targets:
        if len(targets) == 1:
            lines.append("الهدف: %s" % targets[0])
        else:
            lines.append("الأهداف (%d): %s" % (
                len(targets), " ← ".join(str(t) for t in targets)))
    lines.append("R:R = %s" % card["rr"])
    if card.get("structural_warning"):
        lines.append("⚠ الإطار البنيوي H4 يعارض الاتجاه — ثقة أقل.")
    tf_bits = []
    for tf, read in result["timeframes"].items():
        arrow = {"LONG": "▲", "SHORT": "▼", "NEUTRAL": "–"}[read["direction"]]
        tf_bits.append("%s %s RSI %s" % (tf, arrow, read["rsi"]))
    lines.append(" | ".join(tf_bits))
    lines.append("ملاحظة: قراءة قاعدية غير مُعتمدة، ليست نصيحة.")
    return "\n".join(lines)
