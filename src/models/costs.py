"""T20 — the three cost tiers (RULE B). Canonical cost model for decision-grade work.

RULE B: every decision-grade number must be reported under three cost tiers, and
a result computed under the zero-cost tier is a reference only, never
decision-grade.

    1. zero          — reference only; never decision-grade
    2. floor         — spread + commission
    3. conservative  — spread + commission + slippage

Deterministic, pure stdlib. Cost is expressed in price units for a round trip and
is converted to R units by the caller (see `src.models.levels`).

No market data is present in the container; the default XAUUSD assumptions below
are the mission's stated numbers and are configurable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple

from src.models.splits import SplitError

# Default XAUUSD assumptions (price units, round trip).
DEFAULT_SPREAD = 0.30
DEFAULT_COMMISSION = 0.10
DEFAULT_SLIPPAGE = 0.20


@dataclass(frozen=True)
class CostAssumptions:
    """One round-trip cost broken into its components, in price units."""

    spread: float = DEFAULT_SPREAD
    commission: float = DEFAULT_COMMISSION
    slippage: float = DEFAULT_SLIPPAGE

    def validate(self) -> None:
        for name, value in (
            ("spread", self.spread),
            ("commission", self.commission),
            ("slippage", self.slippage),
        ):
            # Reject NaN/inf too: `NaN < 0` is False, so a bare sign check would
            # let a non-finite cost reach a decision-grade number (T14 standard).
            if not math.isfinite(value) or value < 0.0:
                raise SplitError(f"{name} must be finite and >= 0, got {value!r}")

    def floor(self) -> float:
        self.validate()
        return self.spread + self.commission

    def conservative(self) -> float:
        self.validate()
        return self.spread + self.commission + self.slippage


@dataclass(frozen=True)
class CostTier:
    """A named round-trip cost in price units."""

    name: str
    round_trip: float
    decision_grade: bool

    def dead_band(self) -> float:
        """Minimum move that must be cleared to be worth trading (theta)."""
        return self.round_trip

    def cost_r(self, risk_distance: float) -> float:
        """Cost expressed in R units for a trade risking `risk_distance`."""
        if risk_distance <= 0.0:
            raise SplitError("risk_distance must be > 0")
        return self.round_trip / risk_distance


def cost_tiers(assumptions: Optional[CostAssumptions] = None) -> Tuple[CostTier, ...]:
    """The canonical three tiers under the given assumptions (RULE B)."""
    a = assumptions or CostAssumptions()
    a.validate()
    return (
        CostTier("zero", 0.0, False),
        CostTier("floor", a.floor(), True),
        CostTier("conservative", a.conservative(), True),
    )


def tier_by_name(name: str, assumptions: Optional[CostAssumptions] = None) -> CostTier:
    for tier in cost_tiers(assumptions):
        if tier.name == name:
            return tier
    raise SplitError(f"unknown cost tier: {name!r}")


def net_expectancy_r(gross_r: float, cost_r: float) -> float:
    """Net edge after cost. Negative means the setup cannot pay for itself."""
    return gross_r - cost_r
