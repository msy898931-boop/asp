# AURA / ASTRA — Backend-First Repository Build Pack

## Identity
- Technical project: **AURA**
- Product / desktop brand: **ASTRA**
- Asset: **XAUUSD only**
- Primary operating mode: **SHADOW**
- Backend implementation agent: **DeepSeek v4.1 Flash via OpenHands**
- Frontend implementation agent: **Alpha**, after backend review and approval

## What this pack does
This repository pack defines the new clean build of AURA. It is designed to be extracted and pushed to a new GitHub repository before the first OpenHands run.

The active architecture in V4 changes the market-data acquisition boundary from MQL5 adapters to a **bundled Python + MetaTrader5 bridge process** that AURA launches and supervises locally. The C++ runtime remains the core decision/runtime engine. The bridge is part of the application distribution but remains a separate child process.

## Required execution order
1. DeepSeek completes the backend using `TASK-BOOTSTRAP-BACKEND-000.md` and `TASK_MANIFEST.yaml`.
2. Backend is independently reviewed, built, tested, and accepted.
3. Only after that, Alpha receives the frontend handoff and builds the ASTRA desktop interface.

## Non-goals for the backend-first pass
- No frontend UI implementation.
- No live/real order execution.
- No replacement of the evaluator by candidate code.
- No MQL5 EA-based candle ingestion.

## Startup target
A final packaged ASTRA application must start by double-click without requiring CMD. The application must resolve its paths from its installation/executable location, start the bundled Python bridge automatically, supervise its health, and expose actionable startup errors instead of depending on the developer shell.

## Where things are
This repository is a fork of `alimazna/asp` (never push upstream — see
`docs/FORK_UPSTREAM_DECISION.md`). For a map of the docs, current versus
historical, see **`docs/INDEX.md`**. Build/test/ship instructions live in
`AGENTS.md`.
