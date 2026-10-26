"""SMC/ICT fact sheet: deterministic market-structure facts for XAUUSD.

The SMC engine (``advisor/smc_engine.py``) hands these facts to an external
model and asks it to reason like a Smart Money Concepts / Inner Circle Trader
analyst. This module forms **no opinion**. It computes only what can be read
off the closed bars — swing pivots, the last break of structure and change of
character, the premium/discount split of the active leg, equal-highs/equal-lows
liquidity pools and recent sweeps, fair-value gaps, order blocks, and the
session ranges. The model interprets them; the numbers themselves are
arithmetic.

Why a fact sheet and not a signal: the project's whole honesty contract is that
no number reaches the reader unless something deterministic computed it. A
model that is asked to "find the order block" will invent one. A model that is
handed the order blocks the code found, and asked only to *reason* about them,
cannot fabricate a level. So the levels are computed here and the model's job
is prose plus a plan over facts it did not author.

Design notes:

  * Pure stdlib, no network, no LLM, no wall clock. The caller passes closed
    bars; every value is a function of those bars alone, so a read is
    reproducible and testable.
  * A **body close** breaks structure, never a wick. A wick beyond a level that
    closes back inside is a *sweep* (a stop-hunt), and is recorded as such —
    telling those two apart is the entire point of the methodology.
  * Session windows are fixed UTC hours (Asian 00:00-07:00, London 07:00-16:00,
    New York 12:00-21:00). Gold trades nearly 24h, so this is a stated
    convention, not a broker's session table.
  * Nothing here is a measured edge. It is structure, described. The caller
    stamps every read ``validated: false``.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Sequence, Tuple

# A bar is a swing pivot if it is the extreme of this many bars on each side.
# Matches the engine's StructureEngine default and mtf_read.
SWING_LOOKBACK = 2
ATR_PERIOD = 14

# Two swing highs/lows within this many ATRs are "equal" — a resting pool of
# stops (EQH/EQL) that smart money is expected to reach for.
EQUAL_TOL_ATR = 0.15

# How far back a sweep is looked for, in bars of the frame being read.
SWEEP_LOOKBACK_BARS = 80

# A fair-value gap smaller than this fraction of ATR is noise, not imbalance.
FVG_MIN_ATR = 0.05

# How many candles to scan back from a break for the order block that fed it.
OB_SCAN_BARS = 4

# Fixed UTC session windows (start_hour inclusive, end_hour exclusive).
SESSIONS = (("asian", 0, 7), ("london", 7, 16), ("newyork", 12, 21))

FACTS_NOTE = (
    "Deterministic SMC/ICT facts computed from closed bars: swing structure, "
    "the last BOS/CHoCH, the premium/discount split, equal-highs/equal-lows "
    "liquidity, sweeps, fair-value gaps, order blocks, session ranges and the "
    "multi-timeframe confluence tally. These are readings, not a measured edge."
)


# ── numeric primitives ───────────────────────────────────────────────────

def _float(value) -> Optional[float]:
    """A real finite number from a bar field, or None. Never guesses."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _closes(bars: Sequence[dict]) -> List[float]:
    out: List[float] = []
    for b in bars:
        value = _float(b.get("close"))
        if value is not None:
            out.append(value)
    return out


def _true_ranges(bars: Sequence[dict]) -> List[float]:
    out: List[float] = []
    for i in range(1, len(bars)):
        high, low = _float(bars[i].get("high")), _float(bars[i].get("low"))
        prev_close = _float(bars[i - 1].get("close"))
        if high is None or low is None or prev_close is None:
            continue
        out.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return out


def atr(bars: Sequence[dict], period: int = ATR_PERIOD) -> Optional[float]:
    """Mean of the last ``period`` true ranges (the engine's meanTrueRange)."""
    trs = _true_ranges(bars)
    if len(trs) < period:
        return None
    window = trs[-period:]
    return sum(window) / len(window)


def _pivots(bars: Sequence[dict], field: str) -> List[int]:
    """Indices of swing pivots for ``field`` ('high' or 'low')."""
    values: List[Optional[float]] = [_float(b.get(field)) for b in bars]
    n = len(values)
    out: List[int] = []
    for i in range(SWING_LOOKBACK, n - SWING_LOOKBACK):
        centre = values[i]
        if centre is None:
            continue
        window = values[i - SWING_LOOKBACK:i + SWING_LOOKBACK + 1]
        if any(v is None for v in window):
            continue
        win = [v for v in window if v is not None]
        is_high = (field == "high" and centre == max(win)
                   and win.count(centre) == 1)
        is_low = (field == "low" and centre == min(win)
                  and win.count(centre) == 1)
        if is_high or is_low:
            out.append(i)
    return out


# ── market structure: BOS and CHoCH ──────────────────────────────────────

def structure_events(bars: Sequence[dict]) -> Tuple[str, List[dict]]:
    """Walk the bars and record every structural break, in order.

    A break is a **body close** beyond the most recent confirmed swing extreme.
    The first break against the prevailing trend is a **CHoCH** (change of
    character — the reversal signal); a break with the trend is a **BOS**
    (break of structure — continuation). A pivot is only usable once
    ``SWING_LOOKBACK`` bars have closed after it, which is what makes the read
    causal rather than clairvoyant.

    Returns ``(bias, events)`` where ``bias`` is 'bullish', 'bearish' or
    'neutral', and each event carries its kind, direction, broken level, bar
    index and time.
    """
    high_pts = [(i, _float(bars[i].get("high"))) for i in _pivots(bars, "high")]
    low_pts = [(i, _float(bars[i].get("low"))) for i in _pivots(bars, "low")]
    high_pts = [(i, p) for i, p in high_pts if p is not None]
    low_pts = [(i, p) for i, p in low_pts if p is not None]

    hi_ptr = lo_ptr = 0
    active_high: Optional[float] = None
    active_low: Optional[float] = None
    trend: Optional[str] = None
    events: List[dict] = []

    for i in range(len(bars)):
        while hi_ptr < len(high_pts) and high_pts[hi_ptr][0] + SWING_LOOKBACK <= i:
            active_high = high_pts[hi_ptr][1]
            hi_ptr += 1
        while lo_ptr < len(low_pts) and low_pts[lo_ptr][0] + SWING_LOOKBACK <= i:
            active_low = low_pts[lo_ptr][1]
            lo_ptr += 1

        close = _float(bars[i].get("close"))
        if close is None:
            continue
        if active_high is not None and close > active_high:
            events.append({
                "kind": "CHoCH" if trend == "bearish" else "BOS",
                "direction": "bullish",
                "level": round(active_high, 2),
                "index": i, "time": bars[i].get("time"),
            })
            trend = "bullish"
            active_high = None  # consumed; the next pivot sets a new one
        elif active_low is not None and close < active_low:
            events.append({
                "kind": "CHoCH" if trend == "bullish" else "BOS",
                "direction": "bearish",
                "level": round(active_low, 2),
                "index": i, "time": bars[i].get("time"),
            })
            trend = "bearish"
            active_low = None

    return (trend or "neutral"), events


def _structure(bars: Sequence[dict]) -> dict:
    bias, events = structure_events(bars)
    n = len(bars)
    recent = events[-4:]
    shaped = []
    for ev in recent:
        shaped.append({
            "kind": ev["kind"], "direction": ev["direction"],
            "level": ev["level"], "time": ev["time"],
            "bars_ago": n - 1 - ev["index"],
        })
    return {
        "bias": bias,
        "last_event": shaped[-1] if shaped else None,
        "events": shaped,
        "bars": n,
    }


# ── premium / discount ───────────────────────────────────────────────────

def _premium_discount(bars: Sequence[dict]) -> Optional[dict]:
    """Split the active dealing range at equilibrium (50%) and place the close.

    The range runs between the most recent swing high and swing low. When price
    has already traded beyond one of them, the range is **extended to include
    the close** and flagged ``extended`` — otherwise the percentage would read
    above 100% and look like an arithmetic error. Above equilibrium is premium
    (a place to sell, per the methodology); below is discount (a place to buy).
    This reports the split; it does not call it a signal.
    """
    highs = _pivots(bars, "high")
    lows = _pivots(bars, "low")
    if not highs or not lows:
        return None
    hi_i, lo_i = highs[-1], lows[-1]
    hi_p, lo_p = _float(bars[hi_i].get("high")), _float(bars[lo_i].get("low"))
    close = _float(bars[-1].get("close"))
    if hi_p is None or lo_p is None or close is None or hi_p <= lo_p:
        return None
    extended = close > hi_p or close < lo_p
    range_high = max(hi_p, close)
    range_low = min(lo_p, close)
    equilibrium = (range_high + range_low) / 2.0
    if close > equilibrium:
        zone = "premium"
    elif close < equilibrium:
        zone = "discount"
    else:
        zone = "equilibrium"
    return {
        "leg_high": round(hi_p, 2),
        "leg_low": round(lo_p, 2),
        "equilibrium": round(equilibrium, 2),
        "leg_direction": "up" if hi_i > lo_i else "down",
        "extended": extended,
        "zone": zone,
        "close_position_pct": round(
            (close - range_low) / (range_high - range_low) * 100.0, 1),
    }


# ── liquidity: equal highs/lows and sweeps ───────────────────────────────

def _clusters(pts: Sequence[Tuple[int, float]], tol: float) -> List[dict]:
    """Group swing points whose prices sit within ``tol`` of each other.

    A cluster of two or more is a resting pool: equal highs (stops above) or
    equal lows (stops below). Returns the cluster's mean level and touch count.
    """
    if not pts or tol <= 0:
        return []
    ordered = sorted(pts, key=lambda p: p[1])
    groups: List[List[Tuple[int, float]]] = [[ordered[0]]]
    for pt in ordered[1:]:
        if abs(pt[1] - groups[-1][-1][1]) <= tol:
            groups[-1].append(pt)
        else:
            groups.append([pt])
    out: List[dict] = []
    for group in groups:
        if len(group) < 2:
            continue
        level = sum(p[1] for p in group) / len(group)
        out.append({
            "level": round(level, 2),
            "touches": len(group),
            "last_index": max(p[0] for p in group),
        })
    return out


def _highs_lows(bars: Sequence[dict]) -> Tuple[List[Tuple[int, float]],
                                              List[Tuple[int, float]]]:
    """Swing-high and swing-low (index, price) pairs, None-valued dropped."""
    high_pts: List[Tuple[int, float]] = []
    for i in _pivots(bars, "high"):
        value = _float(bars[i].get("high"))
        if value is not None:
            high_pts.append((i, value))
    low_pts: List[Tuple[int, float]] = []
    for i in _pivots(bars, "low"):
        value = _float(bars[i].get("low"))
        if value is not None:
            low_pts.append((i, value))
    return high_pts, low_pts


def _liquidity(bars: Sequence[dict], atr_value: Optional[float]) -> dict:
    tol = (EQUAL_TOL_ATR * atr_value) if atr_value else 0.0
    high_pts, low_pts = _highs_lows(bars)
    return {
        "equal_highs": _clusters(high_pts, tol),
        "equal_lows": _clusters(low_pts, tol),
        "sweeps": _sweeps(bars),
    }


def _sweeps(bars: Sequence[dict]) -> List[dict]:
    """Recent stop-hunts: a wick beyond a prior swing that closes back inside.

    This is the "retail liquidity is market-maker fuel" idea made concrete — a
    pool was reached and rejected, which the methodology reads as a precursor to
    a reversal. Only the wick/close relationship is recorded; the interpretation
    is the model's.
    """
    highs = _pivots(bars, "high")
    lows = _pivots(bars, "low")
    n = len(bars)
    start = max(0, n - SWEEP_LOOKBACK_BARS)
    out: List[dict] = []
    for i in range(start, n):
        high, low = _float(bars[i].get("high")), _float(bars[i].get("low"))
        close = _float(bars[i].get("close"))
        if high is None or low is None or close is None:
            continue
        prior_high = next(
            (_float(bars[j].get("high")) for j in reversed(highs)
             if j + SWING_LOOKBACK <= i), None)
        if prior_high is not None and high > prior_high and close < prior_high:
            out.append({"side": "above", "level": round(prior_high, 2),
                        "extreme": round(high, 2), "time": bars[i].get("time"),
                        "bars_ago": n - 1 - i})
        prior_low = next(
            (_float(bars[j].get("low")) for j in reversed(lows)
             if j + SWING_LOOKBACK <= i), None)
        if prior_low is not None and low < prior_low and close > prior_low:
            out.append({"side": "below", "level": round(prior_low, 2),
                        "extreme": round(low, 2), "time": bars[i].get("time"),
                        "bars_ago": n - 1 - i})
    return out[-8:]


# ── fair-value gaps ──────────────────────────────────────────────────────

def _fair_value_gaps(bars: Sequence[dict], atr_value: Optional[float]) -> List[dict]:
    """Three-candle imbalances, with whether a later bar traded back into them.

    A bullish gap is a three-candle window whose first high sits below the third
    low (price left too fast); a bearish gap is the mirror. An unfilled gap is a
    magnet the methodology expects price to return to.
    """
    out: List[dict] = []
    for i in range(1, len(bars) - 1):
        a, c = bars[i - 1], bars[i + 1]
        ah, al = _float(a.get("high")), _float(a.get("low"))
        ch, cl = _float(c.get("high")), _float(c.get("low"))
        if ah is None or al is None or ch is None or cl is None:
            continue
        zone: Optional[Tuple[str, float, float]] = None
        if cl > ah:
            zone = ("bullish", ah, cl)
        elif al > ch:
            zone = ("bearish", ch, al)
        if zone is None:
            continue
        kind, zone_lo, zone_hi = zone
        size = zone_hi - zone_lo
        if atr_value and size < FVG_MIN_ATR * atr_value:
            continue
        filled = False
        for later in bars[i + 2:]:
            lh, ll = _float(later.get("high")), _float(later.get("low"))
            if lh is None or ll is None:
                continue
            if ll <= zone_hi and lh >= zone_lo:
                filled = True
                break
        out.append({
            "kind": kind,
            "low": round(zone_lo, 2), "high": round(zone_hi, 2),
            "equilibrium": round((zone_lo + zone_hi) / 2.0, 2),
            "size": round(size, 2), "filled": filled,
            "bars_ago": len(bars) - 1 - i,
        })
    return out[-10:]


# ── order blocks ─────────────────────────────────────────────────────────

def _order_blocks(bars: Sequence[dict], events: Sequence[dict]) -> List[dict]:
    """The last opposing candle before each structural break.

    A bullish order block is the last down-close candle before the up move that
    broke structure — the footprint the methodology expects to hold on a
    retest. A bearish one is the mirror. Only the candle's own range is
    recorded, so the zone is a fact, not a guess.
    """
    out: List[dict] = []
    for ev in events[-6:]:
        i = ev["index"]
        want_down = ev["direction"] == "bullish"
        for j in range(i - 1, max(-1, i - 1 - OB_SCAN_BARS), -1):
            op, cl = _float(bars[j].get("open")), _float(bars[j].get("close"))
            hi, lo = _float(bars[j].get("high")), _float(bars[j].get("low"))
            if op is None or cl is None or hi is None or lo is None:
                continue
            if (cl < op) == want_down and cl != op:
                out.append({
                    "kind": "bullish" if want_down else "bearish",
                    "low": round(lo, 2), "high": round(hi, 2),
                    "broken_by": ev["level"], "bars_ago": len(bars) - 1 - j,
                })
                break
    return out


# ── session ranges ───────────────────────────────────────────────────────

def session_ranges(bars: Sequence[dict]) -> Optional[dict]:
    """Asian/London/New York ranges for the latest UTC day, plus the prior day.

    Needs intraday bars (they carry ``time``); returns None when the frame is
    daily or the timestamps are missing.
    """
    days: Dict[Tuple[int, int, int], List[Tuple[int, dict]]] = {}
    for b in bars:
        stamp = b.get("time")
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
            continue
        gm = time.gmtime(int(stamp))
        days.setdefault((gm.tm_year, gm.tm_mon, gm.tm_mday), []).append(
            (gm.tm_hour, b))
    if not days:
        return None
    ordered = sorted(days.items())

    def window(rows: Sequence[Tuple[int, dict]], lo: int, hi: int) -> Optional[dict]:
        sel = [b for hour, b in rows if lo <= hour < hi]
        highs: List[float] = []
        lows: List[float] = []
        for b in sel:
            h, low = _float(b.get("high")), _float(b.get("low"))
            if h is not None:
                highs.append(h)
            if low is not None:
                lows.append(low)
        if not highs or not lows:
            return None
        return {"high": round(max(highs), 2), "low": round(min(lows), 2),
                "bars": len(sel)}

    last_day, last_rows = ordered[-1]
    out: dict = {"date": "%04d-%02d-%02d" % last_day}
    for name, lo, hi in SESSIONS:
        out[name] = window(last_rows, lo, hi)
    if len(ordered) >= 2:
        _, prior_rows = ordered[-2]
        out["prior_day"] = window(prior_rows, 0, 24)
    else:
        out["prior_day"] = None
    return out


# ── multi-timeframe confluence ───────────────────────────────────────────

# Higher frames carry double weight: they set the bias the lower frames trade
# into, the same role split the rest of the advisor uses (mtf_read's ladder).
CONFLUENCE_WEIGHTS = {"M5": 1, "M15": 1, "M30": 1, "H1": 2, "H4": 2}


def _confluence(facts_by_tf: Dict[str, dict]) -> Optional[dict]:
    """Tally how many frames' *structure* bias point the same way.

    Each frame's bias is already a fact on this sheet (``structure.bias``); this
    only counts them, so it invents nothing. It is a reading of the sheet, not a
    signal and not a measured edge — the model still decides what to do with it.
    Returns None when no frame produced a read.

    ``opposing_frames`` is the honest half: a 4/5 agreement that includes H4
    against the call is weaker than a 3/5 that does not, and the caller can see
    which is which rather than trusting a single number.
    """
    counts = {"bullish": 0, "bearish": 0, "neutral": 0}
    bulls: List[str] = []
    bears: List[str] = []
    neutrals: List[str] = []
    weighted = 0
    for tf in facts_by_tf:
        bias = ((facts_by_tf[tf] or {}).get("structure") or {}).get("bias")
        if bias not in counts:
            continue
        counts[bias] += 1
        if bias == "bullish":
            bulls.append(tf)
            weighted += CONFLUENCE_WEIGHTS.get(tf, 1)
        elif bias == "bearish":
            bears.append(tf)
            weighted -= CONFLUENCE_WEIGHTS.get(tf, 1)
        else:
            neutrals.append(tf)
    total = len(bulls) + len(bears) + len(neutrals)
    if not total:
        return None
    if weighted > 0:
        dominant, agreeing, opposing = "bullish", bulls, bears
    elif weighted < 0:
        dominant, agreeing, opposing = "bearish", bears, bulls
    else:
        dominant, agreeing, opposing = "neutral", neutrals, bulls + bears
    return {
        "counts": counts,
        "weighted_score": weighted,
        "dominant": dominant,
        "agreeing_frames": agreeing,
        "opposing_frames": opposing,
        "agreeing": len(agreeing),
        "total_frames": total,
        "agreement": round(len(agreeing) / total, 2),
        "note": ("A tally of the frames' structure bias. A reading of the "
                 "sheet, not a measured edge."),
    }


# ── the whole sheet ──────────────────────────────────────────────────────

def timeframe_facts(bars: Sequence[dict]) -> Optional[dict]:
    """Every SMC fact for one timeframe, or None without enough history."""
    if len(bars) < SWING_LOOKBACK * 2 + 5:
        return None
    atr_value = atr(bars)
    _, events = structure_events(bars)
    last_close = _float(bars[-1].get("close"))
    return {
        "close": round(last_close, 2) if last_close is not None else None,
        "atr": round(atr_value, 2) if atr_value is not None else None,
        "structure": _structure(bars),
        "premium_discount": _premium_discount(bars),
        "liquidity": _liquidity(bars, atr_value),
        "fair_value_gaps": _fair_value_gaps(bars, atr_value),
        "order_blocks": _order_blocks(bars, events),
        "swings": {
            "highs": [round(p, 2) for _, p in _highs_lows(bars)[0]][-5:],
            "lows": [round(p, 2) for _, p in _highs_lows(bars)[1]][-5:],
        },
    }


# Frames read for the structural story, coarsest last. The lower frames time
# the entry, the higher frames set the bias — the same role split the rest of
# the advisor uses.
TIMEFRAMES = ("M5", "M15", "M30", "H1", "H4")


def build(bars_by_tf: Dict[str, Sequence[dict]],
          timeframes: Sequence[str] = TIMEFRAMES) -> dict:
    """The fact sheet the model reasons over. No opinion is formed here."""
    facts: Dict[str, dict] = {}
    missing: List[str] = []
    for tf in timeframes:
        bars = bars_by_tf.get(tf)
        read = timeframe_facts(bars) if bars else None
        if read is None:
            missing.append(tf)
        else:
            facts[tf] = read

    # Sessions need intraday timestamps; use the finest frame that has them.
    sessions = None
    for tf in timeframes:
        if tf in facts:
            sessions = session_ranges(bars_by_tf[tf])
            if sessions is not None:
                sessions["source_timeframe"] = tf
                break

    return {
        "timeframes": facts,
        "missing_timeframes": missing,
        "sessions": sessions,
        "confluence": _confluence(facts),
        "facts_note": FACTS_NOTE,
    }
