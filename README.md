# AURA / ASTRA

## Identity
- Technical project: **AURA**
- Product brand: **ASTRA**
- Asset: **XAUUSD only**
- Operating mode: **SHADOW** (signals and alerts; no live order execution)

## What it is

A shadow-mode gold decision tool. A C++ backend supervises a Python market-data
bridge and a Telegram bot and serves a loopback API; a bilingual (English /
العربية) phone PWA is the shipping surface. It deploys as one container — the
phone app reverse-proxies the API and the bridge.

| Part | Where | Role |
| --- | --- | --- |
| Decision/runtime engine | `src/` | C++ core |
| Market data + bot | `bridge/`, `bridge/telegram_bot/` | live (gold-api.com + Kraken PAXGUSD) or replay/MT5 |
| Phone app | `mobile/` | the PWA; the shipping surface |
| News statistics | `akoan/` | the news engine and its verdicts |
| Advisor | `advisor/` | an external second-opinion model |
| Desktop UI | `frontend/qt/` | separate build; not shipped in the container image |

## Non-goals
- No live/real order execution.
- No MQL5 EA-based candle ingestion.

## Where things are

This repository is a fork of `alimazna/asp` — **never push upstream** (see
`docs/FORK_UPSTREAM_DECISION.md`). For a map of the docs, current versus
historical, see **`docs/INDEX.md`**. Build, test, and ship instructions live in
`AGENTS.md`.

## Get it

| Surface | Link |
| --- | --- |
| Phone PWA (live) | https://astra-xlp9.onrender.com/ |
| Android app (`.apk`) | https://github.com/msy898931-boop/asp/releases/download/android/astra.apk |
| Telegram bot | https://t.me/Uhxsuigf_bot |
| Windows desktop (full bundle) | https://github.com/msy898931-boop/asp/releases/download/desktop/ASTRA-full.zip |
| Windows desktop (UI only) | https://github.com/msy898931-boop/asp/releases/download/desktop/ASTRA-windows.zip |

The two phone downloads are the same build: the PWA installs from the browser,
the `.apk` installs from the file. On first launch the `.apk` asks for the
server address; enter the live PWA URL. The desktop bundles are a separate Qt
build and are not part of the container image.

## Access

The live app is **keyed**. The backend sets `ASTRA_ACCESS_TOKEN` and the phone
app shows a one-field unlock screen until the key is entered; the key is then
kept in the `astra_key` cookie. A first visit without a key gets **HTTP 401** —
that is the gate working, not a broken deploy. Ask the operator for the key.

The gate is a single static key: enough to keep casual visitors out, not a real
auth system (no expiry, no per-user accounts, no rate limit). Treat the live URL
as private.

## Tests

Measured on this fork (2026-10-16):

| Suite | Tests |
| --- | --- |
| `bot/` | 152 |
| `tracker/` | 44 |
| `research/` | 50 |
| `akoan/` | 59 |
| `advisor/` | 92 |
| `mobile/` | 119 |
| `bridge/telegram_bot/` | 91 |
| `tests/` (models, integration, features) | 308 |
| **Total (Python)** | **915** |

Plus 19 C++ test binaries (`ctest`). Run a suite with
`python3 -m unittest discover -s <suite> -p "test_*.py"`. Every Python suite
also runs in CI, so the figures above are re-measured on each push.

_All links are published by an AI agent (OpenHands) on behalf of @msy898931-boop._

