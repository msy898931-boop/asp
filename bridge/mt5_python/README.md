# AURA MT5 Python Bridge

PY-0006 — bundled market-data acquisition boundary for AURA/ASTRA.

## Role

```text
Broker / Valetax
   -> MT5 Terminal (user-installed, logged in)
   -> this bridge (Python + official MetaTrader5 package)
   -> 127.0.0.1 loopback JSON API
   -> AURA C++ PythonBridgeClient -> ingestion -> validation -> decision chain
```

The bridge is a **separate child process** managed by AURA. It is shipped with
the application; end users never run Python or CMD manually.

## Files

| File | Purpose |
|---|---|
| `schemas.py` | Wire vocabulary, versioned envelope, structured error codes |
| `mt5_client.py` | All MetaTrader5 contact; reports unavailability instead of faking data |
| `replay_client.py` | Optional synthetic source backed by the committed CSV corpus |
| `live_client.py` | Optional live source backed by Yahoo Finance (keyless) |
| `read_candles.py` | Nine canonical timeframe streams, closed-bar only |
| `bridge_service.py` | Loopback JSON HTTP service AURA starts and supervises |
| `requirements.txt` | Runtime dependencies (MetaTrader5, numpy) |

## Data sources

`--data-source` (or `ASTRA_DATA_SOURCE`) selects where candles come from:

| Value | Source | Use |
|---|---|---|
| `mt5` (default) | A live MetaTrader 5 terminal | The product path |
| `replay` | The committed M1 CSV corpus, synthetic | Tests / demos with no terminal |
| `live` | Yahoo Finance `GC=F` (COMEX gold), keyless | An always-on host with no Windows terminal |

The `live` source serves real gold prices over the same closed-bar contract as a
terminal, so the engine cannot tell the difference in shape. It labels itself
honestly: health carries `synthetic: false` and `data_source: "live"`, and the
broker string is `Yahoo Finance (GC=F)`. Set `ASTRA_LIVE_TICKER` to quote a
different series. Because it is a public quote feed, not a broker, it is not a
trading path — it exists to keep the pipeline fed with real prices 24/7.

## Running

Development only (packaged runtime starts this automatically):

```text
python bridge_service.py --host 127.0.0.1 --port 8791 --symbol XAUUSD
python bridge_service.py --data-source live            # Yahoo Finance
python bridge_service.py --data-source replay          # committed CSV corpus
```

## Endpoints

```text
GET /v1/health
GET /v1/handshake
GET /v1/symbol
GET /v1/candles?symbol=XAUUSD&timeframe=M15&count=500&closed_only=true
GET /v1/candles?tf=M15&limit=500
GET /v1/tick?symbol=XAUUSD
```

`/v1/candles?tf={tf}&limit={N}` is the ASTRA chart series alias. It accepts
`tf` for `timeframe` and `limit` for `count` (1..1000, default 500) and returns
`{bars, timeframe, symbol, count, newest_closed_time, freshness}` — a direct
series for the chart, closed-bar only. Validation failures return HTTP 400;
MT5/symbol unavailability returns HTTP 503. A successful read is cached for 5s
per `(symbol, tf, limit)` so rapid UI interactions do not hammer MT5.

`/v1/candles` also accepts two optional decision-grade guards:

- `min_count=N` — fail with `INSUFFICIENT_HISTORY` when fewer than `N` closed
  bars are available, so a short window never masquerades as a full sample.
- freshness — the response carries `freshness` (`FRESH|STALE|UNKNOWN`),
  `newest_closed_time`, and `age_seconds`. A feed whose newest closed bar is
  older than three nominal bar intervals is rejected with `MARKET_DATA_STALE`.

## Guarantees

- **Loopback only.** `create_server` rejects any host other than `127.0.0.1`.
- **Closed-bar causality.** Candle retrieval skips the forming bar
  (`start_pos=1`); the current bar is never a decision bar.
- **No fabrication.** If MT5 is unavailable the bridge returns a structured
  error (`MT5_TERMINAL_UNAVAILABLE`) and never invents candles. The `replay` and
  `live` sources are explicit opt-ins, each labelled with its own provenance.
- **Plausibility-bounded history.** The `live` source trims history to the
  engine's 400-day bar-plausibility window, so deep daily/weekly/monthly series
  are never handed over as bars the validator would reject.
- **Structured errors.** `MT5_TERMINAL_UNAVAILABLE`, `MT5_SYMBOL_UNRESOLVED`,
  `MARKET_DATA_MISSING`, `MARKET_DATA_INVALID`, `MARKET_DATA_STALE`,
  `INSUFFICIENT_HISTORY`, `BRIDGE_PROTOCOL_MISMATCH`, `BRIDGE_SCHEMA_MISMATCH`,
  `BAD_REQUEST`, `NOT_FOUND`, `INTERNAL_ERROR` — each with code/state/message/
  context/recovery.
- **Versioned.** Every response carries `protocol_version` and
  `schema_version`; mismatched client versions are rejected.
- **Explicit symbol resolution.** Exact `XAUUSD`, then `XAUUSD*`, then any
  `XAU*`; the resolved name is recorded and reported.
- **Graceful degradation.** The service imports and handshakes even when the
  Windows-only MetaTrader5 package is absent, so health is always observable.

## Testing

`tests/integration/test_bridge_t06.py` boots the real service with a stub
MetaTrader5 module (`tests/integration/fake_mt5/`) and exercises the closed-bar,
freshness, error, version, bind, and SIGTERM contracts without a broker:

```text
python3 tests/integration/test_bridge_t06.py
```

The ASTRA candle-series alias has its own unit test, which injects an
in-process MetaTrader5 double and drives the real route over loopback HTTP
(shape, closed-bar, validation, cache, unavailability):

```text
python3 bridge/tests/test_candles_route.py
```

## Environment

- Windows 10/11 with MT5 Terminal installed, open, and logged in.
- Python 3.8–3.11 (MetaTrader5 does not support 3.12+).
- The packaged application supplies a private runtime; a system Python is not
  a product prerequisite.
