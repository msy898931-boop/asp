"""PY-0002 - AURA MT5 bridge wire schemas.

Single source of truth for the loopback JSON protocol shared with the C++
`PythonBridgeClient`. Keeping the vocabulary here (rather than scattered
literals) is what lets both sides stay version-compatible.

Standard library only: this module must import even when MetaTrader5 is absent.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# --- Protocol / schema identity -------------------------------------------

PROTOCOL_VERSION = "1.0"
SCHEMA_VERSION = "1.0"
SOURCE_NAME = "mt5-python-bridge"

# Canonical nine timeframe streams. Order is authoritative: index maps to the
# MT5 timeframe constant in mt5_client.py.
TIMEFRAMES: List[str] = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]

# Timeframe authority (V4-04). M15 is the primary operational/setup timeframe
# and H4 is the primary structural authority.
PRIMARY_OPERATIONAL_TIMEFRAME = "M15"
PRIMARY_STRUCTURAL_TIMEFRAME = "H4"

# --- Quality / status vocabulary ------------------------------------------

QUALITY_VALID = "VALID"
QUALITY_DEGRADED = "DEGRADED"
QUALITY_INVALID = "INVALID"
QUALITY_UNKNOWN = "UNKNOWN"
QUALITY_STALE = "STALE"
QUALITY_MISSING = "MISSING"
QUALITY_OUT_OF_ORDER = "OUT_OF_ORDER"
QUALITY_DUPLICATE = "DUPLICATE"
QUALITY_INCOMPLETE = "INCOMPLETE"

STATUS_OK = "OK"
STATUS_ERROR = "ERROR"

# Structured error codes. These mirror foundation/ErrorCode.h where they
# overlap so the C++ ingestion layer can map them without guessing.
ERR_NONE = "NONE"
ERR_UNKNOWN = "UNKNOWN_ERROR"
ERR_BRIDGE_UNAVAILABLE = "BRIDGE_UNAVAILABLE"
ERR_BRIDGE_PROTOCOL_MISMATCH = "BRIDGE_PROTOCOL_MISMATCH"
ERR_BRIDGE_SCHEMA_MISMATCH = "BRIDGE_SCHEMA_MISMATCH"
ERR_MT5_TERMINAL_UNAVAILABLE = "MT5_TERMINAL_UNAVAILABLE"
ERR_MT5_SYMBOL_UNRESOLVED = "MT5_SYMBOL_UNRESOLVED"
ERR_MARKET_DATA_MISSING = "MARKET_DATA_MISSING"
ERR_MARKET_DATA_INVALID = "MARKET_DATA_INVALID"
ERR_MARKET_DATA_STALE = "MARKET_DATA_STALE"
ERR_MARKET_DATA_INCOMPLETE = "MARKET_DATA_INCOMPLETE"
ERR_INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
ERR_BAD_REQUEST = "BAD_REQUEST"
ERR_NOT_FOUND = "NOT_FOUND"
ERR_INTERNAL = "INTERNAL_ERROR"

# --- Response envelope -----------------------------------------------------


@dataclass
class ErrorInfo:
    """Structured, actionable error payload."""

    code: str = ERR_NONE
    state: str = QUALITY_UNKNOWN
    message: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    recovery: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Envelope:
    """Versioned response envelope (MT5_PYTHON_BRIDGE_V1.md)."""

    status: str = STATUS_OK
    request_id: str = ""
    generated_at_utc: int = 0
    broker: str = ""
    symbol: str = ""
    timeframe: str = ""
    quality: str = QUALITY_UNKNOWN
    payload: Dict[str, Any] = field(default_factory=dict)
    error: Optional[ErrorInfo] = None
    protocol_version: str = PROTOCOL_VERSION
    schema_version: str = SCHEMA_VERSION
    source: str = SOURCE_NAME

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "protocol_version": self.protocol_version,
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "status": self.status,
            "source": self.source,
            "generated_at_utc": self.generated_at_utc,
            "broker": self.broker,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "quality": self.quality,
            "payload": self.payload,
        }
        if self.error is not None:
            out["error"] = self.error.to_dict()
        return out


def ok_envelope(payload: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
    quality = kwargs.pop("quality", QUALITY_VALID)
    env = Envelope(status=STATUS_OK, payload=payload, quality=quality, **kwargs)
    return env.to_dict()


def error_envelope(error: ErrorInfo, **kwargs: Any) -> Dict[str, Any]:
    quality = kwargs.pop("quality", QUALITY_UNKNOWN)
    env = Envelope(status=STATUS_ERROR, error=error, quality=quality, **kwargs)
    return env.to_dict()


def is_valid_timeframe(label: str) -> bool:
    return label in TIMEFRAMES
