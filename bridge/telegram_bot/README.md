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
  - `/level` — add a price level to watch
  - `/levels` — list your levels and their state
  - `/clearlevels` — delete all your levels
  - `/status` — system state and mode
  - `/plan` — your plan and limits (tier-aware)
  - `/myid` — your chat id
  - `/subscribe` / `/unsubscribe` — push alerts on/off
  - `/help` — command list
- **Push alerts** (checked every `--interval` seconds): health transitions,
  MT5 bridge online/offline, and new recorded signals. The first reading is
  primed silently, so starting the bot does not spam the chat.

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

## Security

- Reads only the loopback API; never binds a public socket.
- The token is read from the environment and is never logged.
- No execution authority: messages are informational and always carry the
  shadow-mode disclaimer.
