# ASTRA Telegram bot

Operator-facing Telegram bot for the ASTRA backend. It reads the same
frontend-facing loopback API the desktop UI reads (`127.0.0.1:8790/api/v1`)
and never reaches runtime internals. It is a read-and-notify surface: it has
no execution authority and never places trades.

Standard library only — it runs next to the MT5 Python bridge with no extra
dependencies.

## What it does

- **Commands** (polled with `getUpdates`; the live set is `BOT_COMMANDS` in
  `astra_bot.py`, registered with Telegram via `setMyCommands`):
  - `/start` — start and show the menu
  - `/price` — live gold price
  - `/news` — the world's news and its gold read right now
  - `/level` — add a price level to watch
  - `/levels` — list your levels and their state
  - `/clearlevels` — delete all your levels
  - `/status` — system state and mode
  - `/card` — multi-timeframe trade card (the advisor's model-free read)
  - `/engine` — the isolated AI analysis engine's latest read (model estimates)
  - `/plan` — your plan and limits (tier-aware)
  - `/myid` — your chat id
  - `/subscribe` / `/unsubscribe` — push alerts on/off
  - `/help` — command list
- **Reply keyboard**: deliberately short — four essentials only
  (`/price`, `/engine`, `/card`, `/help`). Every other command stays reachable by
  typing it, through the `/` menu and `/help`, so a smaller keyboard costs no
  function. A phrase the keyboard once showed still maps to its command
  (`BUTTON_COMMANDS`), so typing the Arabic label works even after its button is
  gone.
- **Push alerts** (checked every `--interval` seconds): health transitions,
  MT5 bridge online/offline, new recorded signals, and — via `NewsWatch` —
  the two moments that matter around a high-impact release: the blackout
  window opening (a release is imminent) and a release printing (with the
  mechanical gold read). The first reading is primed silently, so starting the
  bot does not spam the chat, and each event alerts at most once.

The news read is shared with the web terminal and the advisor: `news_engine`
assembles the digest (window, schedule, tape, headlines) from the same
`calendar_feed` / `news_window` pieces, so the bot and the page cannot disagree.
`/news` renders it; the monitor hands the same per-cycle calendar fetch to the
blackout logic, the level alerts and `NewsWatch`.

`/engine` reads the **isolated AI analysis engine** (`advisor/ai_engine.py`) on
its own loopback port (8794). That engine is opt-in — `start_stack.sh` starts it
only with `ASTRA_ENABLE_ENGINE=1` — so a missing one is expected and `/engine`
says so plainly instead of failing. Every number it returns is the model's own
estimate (`validated: false`); the reply labels the win-rate as such and closes
with the shadow-mode line. Reading it can spend the model's token quota, so it
is an explicit request and is never polled.

`/smc` reads the **isolated SMC/ICT analysis engine**
(`advisor/smc_engine.py`) on port 8795, opt-in with `ASTRA_ENABLE_SMC=1`. Unlike
`/engine` and `/card`, the same read is also **pushed** — see below.

## Push alerts

Beyond commands, the monitor thread broadcasts state changes to every
subscribed chat. Each watch is edge-triggered: it primes silently on the first
reading, so starting the bot never dumps a backlog, and each event alerts at
most once.

| Alert | Source | Throttle |
|-------|--------|----------|
| Health transition | backend `/health` | on change |
| MT5 bridge online/offline | bridge status | on change |
| New recorded signal | backend analysis | on new timestamp |
| Price level crossed | your `/level` entries | once per level |
| Volatility crosses threshold | `volatility.py` | on re-arm |
| News blackout opens / release prints | `NewsWatch` | once per event |
| Pre-news prep plan | `ForecastWatch` | once per event, ~1h out |
| New advisor trade card | `TradeCardWatch` | on a moved structure |
| **New SMC/ICT setup** | `SmcWatch` | on a moved structure, at most once per `ASTRA_SMC_ALERT_INTERVAL` |

### The SMC/ICT push (`SmcWatch`)

`/smc` is on-demand; `SmcWatch` turns the same read into a push, so a setup the
engine finds reaches you without polling for it. Two rules keep it quiet:

- **Confidence floor** (`ASTRA_SMC_ALERT_MIN_CONFIDENCE`, default `0.5`). Only a
  plan the model *scored* at or above the floor is pushed. A plan the model left
  unscored is dropped rather than assumed confident — the honest default when
  the number is missing. `/smc` still shows every plan, scored or not.
- **Materiality** (`ASTRA_SMC_ALERT_MATERIAL`, default `0.5`). A plan is keyed on
  direction + stop + targets, never the entry (which is the freshest close and
  drifts by cents each bar). A same-direction re-read is only news when the
  structure moved by at least this much of one risk. A change of *direction* is
  exempt: a reversal is pushed at once.

The engine read costs model tokens, so it is **not** polled on the ordinary
cycle. `ASTRA_SMC_ALERT_INTERVAL` (default `900` seconds) sets the floor between
reads: at most 96 reads/day at the default, and only the confident, moved ones
become messages. Set `ASTRA_SMC_ALERTS=0` to keep the engine on but silent, or
`--no-smc-alerts` on the command line. The three knobs have matching flags:
`--smc-alert-interval`, `--smc-alert-min-confidence`, `--smc-alert-material`.

The push is read-only and carries no execution authority, like every other
alert here.

## Run

```bash
export TELEGRAM_BOT_TOKEN=...      # bot token from @BotFather
export TELEGRAM_CHAT_ID=...        # default chat to notify
python3 astra_bot.py
```

Flags (all optional; env vars are the defaults):

| Flag | Default | Meaning |
|------|---------|---------|
| `--token` | `$TELEGRAM_BOT_TOKEN` | bot token |
| `--chat` | `$TELEGRAM_CHAT_ID` | default chat id; auto-subscribed |
| `--api` | `http://127.0.0.1:8790/api/v1` | backend API base |
| `--interval` | `15` | backend poll interval (seconds) |
| `--state` | `~/.astra/telegram_subscribers.json` | subscriber store |
| `--no-card-alerts` | off | disable advisor trade-card pushes |
| `--no-forecast-alerts` | off | disable the pre-news prep push |
| `--no-smc-alerts` | off | disable SMC/ICT setup pushes |
| `--smc-alert-interval` | `900` | seconds between SMC engine reads |
| `--smc-alert-min-confidence` | `0.5` | lowest model confidence worth pushing |
| `--smc-alert-material` | `0.5` | minimum structure move, in units of risk |

The backend must be running first (`aura_backend_host --dev-system-python`).

## Test

```bash
python3 -m unittest test_astra_bot -v
```

Tests cover formatting, command routing, the subscription store, and the alert
state-machine against recorded API payloads. No network is used: the API and
Telegram transports are in-process fakes that implement the same calls the
production objects make.

```bash
python3 test_astra_connection.py
```

This second suite is the one that exercises the real transports. It starts
loopback servers — the canonical `scripts/mock_api.py` backend, a bridge
answering the candle envelope, and a recording Bot API — and drives the
production `urllib` clients through them. It catches what the fakes cannot: a
contract rename, a bridge envelope read with the wrong key, or a Bot API call
Telegram would reject. Nothing reaches the network.

## Security

- Reads only the loopback API; never binds a public socket.
- The token is read from the environment and is never logged.
- No execution authority: messages are informational and always carry the
  shadow-mode disclaimer.
