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
