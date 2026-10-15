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

_All links are published by an AI agent (OpenHands) on behalf of @msy898931-boop._

