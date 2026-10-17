"""T15 — decision levels: cost tiers, ATR, SL/TP, risk tiers, hit statistics.

Implements the empirical side of `docs/architecture/DECISION_MODEL.md` (Lead
design, Agent-B validation). Pure stdlib, deterministic.

Binding rules exercised here:
  * RULE A — `prob_scaled` TP is deliberately NOT the canonical level; shrinking
    the target as probability rises inflates hit rate without improving
    expectancy. RR is a fixed 2.0 here.
  * RULE B — every decision-grade number is reported under three cost tiers.
  * RULE C — this module computes levels and hit statistics; it publishes no
    probability and takes no calibrated probability as input to sizing.

No market data is present in the container; the demo that exercises this is
synthetic and labelled as such.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

from src.models.costs import CostTier, cost_tiers, tier_by_name  # noqa: F401 (re-exported)
from src.models.splits import SplitError

# The canonical cost tiers live in `src.models.costs` (T20). Re-exported here so
# the T15 level code and its tests share one definition (RULE B).
COST_TIERS = cost_tiers()


def atr(closes: Sequence[float], period: int = 14) -> float:
    """Wilder ATR proxy from closes alone.

    Only closes are available at this layer, so the "true range" is the absolute
    close-to-close move. Documented as a proxy, not the textbook high/low ATR.
    Deterministic: Wilder smoothing with a fixed seed average.
    """
    if period < 1:
        raise SplitError("period must be >= 1")
    if len(closes) < period + 1:
        raise SplitError(
            f"need at least {period + 1} closes for ATR({period}), got {len(closes)}"
        )
    trs = [abs(closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    seed = sum(trs[:period]) / period
    value = seed
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


@dataclass(frozen=True)
class Levels:
    entry: float
    direction: str
    stop_loss: float
    take_profit: float
    reward_risk: float

    def as_dict(self) -> dict:
        return {
            "entry": self.entry,
            "direction": self.direction,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "reward_risk": self.reward_risk,
        }


def suggest_levels(
    entry: float,
    direction: str,
    atr_value: float,
    atr_mult: float = 1.5,
    rr: float = 2.0,
) -> Levels:
    """SL = entry ∓ atr_mult * ATR; TP = entry ± rr * SL distance (RULE A).

    `direction` is UP or DOWN; FLAT has no levels (the model declines to trade).
    """
    if direction not in ("UP", "DOWN"):
        raise SplitError(f"no levels for direction {direction!r}")
    if atr_value <= 0.0:
        raise SplitError("atr_value must be > 0")
    if atr_mult <= 0.0 or rr <= 0.0:
        raise SplitError("atr_mult and rr must be > 0")
    distance = atr_mult * atr_value
    if direction == "UP":
        sl = entry - distance
        tp = entry + rr * distance
    else:
        sl = entry + distance
        tp = entry - rr * distance
    return Levels(entry, direction, sl, tp, rr)


# RULE D tiers -> suggested account risk. Suggestion only; the human decides.
RISK_TIERS: Tuple[Tuple[str, float, float], ...] = (
    ("low", 0.0, 1.0 / 3.0),
    ("medium", 1.0 / 3.0, 2.0 / 3.0),
    ("high", 2.0 / 3.0, 1.0),
)
_RISK_BY_TIER = {"low": 0.25, "medium": 0.5, "high": 1.0}


def suggest_risk_percent(probability: float) -> float:
    """Suggested account risk (%) for a calibrated probability's tier.

    This is a suggestion derived from the probability tier, not a position size;
    no order is ever produced (L3).
    """
    if not (0.0 <= probability <= 1.0):
        raise SplitError(f"probability out of range [0,1]: {probability}")
    for name, low, high in RISK_TIERS:
        if low <= probability < high or (high == 1.0 and probability == 1.0):
            return _RISK_BY_TIER[name]
    raise SplitError("probability fell in no tier")  # unreachable


def simulate_hit(
    closes: Sequence[float],
    entry_index: int,
    direction: str,
    stop_loss: float,
    take_profit: float,
    max_bars: int,
) -> str:
    """Walk forward from `entry_index` and report the first level touched.

    Close-based: only closes are available at this layer, so this cannot see a
    bar's intrabar high/low and does not implement an intrabar both-touch rule.
    It compares each forward close against the levels; because the stop is
    checked before the target at each step, an ambiguous same-close case resolves
    to SL. That is a *conservative* bias, not a true intrabar rule. Returns
    'TP', 'SL', or 'TIMEOUT'.
    """
    if direction not in ("UP", "DOWN"):
        raise SplitError(f"cannot simulate direction {direction!r}")
    if max_bars < 1:
        raise SplitError("max_bars must be >= 1")
    end = min(len(closes), entry_index + 1 + max_bars)
    for i in range(entry_index + 1, end):
        price = closes[i]
        if direction == "UP":
            if price <= stop_loss:
                return "SL"
            if price >= take_profit:
                return "TP"
        else:
            if price >= stop_loss:
                return "SL"
            if price <= take_profit:
                return "TP"
    return "TIMEOUT"


@dataclass(frozen=True)
class HitStats:
    tier: str
    n: int
    tp: int
    sl: int
    timeout: int

    def hit_rate(self) -> float:
        return self.tp / self.n if self.n else 0.0

    def expectancy_r(self) -> float:
        """Expectancy in R units under a fixed RR, ignoring the timeout drift.

        TP pays +RR, SL pays -1. This is the honest RULE A check: a high hit
        rate with RR < 1 can still be negative expectancy.
        """
        if not self.n:
            return 0.0
        return (self.tp * 2.0 - self.sl) / self.n


def summarize_hits(outcomes: Sequence[str], tier_name: str) -> HitStats:
    tp = sum(1 for o in outcomes if o == "TP")
    sl = sum(1 for o in outcomes if o == "SL")
    timeout = sum(1 for o in outcomes if o == "TIMEOUT")
    return HitStats(tier_name, len(outcomes), tp, sl, timeout)


@dataclass(frozen=True)
class TierOutcome:
    tier: str
    n: int
    hit_rate: float
    gross_expectancy_r: float
    cost_r: float
    net_expectancy_r: float
    decision_grade: bool


def compare_costs(
    closes: Sequence[float],
    entry_index: int,
    direction: str,
    levels: Levels,
    max_bars: int,
) -> List[TierOutcome]:
    """One setup's expectancy under every cost tier (RULE B).

    The price outcome is cost-independent (levels are price-based); cost is
    charged once per trade in R units, `round_trip / risk_distance`. Tier 1
    (zero) is reference only and never decision-grade.
    """
    risk_distance = abs(levels.entry - levels.stop_loss)
    if risk_distance <= 0.0:
        raise SplitError("risk distance must be > 0")
    outcome = simulate_hit(
        closes, entry_index, direction,
        levels.stop_loss, levels.take_profit, max_bars,
    )
    stats = summarize_hits([outcome], "single")
    gross = stats.expectancy_r()
    out: List[TierOutcome] = []
    for tier in COST_TIERS:
        cost_r = tier.round_trip / risk_distance
        out.append(
            TierOutcome(
                tier=tier.name,
                n=stats.n,
                hit_rate=stats.hit_rate(),
                gross_expectancy_r=gross,
                cost_r=cost_r,
                net_expectancy_r=gross - cost_r,
                decision_grade=tier.decision_grade,
            )
        )
    return out
