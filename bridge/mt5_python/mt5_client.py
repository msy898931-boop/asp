"""PY-0003 - MT5 client wrapper.

Owns all contact with the MetaTrader5 package. If the package is missing (for
example on a non-Windows machine) the client still constructs and reports
`available = False` with an explicit reason; it never fabricates market data.

Causality: candle retrieval always skips the currently forming bar using
`start_pos=1` semantics, so decision-grade data is closed-bar only.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from schemas import TIMEFRAMES

# Nominal bar duration in seconds per canonical timeframe. Used only to judge
# whether the newest closed bar is stale; MN1 is a 30-day approximation.
_TIMEFRAME_SECONDS = {
    "M1": 60,
    "M5": 5 * 60,
    "M15": 15 * 60,
    "M30": 30 * 60,
    "H1": 60 * 60,
    "H4": 4 * 60 * 60,
    "D1": 24 * 60 * 60,
    "W1": 7 * 24 * 60 * 60,
    "MN1": 30 * 24 * 60 * 60,
}

# A stream is STALE when the newest closed bar is older than this multiple of
# the timeframe's nominal duration. The multiplier tolerates weekends and short
# market closures without masking a genuinely dead feed.
_STALE_INTERVAL_MULTIPLIER = 3

try:  # The package is Windows-only; absence must be reportable, not fatal.
    import MetaTrader5 as _mt5
    _MT5_IMPORT_ERROR = ""
except Exception as exc:  # noqa: BLE001 - pragma: no cover - depends on host platform
    _mt5 = None
    _MT5_IMPORT_ERROR = str(exc)


# MT5 timeframe constants, resolved lazily against the imported module. The
# canonical labels map to attributes on the MetaTrader5 module.
_TIMEFRAME_ATTR = {
    "M1": "TIMEFRAME_M1",
    "M5": "TIMEFRAME_M5",
    "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1",
    "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
    "W1": "TIMEFRAME_W1",
    "MN1": "TIMEFRAME_MN1",
}


@dataclass
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float
    tick_volume: int = 0
    spread: int = 0
    real_volume: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "time": int(self.time),
            "open": float(self.open),
            "high": float(self.high),
            "low": float(self.low),
            "close": float(self.close),
            "tick_volume": int(self.tick_volume),
            "spread": int(self.spread),
            "real_volume": int(self.real_volume),
        }


@dataclass
class ClientResult:
    """Result of a client operation; explicit success flag, never fabricated."""

    ok: bool
    data: Dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    message: str = ""


class Mt5Client:
    def __init__(self) -> None:
        self._initialized = False
        self._resolved_symbol = ""
        self._broker = ""
        self._server = ""
        self._terminal_version: Optional[tuple] = None

    # -- availability -------------------------------------------------------

    @property
    def available(self) -> bool:
        return _mt5 is not None

    @property
    def import_error(self) -> str:
        return _MT5_IMPORT_ERROR

    @property
    def resolved_symbol(self) -> str:
        return self._resolved_symbol

    @property
    def broker(self) -> str:
        return self._broker

    # -- lifecycle ----------------------------------------------------------

    def initialize(self) -> ClientResult:
        if _mt5 is None:
            return ClientResult(
                ok=False,
                error_code="MT5_TERMINAL_UNAVAILABLE",
                message=f"MetaTrader5 package unavailable: {_MT5_IMPORT_ERROR}",
            )
        try:
            if not _mt5.initialize():
                return ClientResult(
                    ok=False,
                    error_code="MT5_TERMINAL_UNAVAILABLE",
                    message=f"mt5.initialize failed: {_mt5.last_error()}",
                )
        except Exception as exc:  # noqa: BLE001 - pragma: no cover
            return ClientResult(ok=False, error_code="MT5_TERMINAL_UNAVAILABLE", message=str(exc))

        self._initialized = True
        with contextlib.suppress(Exception):  # version is optional
            self._terminal_version = tuple(_mt5.version()) if _mt5.version() else None
        with contextlib.suppress(Exception):  # account info is optional
            acc = _mt5.account_info()
            if acc is not None:
                self._broker = getattr(acc, "company", "") or ""
                self._server = getattr(acc, "server", "") or ""
        return ClientResult(ok=True)

    def shutdown(self) -> None:
        if _mt5 is not None and self._initialized:
            with contextlib.suppress(Exception):  # best-effort teardown
                _mt5.shutdown()
        self._initialized = False

    # -- symbol resolution --------------------------------------------------

    def resolve_symbol(self, preferred: str = "XAUUSD") -> ClientResult:
        """Resolve the broker's XAUUSD representation.

        Preference order (mirrors the reference guide):
          1. exact `XAUUSD`
          2. any name beginning with `XAUUSD` (e.g. XAUUSD.vx)
          3. first symbol containing `XAU`
        The resolved name is recorded; we never pretend the bare name exists.
        """
        if _mt5 is None:
            return ClientResult(ok=False, error_code="MT5_TERMINAL_UNAVAILABLE",
                                message="MetaTrader5 package unavailable")
        try:
            symbols = _mt5.symbols_get()
        except Exception as exc:  # noqa: BLE001
            return ClientResult(ok=False, error_code="MT5_SYMBOL_UNRESOLVED", message=str(exc))
        if symbols is None:
            return ClientResult(ok=False, error_code="MT5_SYMBOL_UNRESOLVED",
                                message="symbols_get returned None")

        names = [s.name for s in symbols if "XAU" in s.name.upper()]
        if not names:
            return ClientResult(ok=False, error_code="MT5_SYMBOL_UNRESOLVED",
                                message="no XAU symbol available on this broker")

        chosen = ""
        for name in names:
            if name.upper() == preferred.upper():
                chosen = name
                break
        if not chosen:
            for name in names:
                if name.upper().startswith(preferred.upper()):
                    chosen = name
                    break
        if not chosen:
            chosen = names[0]

        with contextlib.suppress(Exception):  # symbol_select is best-effort
            _mt5.symbol_select(chosen, True)
        self._resolved_symbol = chosen
        return ClientResult(ok=True, data={"symbol": chosen, "candidates": names})

    # -- market data --------------------------------------------------------

    def _timeframe_constant(self, label: str) -> Optional[int]:
        attr = _TIMEFRAME_ATTR.get(label)
        if attr is None or _mt5 is None:
            return None
        return getattr(_mt5, attr, None)

    def read_candles(self, symbol: str, timeframe: str, count: int,
                     closed_only: bool = True) -> ClientResult:
        """Read candles. With closed_only=True the forming bar is skipped.

        `start_pos=1` is the closed-bar guarantee: MT5 index 0 is the current
        forming bar and must never be used as a decision bar.
        """
        if _mt5 is None:
            return ClientResult(ok=False, error_code="MT5_TERMINAL_UNAVAILABLE",
                                message="MetaTrader5 package unavailable")
        if timeframe not in TIMEFRAMES:
            return ClientResult(ok=False, error_code="MARKET_DATA_INVALID",
                                message=f"unknown timeframe {timeframe}")
        if count <= 0:
            return ClientResult(ok=False, error_code="MARKET_DATA_INVALID",
                                message="count must be positive")

        tf_const = self._timeframe_constant(timeframe)
        if tf_const is None:
            return ClientResult(ok=False, error_code="MARKET_DATA_INVALID",
                                message=f"timeframe constant missing for {timeframe}")

        start_pos = 1 if closed_only else 0
        try:
            rates = _mt5.copy_rates_from_pos(symbol, tf_const, start_pos, count)
        except Exception as exc:  # noqa: BLE001
            return ClientResult(ok=False, error_code="MARKET_DATA_MISSING", message=str(exc))
        if rates is None or len(rates) == 0:
            return ClientResult(ok=False, error_code="MARKET_DATA_MISSING",
                                message=f"no candles for {symbol} {timeframe}")

        names = getattr(getattr(rates, "dtype", None), "names", None) or ()
        candles: List[Candle] = []
        for r in rates:
            candles.append(Candle(
                time=int(r["time"]),
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                tick_volume=int(r["tick_volume"]) if "tick_volume" in names else 0,
                spread=int(r["spread"]) if "spread" in names else 0,
                real_volume=int(r["real_volume"]) if "real_volume" in names else 0,
            ))

        # Staleness: the newest *closed* bar should not be older than a few
        # nominal bar intervals. Without a clock we cannot judge, so we report
        # UNKNOWN rather than pretending the feed is fresh.
        now = int(time.time())
        interval = _TIMEFRAME_SECONDS.get(timeframe, 0)
        newest_closed = candles[-1].time
        age_seconds = now - newest_closed
        if interval <= 0:
            freshness = "UNKNOWN"
        elif age_seconds > interval * _STALE_INTERVAL_MULTIPLIER:
            freshness = "STALE"
        else:
            freshness = "FRESH"

        return ClientResult(ok=True, data={
            "symbol": symbol,
            "timeframe": timeframe,
            "closed_only": closed_only,
            "count": len(candles),
            "newest_closed_time": newest_closed,
            "age_seconds": age_seconds,
            "freshness": freshness,
            "candles": [c.to_dict() for c in candles],
        })

    def read_tick(self, symbol: str) -> ClientResult:
        if _mt5 is None:
            return ClientResult(ok=False, error_code="MT5_TERMINAL_UNAVAILABLE",
                                message="MetaTrader5 package unavailable")
        try:
            tick = _mt5.symbol_info_tick(symbol)
        except Exception as exc:  # noqa: BLE001
            return ClientResult(ok=False, error_code="MARKET_DATA_MISSING", message=str(exc))
        if tick is None:
            return ClientResult(ok=False, error_code="MARKET_DATA_MISSING",
                                message=f"no tick for {symbol}")
        return ClientResult(ok=True, data={
            "symbol": symbol,
            "time": int(getattr(tick, "time", 0)),
            "bid": float(getattr(tick, "bid", 0.0)),
            "ask": float(getattr(tick, "ask", 0.0)),
            "last": float(getattr(tick, "last", 0.0)),
            "volume": float(getattr(tick, "volume", 0.0)),
            "flags": int(getattr(tick, "flags", 0)),
        })

    def symbol_specification(self, symbol: str) -> ClientResult:
        if _mt5 is None:
            return ClientResult(ok=False, error_code="MT5_TERMINAL_UNAVAILABLE",
                                message="MetaTrader5 package unavailable")
        try:
            info = _mt5.symbol_info(symbol)
        except Exception as exc:  # noqa: BLE001
            return ClientResult(ok=False, error_code="MT5_SYMBOL_UNRESOLVED", message=str(exc))
        if info is None:
            return ClientResult(ok=False, error_code="MT5_SYMBOL_UNRESOLVED",
                                message=f"no symbol_info for {symbol}")
        fields = ["name", "digits", "point", "trade_tick_size", "trade_tick_value",
                  "trade_contract_size", "volume_min", "volume_max", "volume_step",
                  "trade_stops_level", "trade_freeze_level", "trade_mode",
                  "filling_mode", "margin_mode"]
        data: Dict[str, Any] = {}
        for f in fields:
            if hasattr(info, f):
                data[f] = getattr(info, f)
        return ClientResult(ok=True, data=data)

    # -- identity / health --------------------------------------------------

    def health(self) -> Dict[str, Any]:
        return {
            "package_available": self.available,
            "import_error": self.import_error,
            "initialized": self._initialized,
            "terminal_version": list(self._terminal_version) if self._terminal_version else None,
            "broker": self._broker,
            "server": self._server,
            "resolved_symbol": self._resolved_symbol,
        }
