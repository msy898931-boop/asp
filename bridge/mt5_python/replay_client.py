"""Synthetic/replay market-data source for the MT5 bridge.

An optional stand-in for a live MetaTrader 5 terminal. When enabled, the bridge
serves the committed canonical XAUUSD M1 corpus
(``research/data/xauusd_m1/xauusd_m1_real.csv``) through the *same* closed-bar
contract a live terminal would: ascending series, index 0 is the forming bar,
``start_pos=1`` returns closed bars only.

It exists so the whole pipeline (bridge -> runtime -> API -> bot) can be
exercised end to end on a machine with no Windows terminal and no broker. It is
explicitly NOT a broker feed, and every surface labels it as synthetic: the
bridge reports ``broker = "REPLAY (synthetic)"`` and health carries
``synthetic: true`` / ``data_source: "replay"``.

Enable with ``ASTRA_DATA_SOURCE=replay`` (or ``bridge_service --data-source
replay``). Default is ``mt5``, where a missing terminal is still reported
honestly as unavailable — this module never fabricates data unless asked.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

import mt5_csv_feed
from mt5_client import ClientResult
from schemas import TIMEFRAMES

SOURCE_LABEL = "REPLAY (synthetic)"
DEFAULT_SYMBOL = "XAUUSD"

# Label -> MT5 timeframe constant (values mirror the real package's shape).
_TF_CONST: Dict[str, int] = {
    "M1": mt5_csv_feed.TIMEFRAME_M1,
    "M5": mt5_csv_feed.TIMEFRAME_M5,
    "M15": mt5_csv_feed.TIMEFRAME_M15,
    "M30": mt5_csv_feed.TIMEFRAME_M30,
    "H1": mt5_csv_feed.TIMEFRAME_H1,
    "H4": mt5_csv_feed.TIMEFRAME_H4,
    "D1": mt5_csv_feed.TIMEFRAME_D1,
    "W1": mt5_csv_feed.TIMEFRAME_W1,
    "MN1": mt5_csv_feed.TIMEFRAME_MN1,
}

_TF_SECONDS: Dict[str, int] = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600,
    "H4": 14400, "D1": 86400, "W1": 604800, "MN1": 2592000,
}

# Mirrors mt5_client._STALE_INTERVAL_MULTIPLIER so freshness is judged the same
# way a live feed would be judged.
_STALE_MULTIPLIER = 3


def csv_path_from_env() -> str:
    return os.environ.get("ASTRA_REPLAY_CSV", "").strip()


def symbol_from_env() -> str:
    return os.environ.get("ASTRA_REPLAY_SYMBOL", DEFAULT_SYMBOL).strip() or DEFAULT_SYMBOL


class ReplayMt5Client:
    """MT5-client-shaped source backed by a canonical M1 CSV corpus."""

    def __init__(self, csv_path: str, symbol: str = DEFAULT_SYMBOL) -> None:
        self._csv_path = csv_path
        self._symbol = symbol or DEFAULT_SYMBOL
        self._initialized = False
        self._load_error = ""
        try:
            self._feed: Optional[Any] = mt5_csv_feed.feed_for(csv_path, symbol=self._symbol)
        except OSError as exc:
            # A missing/unreadable corpus is reported by initialize(), not raised
            # at construction, so the bridge can surface it like any other
            # bootstrap failure instead of crashing on startup.
            self._feed = None
            self._load_error = str(exc)

    # -- availability -------------------------------------------------------

    @property
    def available(self) -> bool:
        return True

    @property
    def import_error(self) -> str:
        return ""

    @property
    def resolved_symbol(self) -> str:
        return self._symbol

    @property
    def broker(self) -> str:
        return SOURCE_LABEL

    # -- lifecycle ----------------------------------------------------------

    def initialize(self) -> ClientResult:
        if self._feed is None:
            return ClientResult(
                ok=False, error_code="MARKET_DATA_MISSING",
                message=f"replay corpus unreadable: {self._load_error}")
        if self._feed.total_bars() <= 0:
            return ClientResult(
                ok=False, error_code="MARKET_DATA_MISSING",
                message=f"replay corpus empty or unreadable: {self._csv_path}")
        self._initialized = True
        return ClientResult(ok=True)

    def shutdown(self) -> None:
        self._initialized = False

    def resolve_symbol(self, preferred: str = DEFAULT_SYMBOL) -> ClientResult:
        self._symbol = preferred or DEFAULT_SYMBOL
        return ClientResult(ok=True, data={"symbol": self._symbol,
                                           "candidates": [self._symbol]})

    # -- market data --------------------------------------------------------

    def _corpus_now(self) -> int:
        """The replay timeline's "now": its newest M1 bar.

        A fixed corpus does not advance, so judging freshness against the wall
        clock would mark a perfectly complete corpus STALE after a few minutes.
        For a synthetic feed, internal completeness is the meaningful check, so
        age is measured from the corpus's own end.
        """
        if self._feed is None:
            return int(time.time())
        bars = self._feed.copy_rates_from_pos(
            self._symbol, mt5_csv_feed.TIMEFRAME_M1, 0, 1)
        return int(bars[-1]["time"]) if bars else int(time.time())

    def read_candles(self, symbol: str, timeframe: str, count: int,
                     closed_only: bool = True) -> ClientResult:
        if timeframe not in TIMEFRAMES:
            return ClientResult(ok=False, error_code="MARKET_DATA_INVALID",
                                message=f"unknown timeframe {timeframe}")
        if count <= 0:
            return ClientResult(ok=False, error_code="MARKET_DATA_INVALID",
                                message="count must be positive")
        if self._feed is None:
            return ClientResult(ok=False, error_code="MARKET_DATA_MISSING",
                                message=f"replay corpus unreadable: {self._load_error}")

        start_pos = 1 if closed_only else 0
        bars = self._feed.copy_rates_from_pos(
            symbol, _TF_CONST[timeframe], start_pos, count)
        if not bars:
            return ClientResult(ok=False, error_code="MARKET_DATA_MISSING",
                                message=f"no replay candles for {symbol} {timeframe}")

        candles = [dict(b) for b in bars]
        now = self._corpus_now()
        interval = _TF_SECONDS.get(timeframe, 0)
        newest_closed = candles[-1]["time"]
        age_seconds = now - newest_closed
        if interval <= 0:
            freshness = "UNKNOWN"
        elif age_seconds > interval * _STALE_MULTIPLIER:
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
            "candles": candles,
        })

    def read_tick(self, symbol: str) -> ClientResult:
        close = self._feed.last_close() if self._feed is not None else 0.0
        return ClientResult(ok=True, data={
            "symbol": symbol,
            "time": int(time.time()),
            "bid": close, "ask": close, "last": close,
            "volume": 0.0, "flags": 6,
        })

    def symbol_specification(self, symbol: str) -> ClientResult:
        return ClientResult(ok=True, data={
            "name": symbol, "digits": 2, "point": 0.01,
            "trade_tick_size": 0.01, "trade_tick_value": 1.0,
            "trade_contract_size": 100.0, "volume_min": 0.01,
            "volume_max": 100.0, "volume_step": 0.01,
            "trade_stops_level": 0, "trade_freeze_level": 0,
            "trade_mode": 4, "filling_mode": 1, "margin_mode": 2,
        })

    # -- identity / health --------------------------------------------------

    def health(self) -> Dict[str, Any]:
        return {
            "package_available": True,
            "import_error": "",
            "initialized": self._initialized,
            "terminal_version": None,
            "broker": SOURCE_LABEL,
            "server": "replay",
            "resolved_symbol": self._symbol,
            "data_source": "replay",
            "synthetic": True,
            "corpus": self._csv_path,
            "corpus_bars": self._feed.total_bars() if self._feed is not None else 0,
        }
