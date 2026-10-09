"""PY-0005 - AURA MT5 bridge service.

A loopback-only JSON HTTP service that AURA's C++ runtime starts, supervises,
and shuts down. It uses only the Python standard library so it can boot,
handshake, and report MT5 unavailability even when MetaTrader5 is missing.

Endpoints (MT5_PYTHON_BRIDGE_V1.md):
    GET /v1/health
    GET /v1/handshake
    GET /v1/symbol
    GET /v1/candles?symbol=XAUUSD&timeframe=M15&count=500&closed_only=true
    GET /v1/candles?tf=M15&limit=500       (ASTRA chart alias; closed-bar only)
    GET /v1/tick?symbol=XAUUSD

Security boundary:
    * binds only to 127.0.0.1 (never 0.0.0.0);
    * validates protocol/schema versions and rejects mismatches;
    * rejects malformed requests and future-dated client timestamps;
    * does not log credentials.

Causality:
    * candle requests default to closed_only=true and the client skips the
      forming bar; the service never treats the current bar as a decision bar.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from mt5_client import Mt5Client
from replay_client import ReplayMt5Client, csv_path_from_env, symbol_from_env
from schemas import (
    ErrorInfo,
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    SOURCE_NAME,
    TIMEFRAMES,
    PRIMARY_OPERATIONAL_TIMEFRAME,
    PRIMARY_STRUCTURAL_TIMEFRAME,
    STATUS_ERROR,
    STATUS_OK,
    ERR_BAD_REQUEST,
    ERR_BRIDGE_PROTOCOL_MISMATCH,
    ERR_BRIDGE_SCHEMA_MISMATCH,
    ERR_MT5_SYMBOL_UNRESOLVED,
    ERR_MT5_TERMINAL_UNAVAILABLE,
    ERR_MARKET_DATA_STALE,
    ERR_INSUFFICIENT_HISTORY,
    ERR_NOT_FOUND,
    ERR_INTERNAL,
    QUALITY_UNKNOWN,
    QUALITY_VALID,
    error_envelope,
    ok_envelope,
)

LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8791

# Tolerance for client clock skew when rejecting future-dated request stamps.
_FUTURE_SKEW_SECONDS = 300

# ASTRA chart route (/v1/candles?tf&limit) bounds and short-TTL response cache.
# The cache stops rapid UI interactions (timeframe switches, pan/zoom) from
# hammering MT5 with identical reads; it is keyed by (symbol, tf, limit).
_CHART_MAX_LIMIT = 1000
_CHART_DEFAULT_LIMIT = 500
_CACHE_TTL_SECONDS = 5.0


class BridgeState:
    """Shared, long-lived service state."""

    def __init__(self, symbol: str = "XAUUSD", client: Optional[Any] = None) -> None:
        self.client = client if client is not None else Mt5Client()
        self.preferred_symbol = symbol
        self.started_at = int(time.time())
        self.last_successful_request: Optional[int] = None
        self.last_error: str = ""
        self.resolved_symbol: str = ""
        self.mt5_ready = False
        # Structured reason the terminal never came ready, surfaced verbatim on
        # data requests so a caller sees MT5_TERMINAL_UNAVAILABLE (or a symbol
        # failure) instead of a generic downstream error.
        self.bootstrap_error_code: str = ""
        self.bootstrap_error_message: str = ""
        # Short-TTL candle cache. Keyed by (symbol, timeframe, limit); value is
        # (expiry_monotonic, payload). Guarded by a lock because the server is
        # threaded. Only successful, non-stale reads are cached.
        self._candle_cache: Dict[Tuple[str, str, int], Tuple[float, Dict[str, Any]]] = {}
        self._candle_cache_lock = threading.Lock()

    def bootstrap(self) -> None:
        """Attempt MT5 init + symbol resolution. Failure is recorded, not fatal."""
        init = self.client.initialize()
        if not init.ok:
            self.mt5_ready = False
            self.bootstrap_error_code = init.error_code
            self.bootstrap_error_message = init.message
            self.last_error = f"{init.error_code}: {init.message}"
            return
        resolved = self.client.resolve_symbol(self.preferred_symbol)
        if not resolved.ok:
            self.mt5_ready = False
            self.bootstrap_error_code = resolved.error_code
            self.bootstrap_error_message = resolved.message
            self.last_error = f"{resolved.error_code}: {resolved.message}"
            return
        self.resolved_symbol = resolved.data.get("symbol", self.preferred_symbol)
        self.mt5_ready = True
        self.bootstrap_error_code = ""
        self.bootstrap_error_message = ""

    def shutdown(self) -> None:
        self.client.shutdown()

    def mark_success(self) -> None:
        self.last_successful_request = int(time.time())

    def mark_error(self, text: str) -> None:
        self.last_error = text

    # -- candle cache -------------------------------------------------------

    def cache_get(self, symbol: str, timeframe: str, limit: int) -> Optional[Dict[str, Any]]:
        """Return a cached chart payload if still within TTL, else None."""
        key = (symbol, timeframe, limit)
        now = time.monotonic()
        with self._candle_cache_lock:
            entry = self._candle_cache.get(key)
            if entry is None:
                return None
            expiry, payload = entry
            if now >= expiry:
                self._candle_cache.pop(key, None)
                return None
            return payload

    def cache_put(self, symbol: str, timeframe: str, limit: int,
                  payload: Dict[str, Any]) -> None:
        key = (symbol, timeframe, limit)
        with self._candle_cache_lock:
            self._candle_cache[key] = (time.monotonic() + _CACHE_TTL_SECONDS, payload)


def _handshake_payload(state: BridgeState) -> Dict[str, Any]:
    return {
        "service": SOURCE_NAME,
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "timeframes": TIMEFRAMES,
        "primary_operational_timeframe": PRIMARY_OPERATIONAL_TIMEFRAME,
        "primary_structural_timeframe": PRIMARY_STRUCTURAL_TIMEFRAME,
        "loopback_only": True,
        "started_at_utc": state.started_at,
        "mt5_ready": state.mt5_ready,
        "resolved_symbol": state.resolved_symbol,
    }


def _health_payload(state: BridgeState) -> Dict[str, Any]:
    health = state.client.health()
    health.update({
        "process_state": "ONLINE",
        "mt5_ready": state.mt5_ready,
        "last_successful_request": state.last_successful_request,
        "last_error": state.last_error,
        "resolved_symbol": state.resolved_symbol,
        "generated_at_utc": int(time.time()),
    })
    return health


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "AURABridge/" + PROTOCOL_VERSION
    protocol_version = "HTTP/1.1"

    # Silence default stderr request logging noise.
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        pass

    # -- helpers ------------------------------------------------------------

    def _state(self) -> BridgeState:
        return self.server.state  # type: ignore[attr-defined]

    def _send_json(self, body: Dict[str, Any], http_status: int = 200) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(http_status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _check_versions(self) -> Optional[Dict[str, Any]]:
        """Reject protocol/schema mismatches. Returns an error envelope if bad."""
        proto = self.headers.get("X-AURA-Protocol")
        schema = self.headers.get("X-AURA-Schema")
        if proto is not None:
            req_major = proto.split(".")[0].strip()
            our_major = PROTOCOL_VERSION.split(".")[0]
            if req_major != our_major:
                return error_envelope(
                    ErrorInfo(code=ERR_BRIDGE_PROTOCOL_MISMATCH,
                              message=f"client protocol {proto} incompatible with {PROTOCOL_VERSION}",
                              recovery="use a compatible protocol version"),
                    generated_at_utc=int(time.time()), source=SOURCE_NAME)
        if schema is not None:
            req_major = schema.split(".")[0].strip()
            our_major = SCHEMA_VERSION.split(".")[0]
            if req_major != our_major:
                return error_envelope(
                    ErrorInfo(code=ERR_BRIDGE_SCHEMA_MISMATCH,
                              message=f"client schema {schema} incompatible with {SCHEMA_VERSION}",
                              recovery="use a compatible schema version"),
                    generated_at_utc=int(time.time()), source=SOURCE_NAME)
        return None

    def _reject_future_stamp(self, query: Dict[str, list]) -> Optional[Dict[str, Any]]:
        raw = (query.get("client_time") or [None])[0]
        if raw is None:
            return None
        try:
            client_time = int(raw)
        except (TypeError, ValueError):
            return error_envelope(
                ErrorInfo(code=ERR_BAD_REQUEST, message="client_time is not an integer",
                          recovery="send client_time as unix seconds"),
                generated_at_utc=int(time.time()), source=SOURCE_NAME)
        if client_time > int(time.time()) + _FUTURE_SKEW_SECONDS:
            return error_envelope(
                ErrorInfo(code=ERR_BAD_REQUEST, message="client_time is future-dated",
                          recovery="synchronize the client clock"),
                generated_at_utc=int(time.time()), source=SOURCE_NAME)
        return None

    def _error(self, info: ErrorInfo, http_status: int = 200) -> None:
        self._send_json(error_envelope(info, generated_at_utc=int(time.time()),
                                       source=SOURCE_NAME), http_status)

    # -- routing ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._route()
        except Exception as exc:  # never leak a stack trace to the client
            self._error(ErrorInfo(code=ERR_INTERNAL, message=str(exc),
                                  recovery="retry or restart the bridge"), 500)

    def _route(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        state = self._state()

        version_error = self._check_versions()
        if version_error is not None:
            self._send_json(version_error, 400)
            return

        if path == "/v1/handshake":
            self._send_json(ok_envelope(_handshake_payload(state),
                                        generated_at_utc=int(time.time()),
                                        source=SOURCE_NAME))
            return

        if path == "/v1/health":
            self._send_json(ok_envelope(_health_payload(state),
                                        generated_at_utc=int(time.time()),
                                        source=SOURCE_NAME,
                                        quality=QUALITY_VALID if state.mt5_ready else QUALITY_UNKNOWN))
            return

        if path == "/v1/symbol":
            self._handle_symbol(state)
            return

        if path == "/v1/candles":
            future = self._reject_future_stamp(query)
            if future is not None:
                self._send_json(future, 400)
                return
            self._handle_candles(state, query)
            return

        if path == "/v1/tick":
            future = self._reject_future_stamp(query)
            if future is not None:
                self._send_json(future, 400)
                return
            self._handle_tick(state, query)
            return

        self._error(ErrorInfo(code=ERR_NOT_FOUND, message=f"unknown path {path}"), 404)

    def _handle_symbol(self, state: BridgeState) -> None:
        if not state.client.available:
            self._error(ErrorInfo(code=ERR_MT5_TERMINAL_UNAVAILABLE,
                                  message="MetaTrader5 package unavailable",
                                  recovery="install the bundled Python runtime"))
            return
        result = state.client.symbol_specification(state.resolved_symbol or state.preferred_symbol)
        if not result.ok:
            state.mark_error(result.message)
            self._error(ErrorInfo(code=result.error_code, message=result.message))
            return
        state.mark_success()
        self._send_json(ok_envelope(result.data, generated_at_utc=int(time.time()),
                                    source=SOURCE_NAME, symbol=state.resolved_symbol))

    def _handle_candles(self, state: BridgeState, query: Dict[str, list]) -> None:
        # ASTRA chart alias: ?tf=<tf>&limit=<n>. Accepts `tf` for `timeframe`
        # and `limit` for `count`, and returns a bar series with a "bars" key
        # so the frontend chart has a direct series source. The canonical
        # params keep their original behaviour.
        if "tf" in query or "limit" in query:
            self._handle_chart_candles(state, query)
            return

        symbol = (query.get("symbol") or [state.resolved_symbol or state.preferred_symbol])[0]
        timeframe = (query.get("timeframe") or [PRIMARY_OPERATIONAL_TIMEFRAME])[0]
        count_raw = (query.get("count") or ["500"])[0]
        closed_raw = (query.get("closed_only") or ["true"])[0].lower()
        min_raw = (query.get("min_count") or [None])[0]

        if timeframe not in TIMEFRAMES:
            self._error(ErrorInfo(code=ERR_BAD_REQUEST,
                                  message=f"unsupported timeframe {timeframe}",
                                  context={"supported": TIMEFRAMES},
                                  recovery="request a canonical timeframe"))
            return
        try:
            count = int(count_raw)
        except ValueError:
            self._error(ErrorInfo(code=ERR_BAD_REQUEST, message="count is not an integer"))
            return
        if count <= 0 or count > 100000:
            self._error(ErrorInfo(code=ERR_BAD_REQUEST,
                                  message="count out of range (1..100000)"))
            return
        closed_only = closed_raw in ("true", "1", "yes")

        min_count: Optional[int] = None
        if min_raw is not None:
            try:
                min_count = int(min_raw)
            except ValueError:
                self._error(ErrorInfo(code=ERR_BAD_REQUEST,
                                      message="min_count is not an integer"))
                return
            if min_count <= 0:
                self._error(ErrorInfo(code=ERR_BAD_REQUEST,
                                      message="min_count must be positive"))
                return

        result = state.client.read_candles(symbol, timeframe, count, closed_only)
        if not result.ok:
            state.mark_error(result.message)
            # If the terminal never came ready, the actionable reason is the
            # bootstrap failure, not the generic downstream data error.
            code = result.error_code
            message = result.message
            if not state.mt5_ready and state.bootstrap_error_code:
                code = state.bootstrap_error_code
                message = state.bootstrap_error_message
            self._error(ErrorInfo(code=code, message=message,
                                  context={"symbol": symbol, "timeframe": timeframe},
                                  recovery="verify MT5 terminal, symbol, and data availability"))
            return

        returned = int(result.data.get("count", 0))
        # Insufficient history: the caller asked for a decision-grade minimum
        # and the closed-bar window is too short. Report it explicitly rather
        # than letting a short window masquerade as a full sample.
        if min_count is not None and returned < min_count:
            state.mark_error("insufficient closed-bar history")
            self._error(ErrorInfo(
                code=ERR_INSUFFICIENT_HISTORY,
                state=QUALITY_UNKNOWN,
                message=(f"{returned} closed bars available but min_count={min_count} "
                         f"required for {symbol} {timeframe}"),
                context={"symbol": symbol, "timeframe": timeframe,
                         "available": returned, "required": min_count},
                recovery="request a shorter min_count or wait for more history"))
            return

        # Staleness: the feed may be alive but not current. Surface it as an
        # explicit STALE quality rather than a silently VALID response.
        freshness = result.data.get("freshness", "UNKNOWN")
        if freshness == "STALE":
            state.mark_error("stale market data")
            self._error(ErrorInfo(
                code=ERR_MARKET_DATA_STALE,
                state=QUALITY_UNKNOWN,
                message=(f"newest closed {timeframe} bar is {result.data.get('age_seconds')}s old"),
                context={"symbol": symbol, "timeframe": timeframe,
                         "newest_closed_time": result.data.get("newest_closed_time"),
                         "age_seconds": result.data.get("age_seconds")},
                recovery="verify the MT5 terminal feed is live and the market is open"))
            return

        state.mark_success()
        self._send_json(ok_envelope(result.data, generated_at_utc=int(time.time()),
                                    source=SOURCE_NAME, broker=state.client.broker,
                                    symbol=symbol, timeframe=timeframe,
                                    quality=QUALITY_VALID))

    def _handle_chart_candles(self, state: BridgeState, query: Dict[str, list]) -> None:
        """ASTRA chart series: GET /v1/candles?tf={tf}&limit={N}.

        Closed-bar only (the forming bar is never returned), so the chart never
        plots a bar the decision chain has not yet accepted. Successful reads
        are served from a 5s cache to absorb rapid UI interactions.
        """
        symbol = (query.get("symbol") or [state.resolved_symbol or state.preferred_symbol])[0]
        timeframe = (query.get("tf") or [PRIMARY_OPERATIONAL_TIMEFRAME])[0]
        limit_raw = (query.get("limit") or [str(_CHART_DEFAULT_LIMIT)])[0]

        if timeframe not in TIMEFRAMES:
            self._error(ErrorInfo(code=ERR_BAD_REQUEST,
                                  message=f"unsupported timeframe {timeframe}",
                                  context={"supported": TIMEFRAMES},
                                  recovery="request a canonical timeframe"), 400)
            return
        try:
            limit = int(limit_raw)
        except (TypeError, ValueError):
            self._error(ErrorInfo(code=ERR_BAD_REQUEST, message="limit is not an integer"), 400)
            return
        if limit < 1 or limit > _CHART_MAX_LIMIT:
            self._error(ErrorInfo(code=ERR_BAD_REQUEST,
                                  message=f"limit out of range (1..{_CHART_MAX_LIMIT})"), 400)
            return

        cached = state.cache_get(symbol, timeframe, limit)
        if cached is not None:
            self._send_json(ok_envelope(cached, generated_at_utc=int(time.time()),
                                        source=SOURCE_NAME, broker=state.client.broker,
                                        symbol=symbol, timeframe=timeframe,
                                        quality=QUALITY_VALID))
            return

        result = state.client.read_candles(symbol, timeframe, limit, closed_only=True)
        if not result.ok:
            state.mark_error(result.message)
            code = result.error_code
            message = result.message
            if not state.mt5_ready and state.bootstrap_error_code:
                code = state.bootstrap_error_code
                message = state.bootstrap_error_message
            # Terminal/symbol unavailability is a dependency outage, not a
            # client error: report 503 so the frontend can distinguish it.
            http_status = 503 if code in (ERR_MT5_TERMINAL_UNAVAILABLE,
                                          ERR_MT5_SYMBOL_UNRESOLVED) else 200
            self._error(ErrorInfo(code=code, message=message,
                                  context={"symbol": symbol, "timeframe": timeframe},
                                  recovery="verify MT5 terminal, symbol, and data availability"),
                        http_status)
            return

        # Staleness is surfaced as an explicit error, never a silent VALID.
        freshness = result.data.get("freshness", "UNKNOWN")
        if freshness == "STALE":
            state.mark_error("stale market data")
            self._error(ErrorInfo(
                code=ERR_MARKET_DATA_STALE,
                state=QUALITY_UNKNOWN,
                message=(f"newest closed {timeframe} bar is {result.data.get('age_seconds')}s old"),
                context={"symbol": symbol, "timeframe": timeframe,
                         "newest_closed_time": result.data.get("newest_closed_time"),
                         "age_seconds": result.data.get("age_seconds")},
                recovery="verify the MT5 terminal feed is live and the market is open"))
            return

        bars = result.data.get("candles", [])
        payload = {
            "bars": bars,
            "timeframe": timeframe,
            "symbol": symbol,
            "count": len(bars),
            "closed_only": True,
            "newest_closed_time": result.data.get("newest_closed_time"),
            "freshness": freshness,
        }
        state.cache_put(symbol, timeframe, limit, payload)
        state.mark_success()
        self._send_json(ok_envelope(payload, generated_at_utc=int(time.time()),
                                    source=SOURCE_NAME, broker=state.client.broker,
                                    symbol=symbol, timeframe=timeframe,
                                    quality=QUALITY_VALID))

    def _handle_tick(self, state: BridgeState, query: Dict[str, list]) -> None:
        symbol = (query.get("symbol") or [state.resolved_symbol or state.preferred_symbol])[0]
        result = state.client.read_tick(symbol)
        if not result.ok:
            state.mark_error(result.message)
            self._error(ErrorInfo(code=result.error_code, message=result.message,
                                  context={"symbol": symbol},
                                  recovery="verify MT5 terminal connectivity"))
            return
        state.mark_success()
        self._send_json(ok_envelope(result.data, generated_at_utc=int(time.time()),
                                    source=SOURCE_NAME, broker=state.client.broker,
                                    symbol=symbol, quality=QUALITY_VALID))


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: Tuple[str, int], state: BridgeState) -> None:
        super().__init__(address, BridgeHandler)
        self.state = state


def create_server(host: str = LOOPBACK_HOST, port: int = DEFAULT_PORT,
                  symbol: str = "XAUUSD", data_source: str = "mt5",
                  replay_csv: str = "") -> BridgeServer:
    if host != LOOPBACK_HOST:
        raise ValueError("bridge must bind to loopback only (127.0.0.1)")
    client: Any = None
    if data_source == "replay":
        csv_path = replay_csv or csv_path_from_env()
        if not csv_path or not os.path.isfile(csv_path):
            raise ValueError(
                f"replay data source needs a readable corpus CSV "
                f"(ASTRA_REPLAY_CSV): {csv_path or '(unset)'}")
        symbol = symbol_from_env() if symbol == "XAUUSD" else symbol
        client = ReplayMt5Client(csv_path, symbol=symbol)
        print(f"[OK] data source: REPLAY (synthetic) from {csv_path}", flush=True)
    state = BridgeState(symbol, client=client)
    state.bootstrap()
    return BridgeServer((host, port), state)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="AURA MT5 loopback bridge service")
    parser.add_argument("--host", default=LOOPBACK_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--data-source", default=os.environ.get("ASTRA_DATA_SOURCE", "mt5"),
                        choices=["mt5", "replay"],
                        help="mt5 (default) or replay (synthetic corpus)")
    args = parser.parse_args(argv)

    try:
        server = create_server(args.host, args.port, args.symbol,
                               data_source=args.data_source)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    print(f"[OK] AURA bridge listening on http://{args.host}:{args.port} "
          f"(protocol {PROTOCOL_VERSION})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.state.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
