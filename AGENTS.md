# AGENTS.md

Repository-specific knowledge for the ASTRA desktop app (project AURA) fork.

## What this repo is

A **private working copy** of the public upstream `alimazna/asp` (C++ ASTRA/AURA
gold decision-support desktop app). Upstream is **read-only for us**: we only
clone/fetch from it, never push. All our work lives on `main` here.

- Upstream: `https://github.com/alimazna/asp` (public, ~365 commits, active)
- Our repo: `https://github.com/msy898931-boop/asp` (private)
- Our `main` = upstream `main` + our Telegram bot. To resync, fetch upstream and
  rebase/cherry-pick our additions on top (they touch disjoint files today).

## Layout

| Path | Purpose |
|------|---------|
| `src/` | C++ backend (API, runtime, health, governance, telegram gateway) |
| `frontend/qt/` | Qt 6 desktop UI (the ASTRA window) |
| `bridge/mt5_python/` | Loopback JSON bridge to MetaTrader 5 (Python stdlib) |
| `bridge/telegram_bot/` | **Our** Telegram operator bot (Python stdlib) |
| `scripts/bundle.py` | Stages the installable bundle from `packaging/bundle_manifest.json` |
| `src/platform/windows/` | Backend host, path resolution, process supervision |

## Build & test (backend)

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
cd build && ctest          # 19 tests
```

Frontend is Linux/Qt and Windows-only for the packaged `.exe`.

## Telegram bot

`bridge/telegram_bot/` reads the frontend-facing loopback API
(`127.0.0.1:8790/api/v1`) — it never touches runtime internals.

```bash
# backend must run first
export TELEGRAM_BOT_TOKEN=...   # @BotFather token
export TELEGRAM_CHAT_ID=...     # default chat to notify
./bridge/telegram_bot/run_bot.sh          # backend + bot together
python3 -m unittest discover -s bridge/telegram_bot   # 18 tests
```

`aura_backend_host` launches the bot as a **supervised sidecar** automatically
when `TELEGRAM_BOT_TOKEN` is set (mirroring how it launches the MT5 bridge).
Absence of a token is not an error.

Commands: `/status` `/health` `/analysis` `/signal` `/bridge` `/subscribe`
`/unsubscribe` `/help`. Push alerts fire on health/bridge/new-signal
transitions (first reading is primed silently).

## Windows .exe

`.github/workflows/build-windows.yml` builds `frontend/qt` on `windows-latest`
(Qt 6.8.3 + MSVC), runs `windeployqt`, and uploads an `ASTRA-windows` artifact.
It triggers on pushes that touch `frontend/qt/**`, or manually
(`workflow_dispatch`). It builds the **frontend only** — the backend/MT5 bridge
are separate and not bundled by this workflow.

```bash
gh run download --repo msy898931-boop/asp -n ASTRA-windows   # or via API
```

## Notes / gotchas

- The backend build is self-contained; the MT5 bridge is loopback-only by design.
- `HttpClient` in `src/foundation/` deliberately refuses non-loopback hosts, so
  it cannot reach Telegram's HTTPS API — that is why the bot is Python, not C++.
- Telemetry-style posture: everything is shadow-mode; nothing carries execution
  authority, and messages always carry a "not financial advice" disclaimer.
- Re-staging the bundle: `python3 scripts/bundle.py --out dist/ASTRA
  --backend-host build/aura_backend_host` then `--verify`. It prunes
  `__pycache__`/`.pyc` automatically.
