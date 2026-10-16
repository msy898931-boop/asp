# AGENTS.md

Repository-specific knowledge for the ASTRA desktop app (project AURA) fork.

## What this repo is

A **private working copy** of the public upstream `alimazna/asp` (C++ ASTRA/AURA
gold decision-support desktop app). Upstream is **read-only for us**: we only
clone/fetch from it, never push. All our work lives on `main` here.

- Upstream: `https://github.com/alimazna/asp` (public, ~365 commits, active)
- Our repo: `https://github.com/msy898931-boop/asp` (private)
- Our `main` = upstream `main` + our additions (Telegram bot, mobile PWA,
  akoan engine, container CI). The two lines have diverged (we are ~124 ahead,
  ~101 behind); the resync decision is written down in
  `docs/FORK_UPSTREAM_DECISION.md`. Never push upstream.
- Doc map (current vs historical): `docs/INDEX.md`.

## Layout

| Path | Purpose |
|------|---------|
| `src/` | C++ backend (API, runtime, health, governance, telegram gateway) |
| `frontend/qt/` | Qt 6 desktop UI (the ASTRA window) |
| `bridge/mt5_python/` | Loopback JSON bridge to MetaTrader 5 (Python stdlib) |
| `bridge/telegram_bot/` | **Our** Telegram operator bot (Python stdlib) |
| `bot/` | Watchdog + scheduled scanner (the always-on health layer) |
| `mobile/` | **Our** phone PWA (serves the app and proxies the API) |
| `akoan/` | **Our** news-statistics engine (the News tab) |
| `advisor/` | External second-opinion model (headlines + a read) |
| `android/` | Android wrapper that builds the `.apk` |
| `docs/` | Architecture, runbook, brand, doc index (`docs/INDEX.md`) |
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
python3 bridge/telegram_bot/test_astra_bot.py       # 33 tests
python3 bridge/telegram_bot/test_entitlements.py    # 17 tests
```

`aura_backend_host` launches the bot as a **supervised sidecar** automatically
when `TELEGRAM_BOT_TOKEN` is set (mirroring how it launches the MT5 bridge).
Absence of a token is not an error.

Commands: `/start` `/price` `/level` `/levels` `/clearlevels` `/status` `/plan`
`/myid` `/subscribe` `/unsubscribe` `/help` — exactly the menu the bot registers
with `setMyCommands`, described in Arabic. `/menu`, `/vol`, `/grant` and the
diagnostic `/health` `/analysis` `/signal` `/bridge` also work but stay off the
menu.
Push alerts fire on health/bridge/new-signal transitions (first reading is
primed silently) and when a user's price level is crossed.

**Language: Arabic.** Everything the user sees is Arabic — command replies,
alerts, and the startup message. On boot the bot calls `setMyCommands` with
`BOT_COMMANDS` and `setChatMenuButton`, so the Telegram menu always shows the
real command list regardless of what BotFather was set to. `REPLY_KEYBOARD`
draws a persistent tap keyboard; `BUTTON_COMMANDS` maps each label to its
command, and the router accepts either the label or the command. Direction words
accept Arabic (`فوق`/`تحت`/`أي`) or English (`above`/`below`/`cross`).

### Selling the tool (tiers)

`entitlements.py` + `price_levels.py` add a free/pro gate and price alerts. The
gate is **dormant until `ASTRA_UPGRADE_URL` is set** — with no URL everyone is
Pro, so dev and CI are unchanged. Related knobs: `ASTRA_FREE_LEVELS` (2),
`ASTRA_PRO_LEVELS` (50), `ASTRA_PRO_CHATS` (always-Pro ids). Payment uses a
Stripe Payment Link (no server code, no secret). `mobile/pricing.html` is the
plan page; the buy button reads its URL from `/upgrade.json`, which the mobile
server fills from `ASTRA_UPGRADE_URL`. An admin grants Pro with
`/grant <chat_id> pro`; users read their id with `/myid`. See RUNBOOK §11 for
the mechanics and docs/SELLING.md for the Stripe product/custom-field copy.

`mobile/performance.html` is the **transparency page** — the honest numbers the
project already publishes in `docs/FINAL_REPORT.md`, shown to the buyer: OOS
Brier skill ≈ +0.0012, directional accuracy 51.7%, ECE 0.0015, AUC ≈ 0.50, plus
the walk-forward and the second-provider cross-check. It is a selling point, not
a confession: it says plainly that the model is well calibrated and carries no
demonstrated edge, so the product is the alert tool, not a prediction. If the
tuning changes the numbers, update this page in the same commit — a stale
honesty page is worse than none. `mobile/check_shell.py` (run in CI) fails if any
file named in `sw.js`'s `ASSETS` list is missing, so the offline shell cannot
break silently.

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

- **The access gate is on and every consumer must carry the cookie.** Setting
  `ASTRA_ACCESS_TOKEN` makes `serve_mobile` answer 401 on every route but
  `/api/v1/health`; a first `/?k=KEY` visit stores an HttpOnly `astra_key`
  cookie. Turning it on silently breaks anything that reads the server without
  a cookie: the keepalive canary (`/version.json`, `/akoan.json`), the watchdog
  (engine + poller), the daily report (tracker), and the Android WebView shell.
  Monitoring builds its headers from `astra_env.auth_headers()`; the workflows
  pass `ASTRA_ACCESS_TOKEN` to it. The APK asks for the key once on a 401 and
  establishes the cookie itself, and `sw.js` refuses to cache `/unlock?k=` or
  any 401. When adding a new server reader, send the cookie or exempt the route.
- The backend build is self-contained; the MT5 bridge is loopback-only by design.
- `HttpClient` in `src/foundation/` deliberately refuses non-loopback hosts, so
  it cannot reach Telegram's HTTPS API — that is why the bot is Python, not C++.
- Telemetry-style posture: everything is shadow-mode; nothing carries execution
  authority, and messages always carry a "not financial advice" disclaimer.
- Re-staging the bundle: `python3 scripts/bundle.py --out dist/ASTRA
  --backend-host build/aura_backend_host` then `--verify`. It prunes
  `__pycache__`/`.pyc` automatically.
- The AI advisor (`advisor/`) is an isolated, unvalidated second opinion on
  port 8792. It is deliberately outside the engine and the ledger: the engine
  reads price only and its own edge is ~0, so the advisor adds context/news
  and logs itself (`run/advisor_advisories.jsonl`) to be scored later. Default
  provider is Groq (`GROQ_API_KEY`, global free tier); Gemini is opt-in via
  `ASTRA_ADVISOR_PROVIDER=gemini` (region-restricted). Keys stay server-side;
  the app reaches it via the same-origin `/advisor` proxy. Without a key it
  answers with the snapshot and `configured:false`. Disable with
  `ASTRA_DISABLE_ADVISOR=1`. A failure there must never affect the rest of the
  stack.

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
`astra-state` volume. For a managed host (no VPS to babysit), use Render
(`render.yaml`, permanent free plan) or Fly.io (`fly.toml`, now paid: its free
tier ended in 2024, so an always-on machine costs ~$2-5/month). Both read
the same Dockerfile; Oracle Cloud Always Free is the only free-forever VPS. The `deploy-fly.yml` workflow deploys on demand once `FLY_API_TOKEN` and a Fly account are set up.

Run exactly one instance per bot token: two pollers make Telegram answer
`409 Conflict`.

The image defaults to the **live** source (`ASTRA_DATA_SOURCE=live`), so the
always-on host serves real gold prices with no Windows terminal and no API key.
Set `ASTRA_DATA_SOURCE=replay` for an offline synthetic demo.

Notes: the container must reach `api.telegram.org` (default egress is fine),
`api.gold-api.com`, and `api.kraken.com`; the loopback API/bridge are never
exposed publicly.

### Deploy verification (is my fix live?)

`GET /version.json` answers it in one curl: the `shell` stamp (changes when the
served assets change), the `commit` (from `RENDER_GIT_COMMIT` / `ASTRA_COMMIT` /
`GITHUB_SHA`, else `unknown`), and `uptime_s`. It is `no-store`, so it is never
cached.

```bash
curl -s https://astra-xlp9.onrender.com/version.json
# {"commit":"07a21a8...","shell":"c48cf55964","started_at":...,"uptime_s":...}
```

Two checks watch the live service, both free (GitHub Actions):

- `astra-keepalive` keeps the Render service awake **and** checks it. It runs
  for ~5h17m, pinging `/api/v1/health` every 5 minutes, then re-arms itself
  with a `workflow_dispatch` so the next run takes over. It opens with a
  canary that prints `/version.json` and **fails** if `/akoan.json` answers
  `available:false` — the exact bug that once shipped the News tab blank.
  The long run exists because the cron trigger is too slow to beat the
  15-minute sleep window (see "Never let the server sleep" below).
- `astra-chain-guard` (hourly) is the safety net for the warm chain: if no
  `astra-keepalive` run is in progress it re-dispatches one. It never
  overlaps the chain, so there is no double-poller race.
- `astra-watchdog` (hourly) reads the same body via `check_engine()` and alerts
  on Telegram when the engine serves empty. A 404 or dead connection is treated
  as "unavailable", never as trouble, so an older deploy does not cry wolf.

### Is the interactive bot actually alive?

`GET /bot.json` answers it. The bot long-polls `getUpdates` and writes a
heartbeat every minute (`~/.astra/bot_heartbeat.json`); the phone server reads
its age and reports `available`:

```bash
curl -s https://astra-xlp9.onrender.com/bot.json
# {"available":true,"age_s":12,"pid":1234}
# {"available":false,"reason":"stale","age_s":400,"pid":1234}
```

The hourly watchdog's `check_poller()` fails the verdict on `stale` (three
missed beats, `_BOT_STALE_AFTER` 180 s) so a dead poller alerts on Telegram.
A missing file (`reason":"no heartbeat"`) means this host has no token, which is
not an outage; a 404 means an older deploy, and neither cries wolf.

### Bot state (what a restart keeps)

The bot's whole memory is three JSON files under `~/.astra/` (the container runs
as root, so that is `/root/.astra`):

| File | Holds |
| --- | --- |
| `telegram_subscribers.json` | chat ids that receive push alerts |
| `price_levels.json` | per-chat price levels |
| `entitlements.json` | per-chat free/pro tier |

A restart (or a deploy) rebuilds these objects from those files, so the state
survives only if the directory does. `render.yaml` mounts a persistent disk at
`/root/.astra`; without that disk, `autoDeploy` wipes subscribers, levels and
tiers on every push. `bridge/telegram_bot/test_astra_bot.py`
(`StatePersistenceTests`) proves a restart reloads all three.

### Where ASTRA lives (host + repo are configurable)

The deployed host and the fork repo are read from the environment, not baked in,
so a clone can move to a new deployment by setting two variables:

- `ASTRA_URL` — the service root, trailing slash optional. Read by
  `bot/astra_env.py` (used by `daily_report`, `miss_alert`, `review_digest`,
  `watchdog`), the keepalive and watchdog workflows, and `check_deploy.sh`.
- `ASTRA_REPO` — `owner/repo`, used by `serve_mobile.py` to build the
  `/astra.apk` fallback link (or set `ASTRA_APK_URL` to override the link whole).
- `ASTRA_ACCESS_TOKEN` — optional. Set it and every phone-app route needs the
  key; leave it unset (the default) and the server is open exactly as before.
  See *Access control* under the phone app below.

The watchdog's repo comes from `GITHUB_REPOSITORY`, which GitHub sets in Actions
automatically. Defaults remain the current Render host and fork, so nothing
changes until the variables are set. `bot/test_astra_env.py` locks this down.

**Blank means unset.** A workflow that writes `ASTRA_URL: ${{ vars.ASTRA_URL }}`
sets it to the empty string when the repo variable is absent. `astra_env` treats
blank or whitespace as unset, and every workflow that passes the variable also
carries a `|| 'https://…'` fallback. Both guards exist because a blank host once
built a relative URL and killed the daily report (`7cd58a8`).

### Daily report

`bot/daily_report.py` (workflow `astra-daily`) sends one Telegram message a day,
at **21:00 UTC = 00:00 UTC+3** (local midnight), covering the UTC day that just
ended. Each closed and open idea shows direction, entry, outcome and R, followed
by the basis the rules saw — trend bias, market regime and RSI — so the reader
can see *why*. It only reads the tracker, so it can never invent a result, and
it refuses to state a rate below `MIN_SCORED`. Re-send any day with
`gh workflow run astra-daily.yml -f date=YYYY-MM-DD -f force=true`.

### Paper trial

`bot/paper_trade.py` shadows the live bot: it fetches the same closed bars,
replays the engine, and records the trades the pre-specified rule (H4-aligned,
within 120 minutes of a news event) would have taken to an append-only ledger.
It places no orders and touches no live state. The ledger and its state ride in
the Actions cache under the `astra-paper-` scope, and the job speaks on Telegram
only when the rule trades or the UTC day rolls over — it is a trial record, not
a heartbeat.

The observer runs as the `paper` job of `astra-bot.yml`, riding that workflow's
five-minute cron. A standalone `astra-paper.yml` was tried first, but its
schedule fired only sporadically (twice in total) while `astra-bot` fired
reliably all day, so the observer was moved onto the trigger that works and the
standalone workflow was deleted to keep one source of truth. GitHub's cron also
lags (runs land at :21, :51, …), so the job must stay idempotent and must not
gate on the wall-clock minute.

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
`mobile/serve_mobile.py` serves the shell and reverse-proxies `/api/v1/*`,
`/bridge/*` and `/advisor/*` to 127.0.0.1 (GET only, upstream fixed to loopback).
Run it on a public port; icons are generated with `mobile/icons/make_icons.py`
(stdlib only). Install on the phone via the browser's "Add to Home screen".

Access control: the server is **open by default**. Set `ASTRA_ACCESS_TOKEN`
(or pass `--access-token`) and every route but `/api/v1/health` answers `401`
without the key. Health stays open on purpose — Render, Fly and the keepalive
cron poll it with no credential, so gating it would read as "service down".
A first visit with `?k=KEY` hits `/unlock`, which sets an `HttpOnly` cookie and
redirects to a clean `/`, so the key does not linger in the address bar, history
or `Referer`. The key is compared with `hmac.compare_digest` so a wrong guess
leaks no timing signal. The gate is dormant until the variable is set, mirroring
`ASTRA_UPGRADE_URL`: development, CI and the current deployment behave exactly
as before. `mobile/test_serve_mobile.py::AccessControlTest` locks all of this.

Shell caching: the shell is served `Cache-Control: no-cache` and the HTML has a
content hash appended to `styles.css`/`app.js` (`?v=<build>`), while `sw.js`
fetches the shell network-first. A stale shell is the failure mode to watch for
— the AI tab showed only placeholders because the browser kept an old `app.js`
whose panel markup had no renderer. When a UI change does not appear, suspect
the cache before the code.

A second failure mode looks the same from the browser: if the advisor process
dies (e.g. it raises on boot), the mobile `/advisor` proxy answers 502 and the
AI tab sits on placeholders with no error. Check `run/advisor.log` and
`GET /advisor/v1/advisor` before blaming the shell. The advisor logs
`provider=... configured=...` on startup, so a missing log line means it never
came up.

### Web terminal (the wide-screen view)

`mobile/terminal.html` is the desktop counterpart of the phone app: the same
backend, the same palette, read on a monitor. It is a **separate page** served
by the same server (and stamped/gated with the rest of the shell), so it adds no
risk to the phone shell — if it were folded into `index.html` it would have to
be in the offline shell and every phone would pay for a view it never opens.

Eight sections: Overview (aggregate state, a metric matrix with each reading's
source endpoint, the two honesty badges, degraded reasons), Market & trades,
News & gold, Timeframes (all nine, each with a tap-for-detail dialog), Services
(a live latency board over the same endpoints, plus the bridge readout), Audit,
Risk & System, and Console — a raw endpoint runner so "what does the app
actually receive?" is one click.

**Market & trades** is the one panel built from the decision engine's own
output rather than a state flag. It reads `analysis/latest` for the direction
(`signal.direction`) and the live context (`regime`, `h4_bias`, `m15_trigger`,
`volatility_state`), the risk proposal for the trade plan
(`levels.entry` / `stop_loss` / `take_profit` / `reward_risk` /
`suggested_risk_pct`), `signals/latest` for the last recorded decision
(reference vs last price, drift, outcome flag), and `shadow/positions` +
`shadow/outcomes` for the simulated book. Nothing here is derived: if the
backend returns `null` levels the panel says "no trade plan" instead of drawing
one, and an absent analysis document reads "unavailable", never a direction of
`NONE`. **RULE C is enforced in the renderer**: the probability is shown only
when `signal.probability_calibrated === true`, otherwise the raw score is
labelled a score, never dressed as a probability. Its CSS lives in
`terminal.css` only — the shared `styles.css` the phone app depends on is not
touched, and the tab bar is widened to eight in `terminal.css`.

When no plan is drawn, the panel does not leave a blank "waiting" line: it
names the gate the values are consistent with (`whyNoTrade`), read from the
context the backend already sent — "the regime and structure did not agree on a
direction", "the regime is not eligible under policy", or "the engine denied
the trade". A locked analysis route (401) reads "backend is key-protected", not
a decision of `NONE`. The regime label keys use the surface's real vocabulary
(`rg_TRENDING_UP` / `rg_TRENDING_DOWN` / `rg_RANGING`); a key on the older
`TREND_UP` would silently fall back to the raw English word in the Arabic UI.

The rule that governs every panel: **show the backend's real answer, and when
the answer is unknown say so.** A missing reading renders `غير متاح`, never a
plausible-looking `0`. `refresh()` uses `Promise.allSettled`, so one dead
endpoint degrades its own tile instead of blanking the page, and a `401`
everywhere reports "backend locked", not "unreachable" — the two send an
operator down different paths. `mobile/test_terminal.py` pins all of this
(asset wiring, the stamp, gate coverage, bilingual key parity, no `eval`, no
absolute origins, and the unknown-is-not-safe rule).

**News & gold** is the live news read. `advisor/news_engine.py` assembles a
digest from keyless public sources — the TradingView economic calendar
(`advisor/calendar_feed.py`, which owns the release fetch and the mechanical
surprise read) and the Google News RSS gold search (the same reader the advisor
uses). It reuses, never re-implements: `news_window.blackout_event` /
`upcoming_event` own the blackout logic and the Arabic one-liner, and
`calendar_feed.classify` owns the gold direction, so the bot, the advisor and
this panel cannot disagree. The digest is the blackout window (state + reason),
the upcoming schedule, the just-released events with their gold read, and the
day's headlines. `serve_mobile.py` exposes it as `/news.json`, cached ~180 s;
the engine is imported from `advisor/` the same way `/akoan.json` imports
`akoan/` and `/vol.json` imports `bridge/telegram_bot`. The route is
best-effort and never 500s: a dead feed yields empty lists and the panel shows
its empty states.

The honesty rules carry over from the sources and are worth restating: a
release with no forecast and no previous has no baseline, so no direction is
asserted; the gold link is asserted only for a US release (elsewhere it is
labelled indirect, because only the dollar has a mechanical link to gold); the
wording is "pressure" / "support", never "will fall"; and the caveat says the
news effect on system results is not measured, so no edge is claimed. The panel
is fetched in `refresh()` **before** the "all endpoints dead" early return, so
the news still renders when the backend is down — it reads public feeds, not
the backend. `advisor/test_news_engine.py` and the `NewsPanelTest` class in
`mobile/test_terminal.py` pin the window logic, the baseline honesty, the
dollar-only gold read, and the route wiring, all offline.

Arabic-first but locale-following, like the app: no saved choice means the
phone's language decides; `?lang=ar` / `?lang=en` forces one (and persists it).


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

### The stationary target the drift cannot game (Phase 0 — GO)

`research/horizon_probe.py` takes the second half of that lesson seriously and
switches the target from the *sign* of the next move to its *size*. Volatility
clusters, and that is a law, not a regime: trailing volatility ranks forward
volatility with OOS (2025) AUC **0.688** (1h), **0.729** (4h), **0.794** (1d),
and holds in every calendar year (0.646–0.729). The direction baselines in the
same table sit on the base rate (0.49–0.51) — a coin flip, confirming the
sections above.

The probe also nails the drift trap from the other side: the OOS up-move base
rate at the 15m horizon is **0.517**, *identical* to the published directional
accuracy. T27's 0.517 was the market drifting up, not the model predicting it.
Verdict and numbers: `research/PHASE0_VOLATILITY_DECISION.md`.

The product this justifies is a **"do not trade now" volatility filter** (honest,
never claims direction). It is Phase 1+; build it on `run_calibrated` with a
"forward vol above median" label, and fold the news-quiet window into it.

### The deployable volatility model (Phase 1 — shipped in the bot)

`scripts/volatility_lab.py` trains the filter and `--export`s a self-contained
JSON artifact (`bridge/telegram_bot/vol_model.json`). It reuses the published
pipeline (IRLS logistic, l2=0.05, Platt calibration, chronological year split
dev 2021-22 / val 2023-24 / OOS 2025). The label is stationary: each instant is
"1" when *forward* volatility over the horizon beats the *causal* rolling median
(30-day lookback), so market drift cannot game it the way it gamed the
directional target.

OOS (2025), 4h horizon, multi-timeframe feature set
`(M5|M15|H1|H4|D1).(volatilityRatio|atrRatio)`:

| metric | value | publish bar |
|---|---|---|
| Brier skill | **+0.1750** | > 0 |
| AUC | **0.743** | > 0.5 |
| ECE | **0.0325** | < 0.05 |

The M15-only arm scores AUC 0.575 — **multi-timeframe is the winner**, so the
artifact carries all five timeframes the bot actually fetches. The solver is
pure-Python (stdlib-only, no numpy by design), which is why feature reduction
was required to keep IRLS tractable.

`bridge/telegram_bot/volatility.py` is the live inference path: it recomputes
`volatilityRatio` and `atrRatio` from closed candles exactly as
`AnalyticalFeatureEngine.cpp` defines them (sample stdev of log returns over the
9-bar trigger window vs the 200-bar context; Wilder mean true range; clamped to
[0,1]). **`test_volatility_parity.py` runs the real `aura_feature_dump` and
asserts the Python values match to 1e-9 per timeframe** — if the engine's
definitions change, that test fails. The model returns `None` (bot stays silent)
rather than a number when fewer than five timeframes have data.

The bot exposes `/vol` (a single reading) and the monitor thread emits one
edge-triggered alert when the probability crosses the threshold, re-arming only
after the market calms — never a per-cycle repeat. The alert text states
explicitly that it measures *size*, not direction.

### The news-quiet window (Phase 2 — folded into the filter)

`advisor/news_window.py` answers one question for the bot: is *now* a bad moment
because a high-impact release is imminent or just printed? The logic is pure —
it takes the normalized event list and a timestamp, returns a reason string or
None — so it is fully unit-testable and pays no network cost of its own.
`advisor/calendar_feed.py` owns the fetch.

- `blackout_event` / `blackout_reason` — ±15 min around a high-impact event
  (`importance >= HIGH`). The message says "the market is usually violent", never
  that it *will* move.
- `upcoming_event` — a relevant event within 45 min, when the coast is clear now
  but a release looms.
- `recent_context` — the level feature's "interpretation 1": when a price level
  fires, the alert names the newest relevant release and reuses
  `calendar_feed.classify`, so the baseline (forecast or previous) is always
  named and gold direction is only asserted for a US event.

`astra_bot.py` fetches the calendar **once per monitor cycle** (`news_snapshot`)
and hands the same event list to both the volatility alert and the level alert.
The advisor import is best-effort and probes several path shapes (repo
`<root>/advisor`, container `/app/advisor`), so the bot degrades to
volatility-only rather than crashing when the advisor is absent.

**Honesty caveat (stated in code and on the page):** the news-quiet window is a
*safety heuristic*, not a measured signal — the corpus has no historical
calendar, so unlike the volatility model its effect cannot be backtested. The
`/vol` reading and its alerts say so.

**Plan B - the historical calendar, now built.** The caveat above was true of
the Forex Factory mirror (one week, no `actual`). The advisor's TradingView
calendar also serves the *past* with `actual` populated, so
`research/features_real/build_news_ledger.py` fetches it month by month into a
committed **27,398-event ledger over 2,147 days (2021-2026)**
(`news_ledger.jsonl.gz`), in the same append-only shape `akoan/events.py` reads.
`news_study.py` joins it to the feature store and gates every news condition.
Result: **nothing clears the gate**, but the loss structure is the finding -
almost all of the -598R lives in the *quiet* window (-573R); news-in-play is
net-flat, "after a release" is +30R and "before a release" is the worst bucket
(-0.148 R/trade). The "after a release" lead survived ATR-matching, split-year
and window-sensitivity checks and still fails on sample size. Report:
`docs/research/PLAN_B_HISTORICAL_NEWS.md`. The engine is untouched.

**Plan C - one rule, fixed before looking.** `research/features_real/rule_test.py`
takes the single rule the two studies implied — *H4-aligned AND within 120 min
after a release* — and gates it, plus a permutation test the gate cannot give.
Result: 282 trades (4.0% of the book), R/trade **+0.117**, permutation
**p = 0.0067** (survives a 3-test Bonferroni correction), positive in-sample
(+0.188) and out-of-sample (+0.046), robust to the window (positive 30-240 min).
But it is flat in 3 of 6 years, and the gate still returns **REJECT** (Wilson
lower bound below break-even on both halves). So: a **real, significant lead,
not a proven edge** — which earns a paper-trading trial, not live. Report:
`docs/research/PLAN_C_PRESPECIFIED_RULE.md`.

**Plan C, step 2 - the rule is now running on paper.** `bot/paper_trade.py` +
the `paper` job of `.github/workflows/astra-bot.yml` shadow the live bot: they
fetch the same closed bars, replay the engine, and append the trades the rule
would take to `run/paper_trades.jsonl` (de-duped by time+direction+entry, cached
between runs). No orders, no live state touched, no engine change. The channel
gets a message when the rule trades or once a day, and the read states a win
rate only once there are enough scored trades. This is the only honest way to
settle a rule that is significant but under-sampled: forward in time, on bars
the backtest never saw. If the rule is wrong, the ledger will show it.

The observer was first given its own `astra-paper.yml` on a 15-minute cron, but
that schedule fired only twice in total while `astra-bot` fired reliably all
day, so it was moved onto the bot workflow's five-minute cron and the standalone
workflow was deleted (see the *Paper trial* section above).

### akoan — the news ↔ statistics bridge (the caveat above, closed)

`akoan/` is an additive engine that turns the caveat above into a test. It
builds the historical calendar the corpus never had, joins it causally to real
trades, and reports whether the news explains the results — with the honest
default of "no". It changes no existing engine and writes nothing the rest of
the system reads.

- `stats.py` — the tests, stdlib-only: Wilson interval, Fisher exact, a
  label-shuffle permutation test, and Benjamini-Hochberg FDR control. The edge
  it reports is always the gap from the **base rate**, never from 0.5 — the
  lesson from `horizon_diagnostic.py`, where a 51.7% "accuracy" was gold's
  51.7% drift.
- `events.py` — an append-only event ledger (`run/akoan_events.jsonl`,
  gitignored). Backfills the keyless TradingView calendar (`--backfill DAYS`;
  the feed reaches ~120 days) and self-accrues on every run. De-duplicated by
  `(at_utc, title, country)`, so a later revision of `actual` does not
  duplicate the row. `coverage()` states how thin the evidence is.
- `state.py` — the causal news state at one instant: blackout, recent release,
  upcoming, and whether the trade leaned with or against the news read. Only
  events at or before the timestamp are consulted, so a later release can never
  explain an earlier trade. Pure — no clock, no network.
- `link.py` — joins `run/signal_trades.jsonl` with the news state. **Collapses
  the tracker's repeated appends**: a trade is one `(time, direction, entry,
  stop, target)`, however many polls re-appended it (without this, one idea
  counted ~200 times and every rate was fiction).
- `engine.py` — scans every dimension, applies FDR, and returns a verdict:
  `NO_EDGE_MEASURED` / `INSUFFICIENT_DATA` / `EDGE_CANDIDATE`. A dimension with
  only one populated bucket is marked `comparable: false` and reported as
  "ما بينقاس" rather than shown as a rate with nothing to compare against.
- `study.py` — replays the *project's own* chain (`tracker/replay.py`) over the
  committed M1 corpus resampled to M15, so the study trades are the trades the
  engine would take. This is what gives the analysis months of depth.
- `run.py` / `report.py` — CLI and the Arabic report (also JSON). The report
  always names its baseline, its coverage, and its disclaimer.

**The measured result (over the committed corpus):** 353 scored trades, base
rate 34.8%. Every news dimension sits on the base rate — `blackout` and
`upcoming` have too few trades to compare, `recent_release` 36.4% vs 34.7%,
`news_in_play` 36.7% vs 34.7% — and **nothing survives BH-FDR correction**.
Verdict: `NO_EDGE_MEASURED`. So the news-quiet window stays a safety heuristic,
now with evidence behind the claim rather than only an argument.

**Operational note:** the feed prunes old rows, so re-run
`python3 akoan/events.py --backfill --days 120` periodically; the ledger keeps
what the feed later forgets. CI runs the tests and asserts the study
reproduces (`akoan/study.py --json`), so the numbers cannot drift silently.

**Where it surfaces.** Two places, both reading the same engine:

- **The phone's News tab** (`mobile/`, `/akoan.json`). The mobile server runs
  `study.study()` and returns a compact body — verdict, baseline, one bucket per
  dimension, coverage. Cached 10 min behind a compute lock, because the replay
  is heavy and every phone polls. The cache is **warmed in a background thread
  at startup** (`warm_akoan_cache`), so the first phone to open the tab reads a
  cached result instead of waiting the ~100 s cold replay on a blank panel. The
  panel's text is built in JS, so `applyLang` calls `rerenderAkoan()` to rebuild
  it from the last reading on a language switch (an `[data-i18n]` sweep cannot
  reach generated nodes). Because the read can still be slow, the panel shows
  elapsed seconds past 20 s (`akoan_slow`) rather than a frozen "loading…", and
  the app bounds every request with `AbortController` (`FETCH_TIMEOUT_MS`,
  `AKOAN_TIMEOUT_MS` for this one) so no panel can hang forever.
- **A weekly Telegram digest** (`bot/weekly_akoan.py`, workflow
  `astra-weekly-akoan.yml`, Monday 06:00 UTC). Weekly, not daily: the answer
  moves slowly and a daily message would be noise. ISO-week state in the
  Actions cache makes a repeated cron slot idempotent. The ledger is accrued
  first, then cached back, so the record only grows. The workflow was added on a
  Wednesday and so showed zero runs until its first Monday (`2026-10-20`) — a
  schedule that has not reached its first slot is not a broken schedule. Verify
  one by hand with `gh workflow run astra-weekly-akoan.yml -f dry_run=true`.

**Shipping note.** Two things the source tree has that the image does not, and
both once made the phone's News tab answer "engine unavailable" in production
while every CI job stayed green (they all run against the source tree):

1. `Dockerfile` must `COPY akoan /app/akoan` — a route can import a package the
   image never copied and no source-tree test can see it.
2. `run/akoan_events.jsonl` is gitignored and absent in a fresh container, so
   `events.load()` falls back to the committed seed `akoan/events_seed.jsonl`.
   The live ledger wins on a shared key, so accrued events are never masked;
   passing an explicit path reads only that file. Refresh the seed with
   `cp run/akoan_events.jsonl akoan/events_seed.jsonl` when the live ledger
   grows. The `container` CI job builds the real image and asks the running
   service for the route, which is the only job that can catch this class.

The transparency page (`mobile/performance.html`) now leads with the measured
volatility edge (AUC 0.743) and keeps the directional coin-flip (AUC 0.50) as
the honest contrast.

## Product surface (mobile app / APK / desktop)

The phone app is `mobile/` (a PWA served by `scripts/start_stack.sh`) and the
installable Android app is a WebView shell around it (`android/`). Two rules
keep them honest and in sync:

- **The app states its own limits.** The analytics panel is titled "Market
  state" and carries two permanent honesty badges: *Direction edge: none
  measured (OOS 2025, AUC ≈ 0.50)* — matching the research finding above — and
  *Volatility edge: measured (OOS 2025, AUC ≈ 0.74, predicts size not
  direction)*. Confidence is shown only when the backend produced a
  *calibrated* probability; otherwise it reads `n/a` rather than inventing a
  number from an uncalibrated score. Do not reintroduce predictive language
  ("signal", "buy/sell") — that would assert an edge the corpus does not
  support.
- **Bilingual by design (EN/AR, RTL).** All user-facing strings live in the
  `STRINGS` table in `mobile/app.js` and are applied via `data-i18n` attributes.
  The layout flips with `dir="rtl"`; numeric readouts stay LTR. Add a string in
  both languages or not at all.

The app is read-only: one origin (`serve_mobile.py`) reverse-proxies `/api/v1`
to the backend and `/bridge` to the bridge. It never places trades.

### The tab bar (five primary tabs plus a More sheet)

Nine views will not fit one phone-width row at a readable size (~594px vs
375–430px), and a two-row bar ate roughly 80px of the bottom of every screen.
So the bar carries the five views that make up the daily read — Status, State,
Chart, Vol, News — plus a **More** button, six cells in one row. The four
secondary views (Signals, AI, Data, Bridge) open in a `.tabs-more` sheet that
sits flush on top of the bar.

* `setMoreOpen()` toggles the sheet's `[hidden]` and the More button's
  `aria-expanded`. A tap outside the sheet closes it (the click handler checks
  `e.target.closest("#tabs-more")` and `.tab-more`).
* `selectView()` owns the selection: it sets `aria-selected` on the chosen tab
  and clears the rest, then also marks **More** as selected whenever one of its
  four views is open, so the bar still says where you are after the sheet
  closes.
* The bar has a fixed height (`calc(46px + env(safe-area-inset-bottom))`) so the
  sheet can anchor exactly above it without guessing at the rendered row size.
* `updateTabFades()` and the `.more-left` / `.more-right` masks remain for a
  wide screen that still overflows; the hidden side flips with direction (RTL
  starts at the right), chosen by `getComputedStyle(el).direction`, not assumed.
  Fades recompute on scroll, on `resize`, on `orientationchange`, and after a
  language switch (which flips direction).

Arabic sets `html[dir="rtl"] .tab { letter-spacing: normal }`: the Latin
tracking that separates capitals breaks the joins between Arabic letters.

### The volatility panel (the phone's one measured edge)

The "Vol" tab shows the same calibrated reading the bot reports. `serve_mobile.py`
exposes `/vol.json`, which imports the bot's `volatility.py` from
`bridge/telegram_bot`, fetches M5/M15/H1/H4/D1 candles from the bridge, and
scores the loaded artifact — the *same* artifact and *same* code, so the phone
and the bot cannot disagree by construction. The result is cached (~20 s) so the
15 s app refresh does not re-hit the bridge five times each cycle.

The route is best-effort: an unreachable bridge, a missing model, or too few
closed bars yields `{"available": false, "reason": ...}` and the panel reads
"reading unavailable" — it never fabricates a number. The threshold tick on the
meter is the model's own `alert_threshold`; the panel states plainly that this
measures *size*, not direction, and is a safety filter rather than a trade
signal.

### The trade journal tab (and the daily Telegram report)

The "Journal" tab (`tab_tracker`) lists the tracked ideas newest-first, with a
`Today`/`All` filter. `Today` keeps the ideas whose timestamp falls on the
device's local day; the summary strip shows closed count, the W/L record, net R
and net **$**, plus where a still-open idea stands *right now* (live R, from the
ticker price `lastPrice`). The dollar figure is the **minimum position** (1 oz =
$1 per $1/oz move), never an implied account return — same baseline as the bot.

`bot/daily_report.py` sends the same view to Telegram once a day
(`.github/workflows/astra-daily.yml`, cron `0 20 * * *`, UTC). It reads
`/tracker/v1/signals`, keeps the day's signals, and always appends the honest
line: below `min_scored` (30) it says the sample is too thin to judge, and the
message ends "not financial advice". State (`run/daily_report_state.json`) rides
in the Actions cache so a repeated slot does not double-send. Both surfaces read
the tracker; neither can invent a result.

### APK build

`android/build.sh` needs only a JDK and SDK build-tools (no Gradle). Build with
build-tools **35** on a modern JDK — build-tools 34's `d8` throws an NPE on
JDK 21:

```sh
JAVA_HOME=/path/to/jdk ANDROID_HOME=/path/to/sdk BT_VER=35.0.0 \
  android/build.sh --url https://your-host/ --name ASTRA
```

The server URL is baked in but changeable on-device from the error screen. The
CI workflow `build-android.yml` builds it on every `mobile/**` or `android/**`
push, signed with a cached keystore, and publishes it to the rolling `android`
release — which is what the web footer's `/astra.apk` link redirects to.

### Live price provenance (gold-api vs Kraken PAXG)

Two upstreams, and only one of them can serve candles for free:

* **Spot / tick** - `api.gold-api.com/price/XAU`. Free, unlimited, no key. This
  is the exact spot XAUUSD and the source of truth for the tick read.
* **Candles** - Kraken `PAXGUSD`. gold-api's own `/ohlc` and `/history` endpoints
  return `401 No x-api-key header` without a key, and the free tier caps them at
  10 requests/hour with a finest `groupBy` of `day` — no M15, so it cannot feed
  the M1..D1 engine. PAXGUSD stays the candle source.

Because PAXG carries its own premium/discount (~0.1%, several dollars at $4200),
`_fetch` scales the series onto the spot quote using the **live proxy tick** as
the anchor leg. Do not anchor on the newest closed bar: it closed up to a full
interval ago and would drag the current price to a stale level. Verified live,
the bridge close then tracks spot to within about a dollar.

Yahoo `GC=F` is not a substitute — it is COMEX futures and runs ~$29 above spot.

### "No signals / market shows UNKNOWN"

Two separate causes, both diagnosed by inspecting the live API:

1. **No directional candidate.** `SignalEngine::generate` requires two of three
   votes to agree (structure bias, regime trend, RSI vs `rsiLongThreshold`/
   `rsiShortThreshold`). When they disagree the bar yields `direction=NONE` and
   no decision — the honest outcome, not a fault.
2. **UNKNOWN context on a no-trade bar.** Historically `DecisionPipeline::
   onClosedBar` returned early without recording `lastContext_`, so the
   frontend rendered `regime`/`h4_bias`/`volatility_state` as UNKNOWN — which
   looks identical to "not analysing". Fixed by publishing the evaluated
   context on every closed bar, including no-trade ones.

Diagnose from the host, not the UI: `/api/v1/context/latest` (regime/structure),
`/api/v1/audit/recent` (`audit_stream_size`, decisions), `/api/v1/timeframes`
(freshness), and the bridge's `/v1/candles?tf=M15&limit=500`. A regime of
`RANGING` or `VOLATILE` with defaults (`allowRanging=true`,
`allowVolatile=false`) is ineligible by policy; RSI thresholds are 52/48.

### Building the backend in a sandbox

`build/CMakeCache.txt` pins `CMAKE_COMMAND=/usr/bin/cmake`, which does not exist
here — a stale `build/` will fail with `gmake: /usr/bin/cmake: No such file or
directory`. Install cmake with `pip install cmake` (it lands in
`~/.local/bin`, which is not on PATH), configure a fresh tree
(`cmake -S . -B build2 -DCMAKE_BUILD_TYPE=Release`), build, then copy
`build2/aura_backend_host` over `build/aura_backend_host`. Stop the stack first:
the running binary is `Text file busy` otherwise.

### Telegram bot 409 conflicts

`getUpdates` returns HTTP 409 when another process polls the same bot token.
The second poller is the always-on deployment (`render.yaml`, and the
manual-only `deploy-fly.yml`), which holds the token as a host secret. Killing
the local bot removes the conflict; the remote instance then polls cleanly on
its own.

The local stack never runs the bot by default — `start_stack.sh` requires both
`ASTRA_ENABLE_BOT=1` and a non-empty `TELEGRAM_BOT_TOKEN`. `watch_stack.sh`
calls `start_stack.sh` with the inherited environment, so to keep the bot off
permanently, launch the watchdog with the token unset
(`env -u TELEGRAM_BOT_TOKEN -u ASTRA_ENABLE_BOT scripts/watch_stack.sh`).
Confirm with `getWebhookInfo` (empty URL) and by watching the 409 counter in
`run/host.log` while making no external Telegram calls.

To see whether a poller is running *right now*, ask the server instead of
guessing from 409s: `GET /bot.json` reports the heartbeat's age (see "Is the
interactive bot actually alive?"). A `stale` reading is a real outage; a
`no heartbeat` reading means the host has no token, which is not one.

### Per-bar answers: the heartbeat contract

The user wants an answer on **every** closed M15 bar, not only when the engine
trades. That is the heartbeat, and it is the default (`ASTRA_HEARTBEAT=1`;
set it to `0` to go back to trade-only alerts).

* `tracker/replay.py::explain_last_bar` returns the newest bar's decision from
  the same loop that opens trades, so it can never disagree with the ledger.
  A no-trade status carries a `reason` (`NO_EDGE`, `VOLATILE`, `IN_POSITION`,
  `COOLDOWN`, `JUST_RESOLVED`, `STRUCTURE_INCOMPLETE`, `NO_DATA`) plus the
  engine's `read` (regime, bias, rsi, atr, close). Never blank.
* `bot/scan_once.py` sends one message per bar and dedups on the bar's own
  `time` (`state["last_bar_time"]`), so running the cron more often than 15 min
  only buys reliability, never duplicates. The bot cron is every 5 minutes for
  exactly this reason.
* `bot/telegram_bot.py::format_last_bar` renders either a trade (levels + the
  reason + the money) or an explained no-trade. `REASON_AR`/`REGIME_AR`/`BIAS_AR`
  hold the Arabic labels; keep them in sync with the app's `NOW_REASON` etc.
* `mobile/app.js::renderNow` draws the same status at the top of the tracker
  tab (`#trk-now`). The `/tracker/` route is network-only in `sw.js`.

**Do not force a trade every bar.** Measured on 200 M15 bars: the strict engine
produced 9 ideas, while trading every bar produced 146 at a 34% win rate and
-13.6R net. The silence is the edge, not a bug. This was shown to the user, who
chose the heartbeat (option A) over forced trades.

### $200 account and gold's minimum lot

Gold's smallest tradable size is 0.01 lot = 1 oz, so at a ~6.4 USD/oz stop the
minimum position risks ~6.4 USD = ~3.2% of a 200 USD account, regardless of the
1% asked for. `replay.account_plan` computes this and sets `below_min_lot`;
both the bot (`_plan_line`) and the app (`now_plan_warn`) say it out loud rather
than quoting a position that cannot be traded. Money figures use the minimum
1 oz position (`_result_usd`), the honest baseline. Config: `ASTRA_ACCOUNT_USD`
(default 200), `ASTRA_RISK_PCT` (default 1.0).

### Never let the server sleep

Two always-on pieces, and they are independent:

* **The bot** runs on GitHub Actions (`.github/workflows/astra-bot.yml`) and
  reads candles straight from the upstream via `LiveMt5Client` — it does **not**
  touch the Render server, so it is always on with nothing to keep warm. Do not
  add a Render dependency to `bot/scan_once.py`; that would couple the never-
  sleeping half to the one that naps.
* **The phone app and the interactive bot** both live on the Render service,
  and Render's free plan spins a service down after ~15 minutes idle. The bot
  is not a separate process to keep warm — the backend launches it whenever
  `TELEGRAM_BOT_TOKEN` is set — so keeping the service awake keeps the bot
  answering too.
* **Cron cannot keep it awake.** GitHub's cron is best-effort: on this repo a
  5-minute schedule has landed 20–75 minutes apart, longer than the sleep
  window. `astra-keepalive` therefore runs for ~5h17m (under the 6-hour job
  limit), pinging every 5 minutes, and re-arms itself with a
  `workflow_dispatch` (needs `actions: write`) so each run starts the next.
  It is dispatch-only — a cron on the same workflow would queue behind the
  running chain and then start a redundant second one. `astra-chain-guard`
  (hourly cron) is the safety net: it re-dispatches the keepalive when no
  run is in progress, so a dropped re-arm heals within the hour.

`render.yaml` says `plan: starter` (paid, never sleeps), but the live service
is on the free plan — hence the warm chain. One always-on service still fits
Render's free allowance (750 instance-hours; a month is ~730). If the service
is ever upgraded the chain is harmless and can stay. The health path is
`/api/v1/health`; the bare `/health` and `/api/health` both 404.

### The AI advisor must be started, not just built

The advisor (`advisor/ai_advisor.py`) is an isolated second opinion behind the
app's "AI advisor" tab, reverse-proxied at `/advisor` (port 8792). It shipped in
the repo but was **never wired into the container**: the Dockerfile did not
`COPY advisor` and `scripts/docker_entrypoint.sh` did not launch it, so the app
always reported "backend unreachable". Both are now fixed; the proxy already
mapped `/advisor` to 8792 via `--advisor-port`.

Two keys to remember:
* It **always starts**, even with no key. With no key it answers
  `configured: false` and a note, and the app shows "not configured" — never an
  error. So an empty `GROQ_API_KEY` is a valid state, not a failure.
* To make it speak, set `GROQ_API_KEY` (provider default `groq`, model
  `openai/gpt-oss-120b`) on the **host** (Render dashboard; the blueprint marks
  it `sync: false`). `.env` holds a working key for local runs but is gitignored
  — never commit it, the repo is public.

### The economic calendar and the Arabic trade plan

The advisor also reads a keyless economic calendar so it can name the scheduled
release that will actually move gold and build a pre-trade plan around it.
`fetch_calendar()` parses FairEconomy's XML mirror of the Forex Factory calendar
(`ff_calendar_thisweek.xml`; the JSON twin 429s datacenter IPs fast, the XML does
not), keeps only high-impact USD/EUR rows inside
`ASTRA_ADVISOR_CALENDAR_HORIZON` hours, and `upcoming_events()` caches them for
`ASTRA_ADVISOR_CALENDAR_TTL`. A failed fetch sets `failed_at` and is retried only
after `ASTRA_ADVISOR_CALENDAR_RETRY`, so the feed is never hammered. Events land
in the snapshot as `events`, `events_available`, each with an Arabic `title_ar`
from `arabic_event_name()` (CPI, NFP, FOMC, PCE...).

Every human-readable field the model returns is Arabic (keys stay English): the
thesis, the risks, `event`, and a `plan` object
(`bias/trigger/entry/stop/target/note`). The app renders the headlines, the
upcoming high-impact events, and the plan table in the advisor card. Knobs:
`ASTRA_ADVISOR_CALENDAR=0` off, `_CCY` / `_HORIZON` / `_LIMIT` / `_TTL` / `_RETRY`.

Verify a deployment with `curl https://<host>/advisor/v1/advisor` and check
`configured`, `snapshot.events`, and `opinion.plan`. The advisor test suite runs
in CI and covers parsing, the RSS/XML parsing (offline, stubbed), the calendar
filters and backoff, and the not-configured degradation without any network.

### Learning from its own mistakes (self-review)

A model is stateless, so it cannot remember a bad call on its own. `self_review`
gives it a memory the honest way: every advisory is appended to
`run/advisor_advisories.jsonl`, and on each later request `run_review()` reads
that log, and once a call is `ASTRA_ADVISOR_REVIEW_HORIZON` old it compares the
stance with where price actually went and writes a scored outcome to
`run/advisor_outcomes.jsonl`. LONG is right if price rose, SHORT if it fell,
NEUTRAL if it moved less than `ASTRA_ADVISOR_NEUTRAL_BAND` percent. The misses
become a short Arabic `lessons_block()` injected into the next prompt ("do not
repeat this call without a new reason"), and the running scoreboard is returned
as `review` and shown in the app. `ASTRA_ADVISOR_LESSONS=0` disables the
injection. This never touches the validated ledger — it only reads and writes
the advisor's own files, and every number is best-effort.

### Alert before a high-impact release

`bot/news_alert.py` runs on the `astra-news` GitHub Actions cron (every 15
minutes, state cached). It reads the same calendar the advisor does, and when an
event is inside `ASTRA_NEWS_LEAD_HOURS` (default 1h) it sends one Arabic
Telegram alert per event (dedup key `country:title:at_utc`, kept in
`run/news_alert_state.json`), naming the event and telling the reader not to
trade into it. `--dry-run` prints instead of sending. Tests in
`bot/test_news_alert.py`.

### What a release actually did

The pre-release alert can only warn: the Forex Factory mirror carries forecast
and previous, never a released value. `advisor/calendar_feed.py` reads the
keyless TradingView calendar instead, which carries `actual`, and turns a
release into a directional read:

  * actual vs forecast — falling back to previous, and saying which it used;
  * higher-is-stronger, with the inversion for jobless claims and the
    unemployment rate (a high print there is currency-*negative*);
  * USD for US events, EUR for EU; and for a US event only, the usual gold
    pressure (a stronger dollar presses gold). A non-US event is labelled as an
    indirect gold link rather than asserting one, because the direct link is
    the dollar.

`bot/news_result.py` runs on the `astra-result` cron (every 15 minutes, state
cached) and sends one Arabic message per released, market-relevant event —
high-impact, or a medium whose indicator is known to move gold (ADP, CPI, PPI,
NFP, claims...). Dedup key `country:title:at_utc` in
`run/news_result_state.json`. A release with no baseline at all says so instead
of guessing. `--dry-run` prints. Tests in `bot/test_news_result.py` and
`advisor/test_calendar_feed.py`.

This is the honest answer to a message that compares an actual against nothing
and calls it "positive for the dollar": the read here always names the baseline
it used, and refuses the call when there is none.

### The self-review digest

`bot/review_digest.py` runs on the `astra-review` cron (daily, 07:00 UTC). It
nudges the advisor for one fresh call (`/v1/advisor?refresh=1`, so the loop is
fed even when the app is never opened), then reads `/v1/review` and sends
`self_review.format_review()` to Telegram: hit rate overall and per stance, the
recent misses with their lesson. `--no-poke` skips the nudge; `--dry-run`
prints. If the advisor is unreachable it says so and exits non-zero — it never
invents numbers. `ASTRA_REVIEW_URL` overrides the endpoint (an unset Actions
`vars.` arrives empty and is treated as unset).

`bot/miss_alert.py` (the `astra-miss` cron, every 15 minutes) is the instant
version: it reads the same endpoint, and for each scored call the advisor has
not already been alerted on it sends one short Arabic message — a hit
(`format_hit`) or a miss with its lesson (`format_miss`). `--misses-only` keeps
it to the mistakes. Dedup rides in `run/miss_alert_state.json`; the first run
primes silently, so a lost cache never blasts the back-catalogue. This is why
`/v1/review` exposes the full `outcomes` list, not just `recent_wrong`. A brief
price outage stops new scoring but still summarizes what is already on file, so
a missed alert is only delayed, never lost.

Routine bars stay quiet: `scan_once.should_heartbeat` suppresses the heartbeat
for `IN_POSITION` and `COOLDOWN` (an open trade just being held, or its
cooldown), so holding a trade no longer repeats the same line every M15 bar.
`ASTRA_HOLD_PING=1` restores the old behaviour; trades, resolutions and genuine
no-edge reads always speak.

But a held trade running toward a level is worth saying. On the first bar a
live trade gets within `ASTRA_APPROACH_FRAC` (default 0.2) of its stop-to-target
span, `scan_once` sends one heads-up (`telegram_bot.format_approach`) naming the
nearer side and the distance left. It fires once per bar, so it cannot spam;
`ASTRA_APPROACH_ALERT=0` silences it.

### The silent failures: watchdog and stale data

`bot/watchdog.py` (the `astra-watchdog` cron, hourly) is a dead-man's switch
over the failures the trading jobs cannot report on their own: the keepalive
chain died (`check_keepalive`, no `astra-keepalive` run in progress and the last
one older than `ASTRA_KEEPALIVE_MAX_AGE_MIN`, 400 min — the real liveness
signal), the scanner stalled (`check_bot`, last successful `astra-bot` run older
than `ASTRA_BOT_MAX_AGE_MIN`, 360 min — a deliberately loose secondary signal,
because the scanner rides GitHub's best-effort cron, which this repo has seen
stall for up to 4.6 hours), the web server is down (`check_server`, no 200 from
`/api/v1/health`), the engine serves empty (`check_engine`), the **interactive
poller** stopped (`check_poller`, reads `/bot.json`), and the repository has gone
idle long enough (`check_commit`, over `ASTRA_COMMIT_WARN_DAYS`, 55 days) that
GitHub disables the scheduled workflows outright. It alerts on the transition
into trouble, re-alerts every `ASTRA_WATCHDOG_REALERT_HOURS` (6) while the
trouble lasts, and confirms recovery. `decide()` is pure over (verdict, state,
now); the state rides in the Actions cache. An unreachable GitHub API never
cries wolf — the check returns "unavailable" and stays quiet. So does a 404 (an
older deploy without the route) or a missing heartbeat (a host with no token):
only a *stale* beat, or a real 200-but-empty engine, is trouble.

`scan_once.staleness()` guards the other silent failure: a bar older than
`ASTRA_STALE_AFTER_MIN` (45) means the feed is quiet or the market is closed
(gold shuts Fri 22:00 – Sun 23:00 UTC). When stale, the scan sends no ideas, no
results and no approach warnings; it says so once per episode
(`format_stale`) and confirms when it resumes (`format_resumed`). The bar
timestamp is the candle's *open*, so a just-closed M15 bar already reads up to
15 minutes old — the threshold has to clear that before it means anything.

### Where the edge is: the digest's breakdown

`replay.summarize` carries `by_regime` and `by_direction` hit rates plus
`max_loss_streak` (the longest run of consecutive losses, in resolution order —
the pain a win rate hides). A bucket states a rate only once it holds
`MIN_BUCKET` (8) scored trades; below that it shows the raw win/loss split, so a
lucky two-trade streak never reads as a strategy. `scan_once.digest_text` prints
them in the daily digest, so a quiet regime or side shows itself instead of
hiding inside the overall rate.

### Notifications on the phone

`mobile/app.js` never calls `new Notification()` for its own alerts: Android
Chrome throws on the constructor, so the alert silently did nothing on the
phone. `showNotification()` posts the message to the service worker, whose
`message` handler calls `self.registration.showNotification` — the one path
that works on both desktop and Android, and it survives the page being
backgrounded. The worker also has a `push` handler and a `notificationclick`
handler that focuses the app. The page falls back to the constructor only where
there is no worker. Bump the shell cache in `sw.js` whenever the worker changes,
or the old one keeps being served.

CI runs the `bot` suite too (alerts, watchdog, scanner); before, only tracker,
advisor and the paid-tool layer were covered, so a broken alert could ship green.

### News for free: keyless RSS, no provider quota

The advisor grounds its read in real headlines via **Google News RSS** — no API
key, no quota, no billing. `fetch_news()` parses `news.google.com/rss/search`
for `gold price XAUUSD when:1d`, and `recent_news()` caches the result for
`ASTRA_ADVISOR_NEWS_TTL` (900s) because the feed barely moves. Headlines land in
the snapshot as `news`, `news_available`; the model sets `news_used` and the app
shows it ("استخدم الأخبار"). Knobs: `ASTRA_ADVISOR_NEWS=0` disables it,
`ASTRA_ADVISOR_NEWS_QUERY` / `_LIMIT` / `_TTL` tune it, `ASTRA_ADVISOR_NEWS_URL`
repoints it. Every fetch is best-effort: a dead feed yields no headlines and the
advisor honestly reports `news_used: false` rather than inventing news. (Kitco
and FXStreet RSS both 404/403 from a datacenter IP; Google News does not.)

### Research: the feature store and the promotion gate

`tracker/features.py` turns each replayed trade into a flat context row (regime,
session, weekday, RSI, ATR scale, H4 agreement) with **no lookahead** — every
feature is computed from bars up to the entry bar only. `tracker/validation_gate.py`
is the one standard a change must clear before it may touch the engine: the
**Wilson 95% lower bound** of the win rate must clear break-even on **both** the
in-sample (2021-23) and out-of-sample (2024-26) halves, with positive net R and
`min_n` scored trades per half. Thin samples report `INSUFFICIENT`, never a pass.

`research/features_real/build_store.py` builds the store from the real corpus
(`--tf` picks the bar size), `study.py` gates every candidate condition, and
`timeframe_study.py` repeats it for H1/H4. The engine is untouched: this is
read-only analysis. Store output (`feature_store*.jsonl`) is gitignored and
reproducible.

Verdict (2026-10): nothing clears the gate. M15 (live) loses in both halves; H1
turns positive out-of-sample but fails in-sample; on H4 the aligned conditions
are positive R on both halves but below the Wilson bar. No session/weekday/
direction/regime filter has an edge alone. Full write-up:
`docs/research/PLAN_A_FEATURE_STORE_AND_GATE.md`.

