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

- A container host account. Render has a permanent free plan; Fly.io does not
  (see Option A); Oracle Cloud Always Free is the only free-forever VPS (Option D).
- The Telegram bot token and the operator chat id (ask the bot owner; they are
  not in the repo). Set them as **secrets** on the host — never commit them.

## Option A — Fly.io (paid; no free tier)

Fly.io removed its free allowances for new accounts in October 2024. A new
signup gets a trial of **2 VM hours or 7 days**, whichever ends first, and after
that an always-on machine costs roughly **$2–5/month** (shared-cpu-1x 256 MB is
about $2.02/mo, plus ~$2 for a dedicated IPv4 and a little for storage). It is a
good host if you are willing to pay for always-on; it is not a free one.

```sh
fly launch --no-deploy            # edit `app` in fly.toml to a name you own
fly secrets set TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
fly deploy
```

`fly.toml` keeps the machine **always on** (`min_machines_running = 1`,
`auto_stop_machines = false`) because the bot long-polls Telegram: a sleeping
machine would drop messages. A persistent volume keeps the bot's subscriber
list across deploys. The `Deploy (Fly.io)` workflow needs a `FLY_API_TOKEN`
repo secret; without it the job fails at `flyctl auth` (the three runs before
this note did exactly that).

## Option B — Render

The live instance runs this way. Push this repo, then in the Render dashboard
create a **Blueprint** (it reads `render.yaml`) or a new **Web Service** pointing
at this Dockerfile. Render injects `$PORT`; the container binds it
automatically. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in the dashboard
(the blueprint marks them `sync: false`, so they are never written to git). To
turn on the AI advisor tab, also set `GROQ_API_KEY` to a free Groq key — without
it the tab honestly says "not configured".

**Free plan caveat:** a free web service sleeps after 15 minutes idle, so the
phone app cold-starts (~1 min) on the next open and the interactive bot stops
answering. Cron cannot hold it awake — GitHub's 5-minute schedule lands
20–75 minutes apart here — so `.github/workflows/astra-keepalive.yml` runs
a ~5h17m job that pings `/api/v1/health` every 5 minutes and re-arms itself,
keeping the service warm around the clock. Do not attach a persistent disk to
a free service (disks require a paid plan).
service (disks require a paid plan).

**One instance per bot token.** Running the same token on two services (or a
duplicate service pointing at the same repo) makes Telegram answer
`409 Conflict` and both pollers starve. Suspend or delete the duplicate.

**Deploy on push (recommended).** `autoDeploy` depends on the dashboard
toggle and can lag — the live service once stayed on the previous commit for
about an hour across two pushes with CI green. To make a deploy deterministic,
copy the service's **Deploy Hook** URL (Render → the service → Settings →
Deploy Hook) into the repo secret `RENDER_DEPLOY_HOOK`; the
`.github/workflows/deploy-render.yml` job then triggers a deploy on every push
to `main`. Until the secret is set the job skips, so it is safe to leave in
place.

## Option C — any Docker host / VPS

```sh
cp .env.example .env      # fill in the two values
docker compose up -d --build
```

The compose file publishes `8790:8080` and a named volume for the subscriber
list, and restarts the container unless stopped.

## Option D — Oracle Cloud Always Free (free VPS, always on)

The only provider that still gives a genuinely free, always-on VPS: 2 Arm
Ampere cores + 12 GB RAM + 200 GB storage + 10 TB/month egress, at $0 for the
life of the account (Oracle halved the Arm allowance in June 2026; the AMD micro
shapes and storage are unchanged). It needs a card for identity verification and
a manual setup, but once running it is a real machine that never sleeps.

```sh
# On the VM (Ubuntu 22.04 Arm image), after allowing 8080 in the VCN security list:
sudo apt update && sudo apt install -y docker.io docker-compose-plugin git
git clone https://github.com/msy898931-boop/asp && cd asp
cp .env.example .env      # fill TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
sudo docker compose up -d --build
```

The compose stack restarts unless stopped and survives reboots, so the bot stays
up with no keepalive cron. Point `ASTRA_URL` (and the Render service, if kept)
at the VM's public IP or a domain.

## Option E — free, no card: the warm chain (shipped)

The repo is public, so Actions minutes are unmetered. The bot already runs there
on a cron, but a cron cannot hold the Render service awake (its 5-minute
schedule lands 20–75 minutes apart here, longer than the 15-minute sleep
window). `astra-keepalive.yml` solves this without a card: one job runs for
~5h17m, pinging `/api/v1/health` every 5 minutes, then re-arms itself with a
`workflow_dispatch` so the next run takes over. The service never sleeps, so the
phone app opens instantly and the interactive bot stays up. One always-on
service still fits Render's free 750 instance-hours (a month is ~730).

Nothing to configure — it runs on `main`. If it ever stops, the cron trigger
restarts the chain; if the service is upgraded to paid, the chain is harmless
and can stay.

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
