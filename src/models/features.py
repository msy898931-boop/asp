"""Feature-adapter boundary for the model layer.

Agent-A owns the feature computation (`src/analysis/features/`, C++). This
module does NOT recompute features. It defines the validated, deterministic
Python-side contract that the model layer consumes, so T03 can train on
Agent-A's output without ever touching Agent-A's zone.

The field names, ranges and validity rules mirror
`src/analysis/features/AnalyticalFeatures.h` and `FEATURES.md` exactly. If
Agent-A changes a field, this contract must be updated in the same review
cycle — a silent mismatch is a leakage/honesty risk, so it is validated here
rather than assumed.

Interchange format: a JSON object per decision instant, e.g.

    {
      "asOfBarOpenSec": 1735689600,
      "perTimeframe": [
        {"timeframe": "M15", "valid": true, "quality": "VALID", "values": {...}},
        ...
      ],
      "cross": {"valid": true, "quality": "VALID", "values": {...}}
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Sequence

from src.models.splits import SplitError

# Canonical M1..MN1 order (matches mt5/Mt5BridgeContract.h).
TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1")

# Per-timeframe feature bounds: name -> (low, high). Mirrors FEATURES.md.
PER_TIMEFRAME_BOUNDS: Dict[str, tuple] = {
    "structureTrend": (-1.0, 1.0),
    "rangePosition": (0.0, 1.0),
    "swingAsymmetry": (-1.0, 1.0),
    "bodyRatio": (0.0, 1.0),
    "upperWickRatio": (0.0, 1.0),
    "lowerWickRatio": (0.0, 1.0),
    "runBalance": (-1.0, 1.0),
    "momentumNorm": (-1.0, 1.0),
    "momentumPersistence": (0.0, 1.0),
    "momentumAcceleration": (-1.0, 1.0),
    "volatilityRatio": (0.0, 1.0),
    "atrRatio": (0.0, 1.0),
    "netChangeRatio": (-1.0, 1.0),
    "higherHighShare": (0.0, 1.0),
    "lowerLowShare": (0.0, 1.0),
    "patternScore": (-1.0, 1.0),
    "contextTrend": (-1.0, 1.0),
    "contextVolatility": (0.0, 1.0),
    "contextRangePosition": (0.0, 1.0),
}

# Discrete {-1,0,1} per-timeframe features.
PER_TIMEFRAME_DISCRETE = ("candleDirection",)

# Cross-timeframe feature bounds. Mirrors AnalyticalFeatures.h.
CROSS_BOUNDS: Dict[str, tuple] = {
    "mtfConflictScore": (0.0, 1.0),
    "h4StructuralAuthority": (-1.0, 1.0),
    "m15TriggerState": (-1.0, 1.0),
}
CROSS_DISCRETE = ("h4M15Agreement", "h4D1Agreement")

# DataQualityState vocabulary (src/foundation/DataQualityState.h).
QUALITY_STATES = (
    "VALID",
    "DEGRADED",
    "INVALID",
    "UNKNOWN",
    "STALE",
    "MISSING",
    "OUT_OF_ORDER",
    "DUPLICATE",
    "INCOMPLETE",
)


def _check_bounded(name: str, value: float, low: float, high: float) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SplitError(f"feature {name!r} must be numeric, got {value!r}")
    if not (low - 1e-9 <= value <= high + 1e-9):
        raise SplitError(
            f"feature {name!r}={value} outside declared range [{low}, {high}]"
        )


def _check_discrete(name: str, value: float) -> None:
    if value not in (-1.0, 0.0, 1.0):
        raise SplitError(f"discrete feature {name!r} must be -1, 0 or 1, got {value!r}")


@dataclass(frozen=True)
class FeatureVector:
    """Validated per-timeframe feature vector."""

    timeframe: str
    asOfBarOpenSec: int
    values: Mapping[str, float]
    quality: str
    valid: bool
    detail: str = ""

    def validate(self) -> None:
        if self.timeframe not in TIMEFRAMES:
            raise SplitError(f"unknown timeframe {self.timeframe!r}")
        if self.quality not in QUALITY_STATES:
            raise SplitError(f"unknown quality {self.quality!r}")
        expected = set(PER_TIMEFRAME_BOUNDS) | set(PER_TIMEFRAME_DISCRETE)
        missing = expected - set(self.values)
        if missing:
            raise SplitError(
                f"{self.timeframe}: missing features {sorted(missing)}"
            )
        unknown = set(self.values) - expected
        if unknown:
            raise SplitError(
                f"{self.timeframe}: undeclared features {sorted(unknown)}"
            )
        for name, (low, high) in PER_TIMEFRAME_BOUNDS.items():
            _check_bounded(name, self.values[name], low, high)
        for name in PER_TIMEFRAME_DISCRETE:
            _check_discrete(name, self.values[name])


@dataclass(frozen=True)
class CrossFeatureVector:
    """Validated cross-timeframe feature vector."""

    asOfBarOpenSec: int
    values: Mapping[str, float]
    quality: str
    valid: bool
    m15Available: bool
    h4Available: bool
    d1Available: bool = False
    detail: str = ""

    def validate(self) -> None:
        if self.quality not in QUALITY_STATES:
            raise SplitError(f"unknown quality {self.quality!r}")
        expected = set(CROSS_BOUNDS) | set(CROSS_DISCRETE)
        missing = expected - set(self.values)
        if missing:
            raise SplitError(f"cross: missing features {sorted(missing)}")
        unknown = set(self.values) - expected
        if unknown:
            raise SplitError(f"cross: undeclared features {sorted(unknown)}")
        for name, (low, high) in CROSS_BOUNDS.items():
            _check_bounded(name, self.values[name], low, high)
        for name in CROSS_DISCRETE:
            _check_discrete(name, self.values[name])


@dataclass(frozen=True)
class FeatureSet:
    """One validated decision instant: per-timeframe vectors + cross vector."""

    asOfBarOpenSec: int
    perTimeframe: Sequence[FeatureVector] = field(default_factory=tuple)
    cross: Optional[CrossFeatureVector] = None
    quality: str = "UNKNOWN"
    valid: bool = False

    def validate(self) -> None:
        if self.quality not in QUALITY_STATES:
            raise SplitError(f"unknown quality {self.quality!r}")
        names = [v.timeframe for v in self.perTimeframe]
        if len(names) != len(set(names)):
            raise SplitError(f"duplicate timeframe in feature set: {names}")
        order = [TIMEFRAMES.index(n) for n in names]
        if order != sorted(order):
            raise SplitError("per-timeframe vectors must be in canonical M1..MN1 order")
        for vector in self.perTimeframe:
            vector.validate()
        if self.cross is not None:
            self.cross.validate()

        # One decision instant, enforced. Agent-D's T01 audit (F1/F2) showed the
        # hazard of computing each stream at its own tail: a "multi-timeframe"
        # set whose members describe different instants is not a single snapshot
        # and is non-causal. Every vector here must share the set's decision bar.
        for vector in self.perTimeframe:
            if vector.asOfBarOpenSec != self.asOfBarOpenSec:
                raise SplitError(
                    f"{vector.timeframe} asOfBarOpenSec {vector.asOfBarOpenSec} "
                    f"!= feature set asOfBarOpenSec {self.asOfBarOpenSec} "
                    "(no common decision instant)"
                )
        if self.cross is not None and self.cross.asOfBarOpenSec != self.asOfBarOpenSec:
            raise SplitError(
                f"cross asOfBarOpenSec {self.cross.asOfBarOpenSec} "
                f"!= feature set asOfBarOpenSec {self.asOfBarOpenSec} "
                "(no common decision instant)"
            )

    def timeframe(self, name: str) -> FeatureVector:
        for vector in self.perTimeframe:
            if vector.timeframe == name:
                return vector
        raise SplitError(f"timeframe {name!r} not present in this feature set")

    def as_flat_row(self) -> Dict[str, float]:
        """Flatten to a single deterministic feature row keyed '<TF>.<feature>'.

        Used by the model layer as the training matrix row. Key order is
        canonical so two runs produce identical columns.
        """
        row: Dict[str, float] = {}
        for vector in sorted(self.perTimeframe, key=lambda v: TIMEFRAMES.index(v.timeframe)):
            for name in sorted(vector.values):
                row[f"{vector.timeframe}.{name}"] = float(vector.values[name])
        if self.cross is not None:
            for name in sorted(self.cross.values):
                row[f"cross.{name}"] = float(self.cross.values[name])
        return row


def parse_feature_set(raw: Mapping) -> FeatureSet:
    """Build and validate a FeatureSet from a decoded JSON object."""
    per = []
    for entry in raw.get("perTimeframe", []):
        per.append(
            FeatureVector(
                timeframe=entry["timeframe"],
                asOfBarOpenSec=int(entry["asOfBarOpenSec"]),
                values={k: float(v) for k, v in entry["values"].items()},
                quality=entry["quality"],
                valid=bool(entry["valid"]),
                detail=entry.get("detail", ""),
            )
        )
    cross_raw = raw.get("cross")
    cross = None
    if cross_raw is not None:
        cross = CrossFeatureVector(
            asOfBarOpenSec=int(cross_raw["asOfBarOpenSec"]),
            values={k: float(v) for k, v in cross_raw["values"].items()},
            quality=cross_raw["quality"],
            valid=bool(cross_raw["valid"]),
            m15Available=bool(cross_raw.get("m15Available", False)),
            h4Available=bool(cross_raw.get("h4Available", False)),
            d1Available=bool(cross_raw.get("d1Available", False)),
            detail=cross_raw.get("detail", ""),
        )
    result = FeatureSet(
        asOfBarOpenSec=int(raw["asOfBarOpenSec"]),
        perTimeframe=tuple(per),
        cross=cross,
        quality=raw.get("quality", "UNKNOWN"),
        valid=bool(raw.get("valid", False)),
    )
    result.validate()
    return result


def parse_feature_set_json(text: str) -> FeatureSet:
    return parse_feature_set(json.loads(text))
