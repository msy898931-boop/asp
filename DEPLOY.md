# Deploying ASTRA as an always-on service

One container runs everything the phone and the bot need:

- the backend host (C++), which supervises the **MT5 bridge** and, when a bot
  token is present, the **Telegram bot**;
- the **phone app** (PWA), served on the public port and reverse-proxying
  `/api/v1` and `/bridge` to the loopback backend.

Live market data comes from **gold-api.com** (spot) + **Kraken PAXGUSD**
(candles) over the network — no key, no Windows terminal. The service is
read-only by construction: it never executes trades, and the API/bridge stay
bound to loopback.

## What you need

- A container host account (Fly.io or Render both have a free/cheap tier).
- The Telegram bot token and the operator chat id (ask the bot owner; they are
  not in the repo). Set them as **secrets** on the host — never commit them.

## Option A — Fly.io

```sh
fly launch --no-deploy            # edit `app` in fly.toml to a name you own
fly secrets set TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
fly deploy
```

`fly.toml` keeps the machine **always on** (`min_machines_running = 1`,
`auto_stop_machines = false`) because the bot long-polls Telegram: a sleeping
machine would drop messages. A persistent volume keeps the bot's subscriber
list across deploys.

## Option B — Render

The live instance runs this way. Push this repo, then in the Render dashboard
create a **Blueprint** (it reads `render.yaml`) or a new **Web Service** pointing
at this Dockerfile. Render injects `$PORT`; the container binds it
automatically. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in the dashboard
(the blueprint marks them `sync: false`, so they are never written to git). To
turn on the AI advisor tab, also set `GROQ_API_KEY` to a free Groq key — without
it the tab honestly says "not configured".

**Free plan caveat:** a free web service sleeps after 15 minutes idle, so the
phone app cold-starts (~1 min) on the next open. The repo ships a keep-alive
(`.github/workflows/astra-keepalive.yml`) that pings `/api/v1/health` every 5
minutes to keep it awake, so the app stays warm; the bot never depended on this
because it runs on GitHub Actions. Do not attach a persistent disk to a free
service (disks require a paid plan).

**One instance per bot token.** Running the same token on two services (or a
duplicate service pointing at the same repo) makes Telegram answer
`409 Conflict` and both pollers starve. Suspend or delete the duplicate.

## Option C — any Docker host / VPS

```sh
cp .env.example .env      # fill in the two values
docker compose up -d --build
```

The compose file publishes `8790:8080` and a named volume for the subscriber
list, and restarts the container unless stopped.

## After deploy

- **Phone app:** open `https://<your-service>/` and add it to the home screen.
  The APK is offered at `/astra.apk` (falls back to the rolling release when no
  local build is staged).
- **Bot:** message it and send `/status` — it should answer with the live
  reading (mode, source, symbol).

## Turning on the paid tier

The free/pro gate stays dormant until `ASTRA_UPGRADE_URL` is set, so deploy
first and sell later. To sell, create a Stripe Payment Link and set that one
variable — see **docs/SELLING.md** for the exact product, custom field, and
confirmation-page copy.

## Notes

- The platform's own health check hits `/api/v1/health`, which only returns
  `200` once the backend is up, so the platform will not route to a cold app.
- Do not run two instances of the same bot token at once: Telegram returns
  `409 Conflict` and both pollers fight. Scale to exactly one instance.
