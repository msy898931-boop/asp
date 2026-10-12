"""Faithful replay of the ASTRA decision chain plus honest scoring.

The engine's C++ defaults are mirrored exactly so a trade idea here matches
what the engine would decide on the same bars:

  FeatureEngine    ema fast=20 slow=50, rsi period=14, atr period=14,
                   volatility period=20, minBars=55
  StructureEngine  swing lookback=2, minSwings=2
  RegimeEngine     trendSlope>=0.0008, volatile>=0.012, quiet<=0.0015
  EligibilityEngine allowRanging, !allowVolatile, requireValidStructure
  SignalEngine     rsi long>=52 / short<=48, stop 1.5*ATR, target 2.5*ATR

Exit and accounting rules, kept deliberately conservative:

  * Stop takes precedence over target when a single bar touches both.
  * A horizon (in bars) closes an idea that neither level reached.
  * A direction change closes the live idea as "reversed".
  * Spread is charged on resolution, so a gross winner can be a net loser.
  * The summary reports "insufficient" below MIN_SCORED resolved trades.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# --- engine defaults (must track the C++ configs) --------------------------
EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14
ATR_PERIOD = 14
VOL_PERIOD = 20
MIN_BARS = 55
SWING_LOOKBACK = 2

TREND_SLOPE_THRESHOLD = 0.0008
VOLATILE_THRESHOLD = 0.012
QUIET_THRESHOLD = 0.0015

RSI_LONG = 52.0
RSI_SHORT = 48.0
STOP_ATR_MULT = 1.5
TARGET_ATR_MULT = 2.5

# --- tracker policy defaults ----------------------------------------------
DEFAULT_COST_USD = 0.30      # round-trip spread charged per trade, in USD/oz
DEFAULT_HORIZON_BARS = 32    # M15 * 32 = 8 hours
DEFAULT_COOLDOWN_BARS = 4    # bars to wait after a resolution before re-entering
DEFAULT_MIN_SCORED = 30      # below this, no rate is stated

RR = TARGET_ATR_MULT / STOP_ATR_MULT  # 1.6667


def _ema(values: list[float], period: int) -> float:
    if not values or period <= 0:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    result = values[0]
    for v in values[1:]:
        result = alpha * v + (1.0 - alpha) * result
    return result


def _rsi(closes: list[float], period: int = RSI_PERIOD) -> float:
    if len(closes) < 2 or period <= 0:
        return 50.0
    start = len(closes) - period if len(closes) > period else 1
    gain = loss = 0.0
    for i in range(start, len(closes)):
        change = closes[i] - closes[i - 1]
        if change >= 0.0:
            gain += change
        else:
            loss -= change
    if loss == 0.0:
        return 50.0 if gain == 0.0 else 100.0
    return 100.0 - (100.0 / (1.0 + gain / loss))


def _atr(bars: list[dict], period: int = ATR_PERIOD) -> float:
    if len(bars) < 2 or period <= 0:
        return 0.0
    start = len(bars) - period if len(bars) > period else 1
    total = 0.0
    count = 0
    for i in range(start, len(bars)):
        prev_close = bars[i - 1]["close"]
        tr = max(bars[i]["high"] - bars[i]["low"],
                 abs(bars[i]["high"] - prev_close),
                 abs(bars[i]["low"] - prev_close))
        total += tr
        count += 1
    return total / count if count else 0.0


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _features(bars: list[dict]) -> dict | None:
    if len(bars) < MIN_BARS:
        return None
    closes = [b["close"] for b in bars]
    fast = _ema(closes, EMA_FAST)
    slow = _ema(closes, EMA_SLOW)
    slope = (fast - slow) / slow if slow else 0.0
    atr = _atr(bars)
    if atr <= 0.0 or not math.isfinite(slope):
        return None
    returns: list[float] = []
    start = len(closes) - VOL_PERIOD - 1 if len(closes) > VOL_PERIOD else 0
    for i in range(start + 1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            returns.append(math.log(closes[i] / closes[i - 1]))
    return {"rsi": _rsi(closes), "atr": atr, "slope": slope,
            "vol": _stdev(returns), "close": closes[-1]}


def _structure(bars: list[dict]) -> str:
    lookback = SWING_LOOKBACK
    swings: list[tuple[float, bool]] = []
    for i in range(lookback, len(bars) - lookback):
        window = range(1, lookback + 1)
        is_high = all(bars[i]["high"] > bars[i - j]["high"]
                      and bars[i]["high"] > bars[i + j]["high"] for j in window)
        is_low = all(bars[i]["low"] < bars[i - j]["low"]
                     and bars[i]["low"] < bars[i + j]["low"] for j in window)
        if is_high:
            swings.append((bars[i]["high"], True))
        elif is_low:
            swings.append((bars[i]["low"], False))
    if len(swings) < 2:
        return "INCOMPLETE"
    highs = [s for s in swings if s[1]][-2:]
    lows = [s for s in swings if not s[1]][-2:]
    bull = bear = 0
    if len(highs) == 2:
        if highs[1][0] > highs[0][0]:
            bull += 1
        else:
            bear += 1
    if len(lows) == 2:
        if lows[1][0] > lows[0][0]:
            bull += 1
        else:
            bear += 1
    if bull > bear:
        return "BULLISH"
    if bear > bull:
        return "BEARISH"
    return "RANGE"


def _regime(features: dict, bias: str) -> str:
    if features["vol"] >= VOLATILE_THRESHOLD:
        return "VOLATILE"
    strong = abs(features["slope"]) >= TREND_SLOPE_THRESHOLD
    agrees = ((features["slope"] > 0 and bias == "BULLISH")
              or (features["slope"] < 0 and bias == "BEARISH"))
    if strong and agrees:
        return "TRENDING_UP" if features["slope"] > 0 else "TRENDING_DOWN"
    if features["vol"] <= QUIET_THRESHOLD and not strong:
        return "QUIET"
    return "RANGING"


def _direction(features: dict, bias: str, regime: str) -> str:
    longs = shorts = 0
    if bias == "BULLISH":
        longs += 1
    if bias == "BEARISH":
        shorts += 1
    if regime == "TRENDING_UP":
        longs += 1
    if regime == "TRENDING_DOWN":
        shorts += 1
    if features["rsi"] >= RSI_LONG:
        longs += 1
    if features["rsi"] <= RSI_SHORT:
        shorts += 1
    if longs > shorts and longs >= 2:
        return "LONG"
    if shorts > longs and shorts >= 2:
        return "SHORT"
    return "NONE"


@dataclass
class Signal:
    """One trade idea, and its outcome once price has decided it."""

    index: int
    time: int
    direction: str
    entry: float
    stop: float
    target: float
    bias: str
    regime: str
    rsi: float
    outcome: str = "OPEN"     # OPEN | TARGET | STOP | TIMEOUT
    resolved: bool = False
    r_gross: float | None = None
    r_net: float | None = None
    exit_price: float | None = None
    resolved_time: int | None = None
    _risk: float = field(default=0.0, repr=False)
    _held: int = field(default=0, repr=False)

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "time": self.time,
            "direction": self.direction,
            "entry": round(self.entry, 2),
            "stop": round(self.stop, 2),
            "target": round(self.target, 2),
            "bias": self.bias,
            "regime": self.regime,
            "rsi": round(self.rsi, 1),
            "outcome": self.outcome,
            "resolved": self.resolved,
            "r_gross": None if self.r_gross is None else round(self.r_gross, 3),
            "r_net": None if self.r_net is None else round(self.r_net, 3),
            "exit_price": None if self.exit_price is None else round(self.exit_price, 2),
            "resolved_time": self.resolved_time,
        }


def _levels(direction: str, entry: float, atr: float) -> tuple[float, float]:
    if direction == "LONG":
        return entry - STOP_ATR_MULT * atr, entry + TARGET_ATR_MULT * atr
    return entry + STOP_ATR_MULT * atr, entry - TARGET_ATR_MULT * atr


def _settle(signal: Signal, exit_price: float, time_value: int,
            cost_usd: float) -> None:
    """Stamp an outcome and the gross/net R for an exit at ``exit_price``."""
    risk = abs(signal.entry - signal.stop)
    signal._risk = risk if risk > 0 else 0.0
    signal.resolved = True
    signal.exit_price = exit_price
    signal.resolved_time = time_value
    if signal._risk > 0:
        gross_delta = exit_price - signal.entry
        if signal.direction == "SHORT":
            gross_delta = -gross_delta
        signal.r_gross = gross_delta / signal._risk
        # gross_delta is already directional, so the spread is a flat debit.
        signal.r_net = (gross_delta - cost_usd) / signal._risk


def _advance(signal: Signal, bar: dict, cost_usd: float,
             horizon_bars: int) -> bool:
    """Judge one forward bar against a live signal. True once it settles."""
    if signal.direction == "LONG":
        hit_stop = bar["low"] <= signal.stop
        hit_target = bar["high"] >= signal.target
    else:
        hit_stop = bar["high"] >= signal.stop
        hit_target = bar["low"] <= signal.target
    # Conservative: the stop wins when a single bar touches both.
    if hit_stop:
        signal.outcome = "STOP"
        _settle(signal, signal.stop, bar["time"], cost_usd)
        return True
    if hit_target:
        signal.outcome = "TARGET"
        _settle(signal, signal.target, bar["time"], cost_usd)
        return True
    signal._held += 1
    if signal._held >= horizon_bars:
        signal.outcome = "TIMEOUT"
        _settle(signal, bar["close"], bar["time"], cost_usd)
        return True
    return False


def build_signals(bars: list[dict], cost_usd: float = DEFAULT_COST_USD,
                  horizon_bars: int = DEFAULT_HORIZON_BARS,
                  cooldown_bars: int = DEFAULT_COOLDOWN_BARS) -> list[Signal]:
    """Replay the chain bar by bar, holding at most one idea at a time.

    A live idea suppresses new votes until it settles (stop, target or the
    horizon), then a cooldown bars the immediate re-entry that would otherwise
    record the same trade again at almost the same price. Without both, a trend
    re-votes every bar and a single move is counted many times, which inflates
    any rate built on the count.
    """
    signals: list[Signal] = []
    live: Signal | None = None
    ready_at = -1                  # first bar a new idea may open on

    for i in range(MIN_BARS - 1, len(bars)):
        # 1. Let any live idea meet this bar first.
        if live is not None:
            if _advance(live, bars[i], cost_usd, horizon_bars):
                live = None
                ready_at = i + cooldown_bars
            else:
                continue  # still open: no new idea may take its place
        if i < ready_at:
            continue      # cooling down after the last resolution

        # 2. Read the engine's view of the bars up to and including this one.
        features = _features(bars[:i + 1])
        if features is None:
            continue
        bias = _structure(bars[:i + 1])
        if bias == "INCOMPLETE":
            continue
        regime = _regime(features, bias)
        if regime == "VOLATILE":       # eligibility: volatile not permitted
            continue
        direction = _direction(features, bias, regime)
        if direction == "NONE":
            continue

        # 3. Open exactly one idea at this bar's close.
        stop, target = _levels(direction, features["close"], features["atr"])
        live = Signal(index=i, time=bars[i]["time"], direction=direction,
                      entry=features["close"], stop=stop, target=target,
                      bias=bias, regime=regime, rsi=features["rsi"])
        signals.append(live)

    return signals


def summarize(signals: list[Signal], min_scored: int = DEFAULT_MIN_SCORED) -> dict:
    """Aggregate resolved ideas. States no rate below ``min_scored``."""
    resolved = [s for s in signals if s.resolved]
    scored = [s for s in resolved if s.outcome in ("TARGET", "STOP")]
    wins = [s for s in scored if s.outcome == "TARGET"]
    losses = [s for s in scored if s.outcome == "STOP"]
    timeouts = [s for s in resolved if s.outcome == "TIMEOUT"]

    net_rs = [s.r_net for s in resolved if s.r_net is not None]
    total_net = sum(net_rs) if net_rs else 0.0
    total_gross = sum(s.r_gross for s in resolved if s.r_gross is not None)

    enough = len(scored) >= min_scored
    summary: dict = {
        "total_signals": len(signals),
        "resolved": len(resolved),
        "wins": len(wins),
        "losses": len(losses),
        "timeouts": len(timeouts),
        "open": len([s for s in signals if not s.resolved]),
        "min_scored": min_scored,
        "sufficient": enough,
        "verdict": "SCORED" if enough else "INSUFFICIENT_DATA",
        "total_r_net": round(total_net, 2),
        "total_r_gross": round(total_gross, 2),
    }
    if enough:
        summary["win_rate"] = round(100.0 * len(wins) / len(scored), 1)
        summary["avg_r_net"] = round(total_net / len(resolved), 2) if resolved else 0.0
    else:
        summary["note"] = ("Only %d scored trades (need %d). Not enough to state "
                           "a win rate." % (len(scored), min_scored))
    return summary
