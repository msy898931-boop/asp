"""Live market-data source for the MT5 bridge.

A keyless stand-in for a MetaTrader 5 terminal, for hosts that cannot run the
Windows terminal (a Linux server, the always-on box). It serves *real* gold
prices through the same closed-bar contract the bridge relies on: ascending
series, ``closed_only=True`` returns closed bars only, and freshness is judged
against the wall clock so a dead or closed market is reported honestly rather
than silently.

Two upstreams, both keyless
---------------------------
* **Spot / last price** - ``api.gold-api.com`` (``/price/XAU``). Free, no rate
  limit, refreshed every few seconds; this is the price shown for a tick.
* **OHLC candles** - Kraken's public OHLC feed on ``PAXGUSD``. PAXG is a
  gold-backed token that tracks one troy ounce of LBMA gold, so its USD market
  is a close, always-open proxy for spot XAUUSD: every timeframe the engine
  consumes (M1..MN1) is available with no key and no market-hours gap.

Kraken is not spot XAUUSD - it is a gold-backed market, and the two quotes can
differ by a fraction of a percent. Provenance is never hidden: the bridge
reports ``broker = "gold-api.com + Kraken (PAXGUSD)"`` and health carries
``synthetic: false`` / ``data_source: "live"``, so no surface can mistake this
for a broker feed or for the replay corpus.

Enable with ``ASTRA_DATA_SOURCE=live`` (or ``bridge_service --data-source
live``). The default stays ``mt5``; this module only runs when asked.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from mt5_client import ClientResult
from schemas import TIMEFRAMES

SOURCE_LABEL = "gold-api.com + Kraken"
DEFAULT_SYMBOL = "XAUUSD"

# Spot quote for the tick read: gold-api symbol (XAU = gold, USD/oz).
_DEFAULT_SPOT_SYMBOL = "XAU"
_SPOT_URL = "https://api.gold-api.com/price/{symbol}"

# Kraken OHLC pair used for candles. PAXGUSD is gold-backed and trades 24/7.
_DEFAULT_CANDLE_PAIR = "PAXGUSD"
_KRAKEN_URL = "https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval}"

# Timeframe -> Kraken interval in minutes. Kraken has a native 4h bar, so no
# client-side aggregation is needed.
_TF_MINUTES: Dict[str, int] = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60,
    "H4": 240, "D1": 1440, "W1": 10080, "MN1": 21600,
}

_TF_SECONDS: Dict[str, int] = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600,
    "H4": 14400, "D1": 86400, "W1": 604800, "MN1": 2592000,
}

# Mirrors mt5_client._STALE_INTERVAL_MULTIPLIER: the newest closed bar may be a
# few nominal intervals old before the feed is called STALE.
_STALE_MULTIPLIER = 3

# The engine's bar validator rejects any bar opening more than 400 days before
# now as IMPLAUSIBLE_TIME. Deep history is normal for daily/weekly/monthly bars,
# so a naive 500-bar window on D1/W1/MN1 would hand the engine bars it then
# refuses, degrading those timeframes. Trim history to just inside that limit so
# every served bar stays decision-grade.
_MAX_AGE_SECONDS = 399 * 24 * 60 * 60

_TIMEOUT = 12.0

# One upstream fetch serves many requests. Short enough to stay live, long
# enough that a chart refresh does not hammer the upstream.
_CACHE_TTL_SECONDS = 20.0


def spot_symbol_from_env() -> str:
    return (os.environ.get("ASTRA_LIVE_SPOT_SYMBOL", _DEFAULT_SPOT_SYMBOL).strip()
            or _DEFAULT_SPOT_SYMBOL)


def candle_pair_from_env() -> str:
    return (os.environ.get("ASTRA_LIVE_CANDLE_PAIR", _DEFAULT_CANDLE_PAIR).strip()
            or _DEFAULT_CANDLE_PAIR)


def symbol_from_env() -> str:
    return os.environ.get("ASTRA_LIVE_SYMBOL", DEFAULT_SYMBOL).strip() or DEFAULT_SYMBOL


def _http_json(url: str) -> Any:
    """GET a URL and parse JSON."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; ASTRA-bridge/1.0)",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RuntimeError(str(exc)) from exc


class LiveMt5Client:
    """MT5-client-shaped source backed by a live gold spot + candle feed."""

    def __init__(self, spot_symbol: str = _DEFAULT_SPOT_SYMBOL,
                 candle_pair: str = _DEFAULT_CANDLE_PAIR,
                 symbol: str = DEFAULT_SYMBOL) -> None:
        self._spot_symbol = spot_symbol or _DEFAULT_SPOT_SYMBOL
        self._candle_pair = candle_pair or _DEFAULT_CANDLE_PAIR
        self._symbol = symbol or DEFAULT_SYMBOL
        self._initialized = False
        self._last_error = ""
        self._cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
        self._lock = threading.Lock()

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
        return f"{SOURCE_LABEL} ({self._candle_pair})"

    # -- lifecycle ----------------------------------------------------------

    def initialize(self) -> ClientResult:
        """Prove the upstream is reachable before declaring the feed ready."""
        try:
            self._fetch("M1")
        except RuntimeError as exc:
            self._last_error = str(exc)
            return ClientResult(ok=False, error_code="MARKET_DATA_MISSING",
                                message=f"live feed unreachable: {exc}")
        self._initialized = True
        return ClientResult(ok=True)

    def shutdown(self) -> None:
        self._initialized = False
        with self._lock:
            self._cache.clear()

    def resolve_symbol(self, preferred: str = DEFAULT_SYMBOL) -> ClientResult:
        self._symbol = preferred or DEFAULT_SYMBOL
        return ClientResult(ok=True, data={"symbol": self._symbol,
                                           "candidates": [self._symbol]})

    # -- market data --------------------------------------------------------

    def _fetch(self, timeframe: str) -> List[Dict[str, Any]]:
        """Return ascending bars for a timeframe, cached briefly."""
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(timeframe)
            if hit is not None and hit[0] > now:
                return [dict(b) for b in hit[1]]

        url = _KRAKEN_URL.format(pair=self._candle_pair,
                                 interval=_TF_MINUTES[timeframe])
        doc = _http_json(url)
        errors = doc.get("error") or []
        if errors:
            raise RuntimeError(f"upstream error: {errors}")
        result = doc.get("result") or {}
        rows: Optional[List[Any]] = None
        for key, value in result.items():
            if key != "last" and isinstance(value, list):
                rows = value
                break
        if not rows:
            raise RuntimeError("no OHLC result")

        bars: List[Dict[str, Any]] = []
        for row in rows:
            # [time, open, high, low, close, vwap, volume, count]
            if len(row) < 8:
                continue
            try:
                bars.append({
                    "time": int(row[0]),
                    "open": float(row[1]), "high": float(row[2]),
                    "low": float(row[3]), "close": float(row[4]),
                    "tick_volume": int(float(row[6])), "spread": 0, "real_volume": 0,
                })
            except (TypeError, ValueError):
                continue
        bars.sort(key=lambda b: b["time"])

        # Keep only bars the engine's validator will accept as decision-grade.
        floor = int(time.time()) - _MAX_AGE_SECONDS
        bars = [b for b in bars if b["time"] >= floor]

        with self._lock:
            self._cache[timeframe] = (time.monotonic() + _CACHE_TTL_SECONDS, bars)
        return [dict(b) for b in bars]

    def read_candles(self, symbol: str, timeframe: str, count: int,
                     closed_only: bool = True) -> ClientResult:
        if timeframe not in TIMEFRAMES:
            return ClientResult(ok=False, error_code="MARKET_DATA_INVALID",
                                message=f"unknown timeframe {timeframe}")
        if count <= 0:
            return ClientResult(ok=False, error_code="MARKET_DATA_INVALID",
                                message="count must be positive")
        try:
            bars = self._fetch(timeframe)
        except RuntimeError as exc:
            self._last_error = str(exc)
            return ClientResult(ok=False, error_code="MARKET_DATA_MISSING",
                                message=f"live feed unavailable: {exc}")
        if not bars:
            return ClientResult(ok=False, error_code="MARKET_DATA_MISSING",
                                message=f"no live candles for {symbol} {timeframe}")

        interval = _TF_SECONDS[timeframe]
        now = int(time.time())
        # A bar is closed once its own interval has elapsed. The newest bar is
        # normally still forming, so closed_only drops it - the same guarantee
        # `start_pos=1` gives against a live terminal.
        closed = [b for b in bars if b["time"] + interval <= now]
        series = closed if closed_only else bars
        if not series:
            return ClientResult(ok=False, error_code="MARKET_DATA_MISSING",
                                message=f"no closed live candles for {symbol} {timeframe}")

        candles = series[-count:]
        newest_closed = candles[-1]["time"]
        age_seconds = now - newest_closed
        if age_seconds > interval * _STALE_MULTIPLIER:
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
        """Spot last price; falls back to the newest M1 close if spot is down."""
        price = 0.0
        stamp = int(time.time())
        try:
            spot = _http_json(_SPOT_URL.format(symbol=self._spot_symbol))
            price = float(spot.get("price", 0.0))
        except (RuntimeError, TypeError, ValueError):
            pass
        if price <= 0.0:
            try:
                bars = self._fetch("M1")
                price = bars[-1]["close"] if bars else 0.0
            except RuntimeError as exc:
                self._last_error = str(exc)
                return ClientResult(ok=False, error_code="MARKET_DATA_MISSING",
                                    message=f"live tick unavailable: {exc}")
        return ClientResult(ok=True, data={
            "symbol": symbol,
            "time": stamp,
            "bid": price, "ask": price, "last": price,
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
            "broker": self.broker,
            "server": "gold-api.com+kraken",
            "resolved_symbol": self._symbol,
            "data_source": "live",
            "synthetic": False,
            "spot_symbol": self._spot_symbol,
            "candle_pair": self._candle_pair,
            "last_error": self._last_error,
        }
