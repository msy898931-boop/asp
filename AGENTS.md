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
(Qt 6.8.3 + MSVC), runs `windeployqt`, and publishes a rolling `desktop`
release on `main` (mirrors the Android APK's rolling release; artifacts alone
expire after 30 days). Two assets:

- `ASTRA-windows.zip` — the Qt UI alone (~24 MB).
- `ASTRA-full.zip` — UI + backend host + embedded Python, runs standalone
  (~64 MB): unzip and launch `astra_desktop.exe`.

Permanent link: `https://github.com/msy898931-boop/asp/releases/download/desktop/ASTRA-full.zip`.
It triggers on pushes that touch `frontend/qt/**`, or manually
(`workflow_dispatch`).

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

## Always-on server (Docker)

For a 24/7 bot (no PC needed) the repo ships a headless image. One container
runs **everything**: the backend host (supervising the MT5 bridge and the
Telegram bot) and the phone app (PWA) on the public port. That means the bot
and the phone app deploy as **one service on one port** — see `DEPLOY.md`.

```bash
cp .env.example .env      # set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
docker compose up -d --build
docker compose logs -f    # expect: "[entry] serving phone app" then bot online
```

`docker-compose.yml` sets `restart: unless-stopped`, publishes the phone app on
`8790:8080`, and keeps the API/bridge on loopback. Subscribers persist in the
`astra-state` volume. For a managed host (no VPS to babysit), use Fly.io
(`fly.toml`) or Render (`render.yaml`) — both read the same Dockerfile. The
`deploy-fly.yml` workflow deploys on demand once `FLY_API_TOKEN` is set.

Run exactly one instance per bot token: two pollers make Telegram answer
`409 Conflict`.

The image defaults to the **live** source (`ASTRA_DATA_SOURCE=live`), so the
always-on host serves real gold prices with no Windows terminal and no API key.
Set `ASTRA_DATA_SOURCE=replay` for an offline synthetic demo.

Notes: the container must reach `api.telegram.org` (default egress is fine),
`api.gold-api.com`, and `api.kraken.com`; the loopback API/bridge are never
exposed publicly.

### Synthetic data source

Without a Windows terminal the bridge reports MT5 as unavailable and the system
stays DEGRADED (honest, by design). To exercise the whole pipeline anyway, the
bridge accepts a **replay** source that serves the committed XAUUSD M1 corpus
through the same closed-bar contract:

```bash
ASTRA_DATA_SOURCE=replay \
ASTRA_REPLAY_CSV=/app/research/data/xauusd_m1/xauusd_m1_real.csv
```

`bridge/mt5_python/replay_client.py` implements it; the bridge reports
`broker = "REPLAY (synthetic)"`, so the source is never mistaken for a live
broker. Default remains `mt5`. This is a development/demo feed, not market data.
It also shifts the corpus's timestamps onto the current minute, so replayed
prices are historical values wearing today's clock — useful for shape, not for
a current price.

### Live data source (no terminal needed)

For an always-on host with no Windows terminal, the bridge accepts a **live**
source backed by gold-api.com (spot) + Kraken (candles). It is keyless, serves
real gold prices over the same closed-bar contract as a terminal, and labels
itself honestly (`synthetic: false`, `data_source: live`, `broker = "gold-api.com
+ Kraken (PAXGUSD)"`):

```bash
ASTRA_DATA_SOURCE=live
ASTRA_LIVE_CANDLE_PAIR=PAXGUSD   # optional; any Kraken pair
ASTRA_LIVE_SPOT_SYMBOL=XAU       # optional; any gold-api symbol
```

`bridge/mt5_python/live_client.py` implements it. The tick read comes from
gold-api.com's free, unthrottled `/price/XAU`; the OHLC candles come from
Kraken's public `PAXGUSD` market, a gold-backed token that tracks one troy ounce
and trades 24/7, so every timeframe is available without a market-hours gap.
History is trimmed to the engine's 400-day bar-plausibility window so
daily/weekly/monthly series are not rejected as `IMPLAUSIBLE_TIME`. This is a
public quote feed, not a broker — not a trading path.

### Mobile app (PWA)

`mobile/` is an installable phone app (no store, no build step): an
instrument-panel shell that reads the same loopback API through its own origin.
`mobile/serve_mobile.py` serves the shell and reverse-proxies `/api/v1/*` and
`/bridge/*` to 127.0.0.1 (GET only, upstream fixed to loopback). Run it on a
public port; icons are generated with `mobile/icons/make_icons.py` (stdlib only).
Install on the phone via the browser's "Add to Home screen".

### Android APK

`android/` wraps the same web app in a WebView shell to produce a real,
installable `.apk` (see `android/README.md`). The build is Gradle-free: a JDK
and Android SDK build-tools suffice. The server URL is baked at build time and
can be changed on the device from the app's error screen.

### Windows bundle (desktop)

`.github/workflows/build-windows.yml` produces two artifacts:

* `ASTRA-windows.zip` — the Qt UI alone (needs a backend elsewhere).
* `ASTRA-full-windows.zip` — **the runnable desktop app**: `astra_desktop.exe`
  plus `aura_backend_host.exe`, a private embedded Python under
  `resources/python/`, the bridge, and the operator bot. Unzip, run
  `astra_desktop.exe`; the host finds `resources/` beside itself.

`scripts/embed_python.py` downloads a python-build-standalone Windows build and
lays it down as `resources/python/python.exe` (no Python on the target, no MSI —
the bridge and bot are stdlib-only). `scripts/bundle.py` stages the bridge/bot
and creates `config/`, `data/`, `logs/`.

The packaged host now **defaults to the live source** (`--data-source` /
`ASTRA_DATA_SOURCE` default `"live"`); pass `--data-source mt5` to use a local
MetaTrader5 terminal.

Two Windows-only compile traps found and fixed (CI never built the backend host
before, so the old `.exe` was UI-only):

* the root `CMakeLists.txt` forced `-Wall -Wextra`, which cl rejects (`D8021`);
  MSVC now gets `/W4` with `_CRT_SECURE_NO_WARNINGS NOMINMAX WIN32_LEAN_AND_MEAN`.
* `windows.h` -> `wingdi.h` defines `ERROR` as `0`, so `ServiceState::ERROR`
  became `ServiceState::0` (`C2589`/`C2062`); `ProcessSupervisor.cpp` now
  `#undef ERROR` after the include.

Telegram bot: verified end-to-end against the live feed — `/status`, `/health`,
`/analysis`, `/bridge` all answer correctly (ONLINE/SHADOW/READY, symbol XAUUSD,
source gold-api.com + Kraken PAXGUSD).

### Mobile app (PWA + Android)

`mobile/` is the phone app; `android/` is a WebView shell around it (no Gradle).
Two things to remember:

* The APK asks for the server address on **first launch** (no baked-in default
  that only works on the build machine) and can be re-pointed from the error
  screen. `serve_mobile.py` answers `/astra.apk` with a 302 to the rolling
  `android` release when no local build is staged.
* The repo is **private**, so GitHub release download URLs 404 for anyone
  unauthenticated. The reliable in-product distribution is the mobile server
  staging the APK locally (`--apk`), or handing the file to the operator
  directly. The rolling `android` release is a convenience, not a public CDN.

The app header shows a live price ticker (newest closed close + change vs the
prior close) from the same loopback bridge candles as the chart.

## Model edge — what is and is not signal

The decision model is **honestly measured and carries no demonstrated edge**:
OOS (2025) Brier 0.2497, skill +0.0012 — a coin flip — with excellent
calibration (ECE 0.0012). See `docs/FINAL_REPORT.md` §3 (RULE E).

Two cheap hypotheses for the missing edge have been tested and **refuted**
(both reproduce the published baseline exactly first):

- **Calendar time is not signal.** `scripts/feature_lab.py` adds time-of-day /
  session / day-of-week features beside a random control. The time arm scored
  +0.0011 — indistinguishable from the random arm, and it worsened ECE.
- **Model capacity is not signal.** `scripts/nonlinearity_lab.py` runs a
  gradient-boosted tree on the identical split. It scored +0.0009 — no better
  than the linear logistic, and calibrated worse.
- **Order flow is not signal (it hurts).** `scripts/volume_flow_lab.py` builds 8
  causal volume/spread/imbalance features from the raw Dukascopy feed (the
  shipped corpus stores OHLC shape only, so these are genuinely new inputs). It
  scored **-0.0040** — negative skill, below a coin flip — while the random
  control left the baseline untouched. The fit overfits the flow dimensions.
- **Cross-asset macro is not signal.** `scripts/crossasset_lab.py` joins DXY
  (Yahoo `DX-Y.NYB`) and the 10-year real yield (FRED `DFII10`) by last-value
  carried forward. It scored +0.0006 — no better than baseline, worse than the
  random control. The dollar and real-rate backdrop adds nothing at M15.
- **The horizon was not the problem — and it exposes the real trap.**
  `scripts/horizon_lab.py` sweeps horizons and looks promising: skill rises
  monotonically, +0.0012 (15m) → +0.0033 (1h) → +0.0063 (2h) → +0.0085 (4h).
  But `scripts/horizon_diagnostic.py` shows it is drift and overlap, not edge:
  the model's accuracy edge over the base rate is **+0.0000 at every horizon**
  (0.5172/0.517, 0.5320/0.532, 0.5430/0.543, 0.5537/0.554), and its skill is
  always *below* the base-rate forecast's skill (e.g. +0.0085 vs +0.0115 at 4h).
  The model simply predicts 2025 gold's upward drift; the longer the horizon,
  the more drift dominates. This is why a rising skill curve must be checked
  against the base rate before it is believed.
- **There is no ranking signal at all.** `scripts/discrimination_lab.py` reports
  threshold-free AUC (the drift-proof measure) with a permutation test. AUC is
  ~0.50 at every horizon — 0.5032 (15m, p=0.41), 0.4992 (1h, p=0.79), 0.5033
  (2h, p=0.42) — so the probabilities rank up- and down-moves no better than a
  coin. At 4h AUC dips to 0.4885 (p=0.013): a faint *reversal* tilt, but it is
  on the wrong side of 0.5, does not survive multiple-comparison correction
  across four horizons and six labs, and its Brier is still worse than the base
  rate. Treat it as exploratory, not an edge.

Both scripts reuse the real pipeline (`load_corpus`, frozen labels, year split,
IRLS logistic, Platt calibration), so their numbers are directly comparable to
the published run. Run them before proposing any new feature: if an idea beats
the random-control arm, it is worth a C++ engine change + full corpus
regeneration; otherwise it is not.

Experiments so far: seven scripts — `feature_lab.py` (calendar time),
`nonlinearity_lab.py` (tree capacity), `volume_flow_lab.py` (order flow),
`crossasset_lab.py` (DXY + real yield), `horizon_lab.py` + `horizon_diagnostic.py`
(forecast horizon), `discrimination_lab.py` (AUC + permutation). Every one
refutes its hypothesis. Do not re-run them expecting a different answer.

The decisive lesson is the horizon diagnostic: a model can show *rising* Brier
skill purely from market drift, with **zero** accuracy edge over the base rate.
Always score against the base-rate forecast, not just the 0.5 reference.

The remaining lever is **new information** the corpus does not contain — real
order-book/tape imbalance, or a stationary target the drift cannot game. No
transformation of the OHLC feed, and no horizon, will produce an edge.
