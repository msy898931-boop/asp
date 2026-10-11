# ASTRA — delivery runbook

ASTRA is a **read-only decision-support** system for XAUUSD. It never executes
trades; the API and bridge are loopback-only by construction. This document is
the operational handover: what runs where, how to start it, how to verify it,
and what was learned.

## 1. What the system is

Five parts, one process tree on the server:

| Part | Language | Role |
|---|---|---|
| Backend host | C++ | supervises the bridge and bot; serves the loopback API |
| Bridge | Python | market data; live (gold-api.com + Kraken PAXGUSD) or replay/MT5 |
| Telegram bot | Python | `/status` `/health` `/analysis` `/bridge` over the loopback API |
| Phone app | PWA (JS) | served on the public port; reverse-proxies `/api/v1` and `/bridge` |
| Desktop UI | Qt | separate build; not shipped in the container image |

The **back-end host** (`aura_backend_host`) resolves paths relative to its own
binary: it expects `resources/bridge/mt5_python`, `resources/bridge/telegram_bot`
and `resources/python` beside it. That is the whole layout contract.

## 2. Live now

- **Phone app / PWA:** the Render service below, opened on a phone or desktop.
- **Bot:** Telegram bot polling on the Render service (24/7 while the service
  runs; the free tier sleeps when idle — see §6).
- **Data:** gold-api.com spot + Kraken PAXGUSD candles. No API key.

Verify any deployment end to end:

```sh
scripts/check_deploy.sh https://<your-service>
```

It checks the app (`/`, expects 200), the API (`/api/v1/health` aggregate
ONLINE), a live reading (`/bridge/v1/candles`), and — when `TELEGRAM_BOT_TOKEN`
is set — whether a second poller holds the token.

## 3. Run it locally (exactly as validated)

Three scripts do the whole job — start, keep up, stop:

```sh
scripts/start_stack.sh 12000     # build resources if missing, start host + app, verify
scripts/watch_stack.sh 12000 20  # foreground keeper: restarts anything that dies
scripts/stop_stack.sh            # signals only the PIDs it recorded in run/
```

`start_stack.sh` is idempotent (a healthy tick is a no-op) and refuses to leave a
half-started stack — it waits for the API and the app, then exits non-zero if
either is not up. PID files and logs live in `run/` (gitignored). Because it uses
`setsid`, `stop_stack.sh` can signal the whole process group and force-kills
anything that ignores `TERM`.

**The bot is off by default.** A token merely existing in the environment does
not start a poller; set `ASTRA_ENABLE_BOT=1` only when this machine is meant to
own the bot. That guard exists because the deployed instance already polls the
one token — see §5.

`watch_stack.sh` checks the API and the app every interval and calls
`start_stack.sh` when either is down. Touch `run/watchdog.stop` (or send it
`TERM`) to retire it.

<details>
<summary>What the scripts run under the hood</summary>

The host finds the bridge only if the layout exists next to the binary. From the
repo root:

```sh
# one-time: give the built binary the resources it expects
mkdir -p build/resources/python
ln -sfn "$PWD/bridge"            build/resources/bridge
ln -sfn "$(command -v python3)"  build/resources/python/python3

# start the backend host (live data). Leave the token EMPTY to avoid a 409
# with the deployed bot (see §5).
TELEGRAM_BOT_TOKEN="" ASTRA_DATA_SOURCE=live \
  ./build/aura_backend_host --dev-system-python \
    --api-port 8790 --interval-ms 5000

# in another shell: serve the phone app on the public port
python3 mobile/serve_mobile.py --host 0.0.0.0 --port 12000 \
    --api-port 8790 --bridge-port 8791
```

</details>

Give the backend a few seconds to warm; until it does, `/api/v1/health` reads
`STARTING` and the first candles call can be `NOT_FOUND`.

Check it: `curl -s localhost:8790/api/v1/health` should show
`"aggregate":"ONLINE"`.

Route mapping (easy to trip on): the **public** app strips its prefix, so
`/bridge/v1/candles` becomes the bridge's own `/v1/candles`. Hit the bridge
*directly* on its own port with `/v1/candles?tf=M15&limit=1`, not
`/bridge/v1/candles` — the latter is only valid through the app's proxy.

## 4. Deploy (container host)

One container runs everything: `Dockerfile` + the entrypoint start the backend
host, then serve the PWA on `$PORT` with `/api/v1` and `/bridge` proxied to the
loopback backend.

- **Fly.io:** `fly launch --no-deploy` (edit `app` in `fly.toml`), then
  `fly secrets set TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...`, then `fly deploy`.
  `fly.toml` keeps one machine always on for the long-polling bot.
- **Render:** the current live host. Blueprint from `render.yaml`, or a Web
  Service on the Dockerfile. Set the two secrets in the dashboard. `render.yaml`
  marks them `sync: false` so they never reach git.
- **Any Docker host:** `cp .env.example .env`, fill the two values,
  `docker compose up -d --build` (publishes `8790:8080`).

Set `ASTRA_DATA_SOURCE=replay` for an offline synthetic demo, or `mt5` on a
Windows host with a live terminal.

## 5. One bot token = one poller (the 409 rule)

Telegram answers `409 Conflict` when two processes poll the same token, and both
starve. **Never run two instances against one token.** When developing locally,
start the host with `TELEGRAM_BOT_TOKEN=""` so it does not fight the deployed
bot. If you see repeated `409` in the host log, a duplicate poller exists —
find and stop it, do not ignore it.

**Check whether the bot is actually up** with a quiet-environment probe of
`https://api.telegram.org/bot<TOKEN>/getUpdates`: `ok` (no error) means nobody
is polling, i.e. the bot is **off**; `409` means a poller exists. The backend
host launches the bot only when `TELEGRAM_BOT_TOKEN` is in its environment, so a
Render service can answer `/api/v1/health` while the bot is still off — if the
secret was never set in the Render dashboard. Setting `TELEGRAM_BOT_TOKEN` (and
`TELEGRAM_CHAT_ID` for the broadcast target) there and redeploying is what turns
the always-on bot on; no code change is required.

## 6. Free-tier reality

A free Render web service sleeps after ~15 min idle: the app cold-starts
(~1 min) and the bot is silent while asleep. That is fine for "open the app when
I want it"; it is **not** a 24/7 bot. For a bot that always replies, use a
non-sleeping tier (Fly always-on, or a paid Render instance). Do not attach a
persistent disk to a free service.

## 7. Phone app and Android

- **PWA:** open the service URL and "Add to Home Screen". The service worker
  caches the shell but always fetches `/api/` and `/bridge/` live. The interface
  is bilingual (English / العربية) with an RTL layout; the choice persists in
  `localStorage`. Tabs: Status, State, Chart, Data, Bridge.
- **Honest by design:** the State tab is labelled "Market state" and always
  shows the badge *Edge: none measured (OOS 2025, AUC ≈ 0.50)* — see §9. The
  price-alert list on that tab only notifies, it does not trade; levels persist
  in `localStorage` and are checked against each new close.
- **APK:** offered at `/astra.apk`, which 302s to the rolling `android` release.
  A new APK is built and published by `build-android.yml` on any `mobile/**` or
  `android/**` push (build-tools 35, cached keystore). The baked server URL is
  `https://astra-xlp9.onrender.com/`, changeable on the device from the error
  screen. The shell has no Gradle project by design — `android/build.sh` drives
  `aapt2`/`javac`/`d8`/`apksigner` directly.

## 8. Windows desktop

The Qt desktop UI is a separate build and is **not** part of the server image.
Packaging scripts live under `packaging/`; the rolling desktop release is the
`bff7c28` line. Building the `.exe` on Linux requires a Windows toolchain or a
cross-build; it has not been produced in this environment.

## 9. Research tools (and the honest result)

Six scripts under `scripts/`, all reusing the real pipeline (`load_corpus`,
frozen labels, chronological year split, IRLS logistic, Platt calibration) so
their numbers match the published run byte for byte. Each carries a random
control.

| Script | Hypothesis | Result (OOS 2025) |
|---|---|---|
| `feature_lab.py` | calendar/session time is signal | +0.0011 ≈ random — **no** |
| `nonlinearity_lab.py` | a tree finds what linear misses | +0.0009 — **no** |
| `volume_flow_lab.py` | volume + spread are signal | **-0.0040** — hurts |
| `crossasset_lab.py` | DXY + real yield are signal | +0.0006 — **no** |
| `horizon_lab.py` | the horizon is wrong | skill rises, *looks* promising |
| `horizon_diagnostic.py` | is that skill real? | **edge +0.0000 — drift, not edge** |
| `discrimination_lab.py` | is there *any* ranking signal? | **AUC ≈ 0.50, p ≥ 0.41 — no** |

**Conclusion.** The 185 OHLC-shape features carry no exploitable forward
direction for XAUUSD at any tested horizon. The one field that beats the naive
0.5 Brier reference — longer horizons — does so purely because gold drifted up
in 2025: the model's accuracy never exceeds the majority class (edge +0.0000),
and it scores *worse* than simply predicting the base rate. The threshold-free
check agrees: AUC is ~0.50 at every horizon, so the probabilities rank up- and
down-moves no better than a coin (the lone 4h dip to 0.4885 is a wrong-signed,
multiple-comparison-fragile reversal tilt). This is the
`horizon_diagnostic.py` lesson: **always score against the base-rate forecast,
not only the 0.5 reference**, and confirm with a threshold-free measure. The
system is honest and calibrated; it is not, on this data, predictive. A real
edge needs information the corpus does not contain (order-book/tape imbalance)
or a stationary target the drift cannot game.

Reproduce and verify the published baseline at any time:

```sh
python3 scripts/verify_baseline.py   # exits non-zero if docs/FINAL_REPORT.md drifted
```

## 10. Secrets

`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are host secrets. `.env` is for a
local Docker run and is not committed; `.env.example` shows the shape. The repo
contains no secret values.

## 11. Selling the tool (tiers, upgrade URL, granting Pro)

The tool has two tiers. **Free** gives a live price and a small number of price
levels; **Pro** gives more levels and the push-alert stream. When no upgrade URL
is set, everyone is treated as Pro — the gate stays dormant so development and
CI behave as before.

Turn the gate on by setting one variable (no code change):

```sh
ASTRA_UPGRADE_URL=https://buy.stripe.com/...   # a Stripe Payment Link
```

Related knobs: `ASTRA_FREE_LEVELS` (default 2), `ASTRA_PRO_LEVELS` (default 50),
`ASTRA_PRO_CHATS` (comma-separated chat ids that are always Pro).

A Stripe Payment Link needs no server code and no secret: create it in the
Stripe dashboard, point it at the hosted `pricing.html`, and paste the URL into
`ASTRA_UPGRADE_URL`. Payment Links can collect the buyer's Telegram chat id as a
custom field, so each purchase arrives with the id to activate. Step-by-step
copy for the Stripe product, the custom field, and the confirmation page is in
**docs/SELLING.md**.

Activate a purchase by granting Pro to that chat id — either set it in
`ASTRA_PRO_CHATS` and redeploy, or, from an admin chat, run:

```
/grant <chat_id> pro
```

Users learn their own id with `/myid`. Tier state lives in
`entitlements.json` under the state dir; price levels live in
`price_levels.json`.
