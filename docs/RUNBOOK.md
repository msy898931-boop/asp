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

Check it: `curl -s localhost:8790/api/v1/health` should show
`"aggregate":"ONLINE"`. Give the bridge ~10 s to warm before the first candles
call, or the first response can be `NOT_FOUND`.

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

## 6. Free-tier reality

A free Render web service sleeps after ~15 min idle: the app cold-starts
(~1 min) and the bot is silent while asleep. That is fine for "open the app when
I want it"; it is **not** a 24/7 bot. For a bot that always replies, use a
non-sleeping tier (Fly always-on, or a paid Render instance). Do not attach a
persistent disk to a free service.

## 7. Phone app and Android

- **PWA:** open the service URL and "Add to Home Screen". The service worker
  caches the shell but always fetches `/api/` and `/bridge/` live.
- **APK:** offered at `/astra.apk`; asks for the server address on first launch.
  The `android/` shell has no Gradle project in-repo yet.

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

**Conclusion.** The 185 OHLC-shape features carry no exploitable forward
direction for XAUUSD at any tested horizon. The one field that beats the naive
0.5 Brier reference — longer horizons — does so purely because gold drifted up
in 2025: the model's accuracy never exceeds the majority class (edge +0.0000),
and it scores *worse* than simply predicting the base rate. This is the
`horizon_diagnostic.py` lesson: **always score against the base-rate forecast,
not only the 0.5 reference.** The system is honest and calibrated; it is not, on
this data, predictive. A real edge needs information the corpus does not contain
(order-book/tape imbalance) or a stationary target the drift cannot game.

## 10. Secrets

`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are host secrets. `.env` is for a
local Docker run and is not committed; `.env.example` shows the shape. The repo
contains no secret values.
