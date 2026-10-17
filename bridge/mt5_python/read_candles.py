"""PY-0004 - Canonical timeframe candle reader.

Builds on mt5_client to read the nine canonical timeframe streams with
closed-bar semantics. Every stream reports an explicit quality state; a stream
that could not be read is MISSING/UNKNOWN, never silently empty-and-VALID.

Usable as a module (`read_all_timeframes`) and as a diagnostic CLI.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from mt5_client import Mt5Client
from schemas import (
    QUALITY_MISSING,
    QUALITY_UNKNOWN,
    QUALITY_VALID,
    TIMEFRAMES,
)

DEFAULT_CANDLE_COUNT = 500


@dataclass
class TimeframeRead:
    label: str
    quality: str = QUALITY_UNKNOWN
    count: int = 0
    candles: List[Dict[str, Any]] = field(default_factory=list)
    last_time: Optional[int] = None
    last_close: Optional[float] = None
    error_code: str = ""
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "quality": self.quality,
            "count": self.count,
            "last_time": self.last_time,
            "last_close": self.last_close,
            "error_code": self.error_code,
            "message": self.message,
            "candles": self.candles,
        }


def read_timeframe(client: Mt5Client, symbol: str, label: str,
                   count: int = DEFAULT_CANDLE_COUNT) -> TimeframeRead:
    result = client.read_candles(symbol, label, count, closed_only=True)
    if not result.ok:
        return TimeframeRead(
            label=label,
            quality=QUALITY_MISSING,
            error_code=result.error_code,
            message=result.message,
        )
    candles = result.data.get("candles", [])
    if not candles:
        return TimeframeRead(label=label, quality=QUALITY_MISSING,
                             message="bridge returned zero closed candles")
    last = candles[-1]
    return TimeframeRead(
        label=label,
        quality=QUALITY_VALID,
        count=len(candles),
        candles=candles,
        last_time=int(last["time"]),
        last_close=float(last["close"]),
    )


def read_all_timeframes(client: Mt5Client, symbol: str,
                        count: int = DEFAULT_CANDLE_COUNT) -> Dict[str, TimeframeRead]:
    """Read all nine streams. Never aborts on one stream's failure."""
    out: Dict[str, TimeframeRead] = {}
    for label in TIMEFRAMES:
        out[label] = read_timeframe(client, symbol, label, count)
    return out


def _cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="AURA MT5 bridge candle reader")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--count", type=int, default=DEFAULT_CANDLE_COUNT)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    client = Mt5Client()
    init = client.initialize()
    if not init.ok:
        print(f"[ERROR] {init.error_code}: {init.message}", file=sys.stderr)
        return 2

    resolved = client.resolve_symbol(args.symbol)
    symbol = resolved.data.get("symbol", args.symbol) if resolved.ok else args.symbol

    reads = read_all_timeframes(client, symbol, args.count)
    if args.json:
        print(json.dumps({label: r.to_dict() for label, r in reads.items()}, indent=2))
    else:
        for label, r in reads.items():
            print(f"{label:<4} {r.quality:<8} {r.count:>5}  {r.message}")

    client.shutdown()
    # Exit non-zero if the primary operational timeframe (M15) failed.
    return 0 if reads.get("M15", TimeframeRead("M15")).quality == QUALITY_VALID else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
