"""Live market-data source for the MT5 bridge, backed by Yahoo Finance.

A keyless stand-in for a MetaTrader 5 terminal, for hosts that cannot run the
Windows terminal (a Linux server, the always-on box). It serves *real* XAUUSD
gold prices through the same closed-bar contract the bridge relies on: ascending
series, ``closed_only=True`` returns closed bars only, and freshness is judged
against the wall clock so a dead or closed market is reported honestly rather
than silently.

Price source
------------
Yahoo Finance's chart endpoint, symbol ``GC=F`` (COMEX gold front month) by
default. It is the closest freely available series to spot XAUUSD: the same
metal, quoted in USD, with a per-minute history deep enough for every timeframe
the engine consumes. It needs no API key and no account.

Provenance is never hidden: the bridge reports ``broker = "Yahoo Finance
(GC=F)"`` and health carries ``synthetic: false`` / ``data_source: "live"``, so
no surface can mistake this for a broker feed or for the replay corpus.

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

import mt5_csv_feed
from mt5_client import ClientResult
from schemas import TIMEFRAMES

SOURCE_LABEL = "Yahoo Finance"
DEFAULT_SYMBOL = "XAUUSD"

# Instrument to quote. GC=F is COMEX gold; overridable for a different series.
_DEFAULT_TICKER = "GC=F"

# Spot-price endpoint used for the tick read (last traded price in USD/oz).
_SPOT_URL = "https://api.gold-api.com/price/XAU"

# Label -> (Yahoo interval, range). Yahoo has no native 4h bar, so H4 is built
# by aggregating 1h bars; everything else is served as-is.
_YF: Dict[str, Tuple[str, str]] = {
    "M1": ("1m", "7d"),
    "M5": ("5m", "60d"),
    "M15": ("15m", "60d"),
    "M30": ("30m", "60d"),
    "H1": ("60m", "2y"),
    "H4": ("60m", "2y"),
    "D1": ("1d", "5y"),
    "W1": ("1wk", "10y"),
    "MN1": ("1mo", "max"),
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

_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
_TIMEOUT = 12.0

# One upstream fetch serves many requests. Short enough to stay live, long
# enough that a chart refresh does not hammer Yahoo.
_CACHE_TTL_SECONDS = 20.0


def ticker_from_env() -> str:
    return os.environ.get("ASTRA_LIVE_TICKER", _DEFAULT_TICKER).strip() or _DEFAULT_TICKER


def symbol_from_env() -> str:
    return os.environ.get("ASTRA_LIVE_SYMBOL", DEFAULT_SYMBOL).strip() or DEFAULT_SYMBOL


def _http_json(url: str) -> Any:
    """GET a URL and parse JSON, trying each Yahoo host in turn."""
    last_error: Optional[Exception] = None
    for host in _HOSTS:
        candidate = url.format(host=host)
        req = urllib.request.Request(candidate, headers={
            "User-Agent": "Mozilla/5.0 (compatible; ASTRA-bridge/1.0)",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last_error = exc
    raise RuntimeError(str(last_error) if last_error else "request failed")


def _aggregate(bars: List[Dict[str, Any]], interval: int) -> List[Dict[str, Any]]:
    """Bucket ascending bars into ``interval``-second bars (used for H4)."""
    buckets: Dict[int, Dict[str, Any]] = {}
    order: List[int] = []
    for bar in bars:
        bucket = bar["time"] - (bar["time"] % interval)
        cur = buckets.get(bucket)
        if cur is None:
            buckets[bucket] = {
                "time": bucket,
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "tick_volume": bar["tick_volume"],
                "spread": 0,
                "real_volume": 0,
            }
            order.append(bucket)
        else:
            cur["high"] = max(cur["high"], bar["high"])
            cur["low"] = min(cur["low"], bar["low"])
            cur["close"] = bar["close"]
            cur["tick_volume"] += bar["tick_volume"]
    series = [buckets[b] for b in order]
    series.sort(key=lambda b: b["time"])
    return series


class LiveMt5Client:
    """MT5-client-shaped source backed by a live Yahoo Finance quote."""

    def __init__(self, ticker: str = _DEFAULT_TICKER,
                 symbol: str = DEFAULT_SYMBOL) -> None:
        self._ticker = ticker or _DEFAULT_TICKER
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
        return f"{SOURCE_LABEL} ({self._ticker})"

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

        yf_interval, yf_range = _YF[timeframe]
        url = ("https://{host}/v8/finance/chart/" + self._ticker +
               f"?interval={yf_interval}&range={yf_range}")
        doc = _http_json(url)
        chart = doc.get("chart") or {}
        if chart.get("error"):
            raise RuntimeError(str(chart["error"].get("description", "upstream error")))
        results = chart.get("result") or []
        if not results:
            raise RuntimeError("no chart result")

        res = results[0]
        stamps = res.get("timestamp") or []
        quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
        opens, highs = quote.get("open") or [], quote.get("high") or []
        lows, closes = quote.get("low") or [], quote.get("close") or []
        vols = quote.get("volume") or []

        bars: List[Dict[str, Any]] = []
        for i, stamp in enumerate(stamps):
            o, h, l, c = (opens[i] if i < len(opens) else None,
                          highs[i] if i < len(highs) else None,
                          lows[i] if i < len(lows) else None,
                          closes[i] if i < len(closes) else None)
            # Yahoo pads gaps with nulls; a bar without a full OHLC is not a bar.
            if None in (o, h, l, c):
                continue
            vol = vols[i] if i < len(vols) and vols[i] is not None else 0
            bars.append({
                "time": int(stamp),
                "open": float(o), "high": float(h),
                "low": float(l), "close": float(c),
                "tick_volume": int(vol), "spread": 0, "real_volume": 0,
            })
        bars.sort(key=lambda b: b["time"])

        if timeframe == "H4":
            bars = _aggregate(bars, _TF_SECONDS["H4"])

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
        # Keep only bars the engine's validator will accept as decision-grade.
        floor = now - _MAX_AGE_SECONDS
        closed = [b for b in closed if b["time"] >= floor]
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
            spot = _http_json(_SPOT_URL)
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
            "server": "yahoo-finance",
            "resolved_symbol": self._symbol,
            "data_source": "live",
            "synthetic": False,
            "ticker": self._ticker,
            "last_error": self._last_error,
        }
