# Privacy Policy / سياسة الخصوصية

**Last updated / آخر تحديث:** 2026-10-21
**Applies to / تنطبق على:** ASTRA (the Telegram bot, the mobile/desktop app, and
the web terminal at `astra-xlp9.onrender.com`).

> This document describes what the software actually does. Every claim below
> was checked against the source in this repository. Where the software collects
> nothing, it says so; where it stores something, it names the file and the
> machine it sits on.

---

## 1. The short version

- **We do not run an account system.** There is no email, no password, no
  profile, no name.
- **We do not sell, rent, or share your data.** There is no advertising and no
  analytics tracker in the app.
- **The app is read-only.** It never places a trade and never touches a
  brokerage account.
- **The only personal data we hold is your Telegram chat id and your device's
  push endpoint**, and only if you opt in. Both are stored on the server that
  runs the bot, not in the cloud.

---

## 2. What we collect, exactly

| Data | When | Why | Where it is stored |
| --- | --- | --- | --- |
| **Telegram chat id** | Only when you send `/subscribe` | To send you the alerts you asked for | `telegram_subscribers.json` on the bot server |
| **Push subscription** (an https endpoint + two public keys) | Only when you tap *Enable notifications* in the app | To deliver a push alert to your device | `push_subscriptions.json` on the app server |
| **Access tier** (free/pro) | When your tier is set | To know which limits apply to you | `entitlements.json` on the bot server |

That is the complete list. There is no other personal data in the system.

**What we do *not* collect:** no name, no email, no phone number, no location, no
contacts, no photos, no device identifiers beyond the push endpoint, and no
usage analytics.

**One precision on IP addresses.** The app reads the caller's IP address for one
purpose only: to rate-limit wrong access-key guesses (after 10 failed tries in
5 minutes the address is refused for 15 minutes). That address is held **in
memory only** — it is never written to disk, never logged to a file, and is
forgotten once the cool-down passes. It is not used to identify you and is not
combined with anything else.

---

## 3. What we do *not* do

- No advertising, no ad networks, no tracking pixels.
- No third-party analytics (no Google Analytics, no Sentry, no Facebook pixel).
- No selling, renting, or sharing of data with anyone.
- No user profiling or behavioural tracking.
- No account system, so there is nothing to "log in" to and no password to leak.

---

## 4. Market data and third-party services

To show you prices and news, the server fetches **public** market data. These
requests carry no personal data about you; they are made by the server, not by
your device, and they identify only the server:

| Service | Purpose |
| --- | --- |
| `api.gold-api.com`, `api.kraken.com` | Gold and crypto spot prices |
| `news.google.com`, `nfs.faireconomy.media`, `economic-calendar.tradingview.com` | News and the economic calendar |
| `api.telegram.org` | Delivering your bot messages and alerts |

The AI analysis engine (optional) sends **only the market snapshot** — prices,
indicators, the economic calendar — to a model provider (`api.mistral.ai`, or
`api.groq.com` / `generativelanguage.googleapis.com` if configured). It sends
**no personal data**. If you do not configure a provider key, the engine stays
off and sends nothing.

---

## 5. How long we keep it

- A **chat id** stays until you send `/unsubscribe`.
- A **push subscription** stays until you disable notifications, or until the
  push service reports it expired (at which point it is dropped automatically).
- A **tier** stays until it is changed.

You can remove any of it yourself at any time (see §6). Nothing is retained
after that.

---

## 6. Your control

- **Stop the alerts:** send `/unsubscribe` in the bot. Your chat id is removed.
- **Stop the push alerts:** tap *Disable notifications* in the app, or use
  `/push/unsubscribe`. Your push endpoint is removed.
- **Everything else:** there is nothing else to remove.

Because there is no account, there is no "delete my account" step: removing the
two entries above removes everything tied to you.

---

## 7. Security

- The bridge and the internal API bind to **loopback only** (`127.0.0.1`); they
  are never exposed to the public internet.
- The public app is behind an **access key**; the terminal proxies only `GET`
  and `HEAD` requests and holds no third-party key.
- The state files above live on the server's own disk (a private volume) and are
  not committed to the repository.
- The application is **read-only with respect to money**: no code path places an
  order. A software bug cannot lose money.

No system is perfectly secure. If you find a vulnerability, report it (see
§10) rather than exploiting it.

---

## 8. Children

This app is a financial-information tool and is not directed at anyone under 18.

---

## 9. Changes to this policy

If this policy changes, the "Last updated" date above changes with it. Because
there is no account, there is no email list to notify; the current version is
always the one in this file.

---

## 10. Contact

Open an issue at <https://github.com/msy898931-boop/asp/issues>, or message the
bot operator directly through Telegram.

---

## 11. The honest disclaimer (not legal advice, but it matters)

ASTRA is **decision support in shadow mode**. It does not place trades, it does
not manage money, and its AI-generated win-rates are **the model's estimates,
not measured results** (every such number is stamped `win_rate_validated:
false`). Nothing in the app is financial advice. Markets can lose money; you are
responsible for your own decisions.

---

_This document was written by an AI agent (OpenHands) on behalf of
@msy898931-boop. It describes the software as built; it is not a substitute for
advice from a qualified lawyer in your jurisdiction._
