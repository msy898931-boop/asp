# Selling ASTRA — Stripe Payment Link setup

This is the whole payment story. There is **no payment code and no secret** in
the repo: you create one Stripe Payment Link, paste its URL into the
`ASTRA_UPGRADE_URL` environment variable, and the free/pro gate turns on. Until
you do that, everyone is treated as Pro and nothing changes.

The copy below is written to match the product's honesty rules: it sells a
**price-alert tool**, never a prediction or a signal. Keep it that way — the app
itself says "Edge: none measured", and marketing that promises more would be a
lie the code does not back up.

---

## 1. Create the product

In the Stripe dashboard → **Product catalog** → **Add product**, enter:

| Field | Value |
| --- | --- |
| **Name** | `ASTRA Pro — gold price alerts` |
| **Description** | `Live gold price alerts in Telegram. Set a price, get told when it is crossed. No predictions, no signals.` |
| **Pricing model** | Recurring |
| **Price** | `$19.00` / month |
| **Billing period** | Monthly |

Add a second price on the same product if you want an annual option (e.g.
`$190.00` / year — "two months free").

## 2. Create the Payment Link

Product → **Create payment link**. Then:

**Collect the buyer's Telegram chat id.** This is what lets you activate them.
Under **After payment → Collect customer details**, add a **custom field**:

| Field | Value |
| --- | --- |
| **Type** | Text |
| **Label** | `Telegram chat id` |
| **Optional** | No (make it required) |

Ask buyers to get their id by sending `/myid` to the bot; the bot replies with
the number. Put that instruction in the link's **custom message** so it shows
before payment:

```
Open the Telegram bot and send /myid. Paste the number it replies with below,
so we can switch on your Pro access.
```

**Confirmation page.** Set **After payment → Confirmation page** to the app URL
so a paid buyer lands back on the product, e.g.
`https://your-service/`. In the confirmation message write:

```
You're on Pro. Open the bot and send /plan — it should read "pro".
Access is switched on within a few minutes of payment.
```

**Cancel / refund policy.** Stripe requires a public policy; a one-liner is
fine:

```
Cancel anytime from the link in your receipt. Refund on request within 7 days.
```

**Copy the link.** It looks like `https://buy.stripe.com/xxxxxxxx`. That is the
value for `ASTRA_UPGRADE_URL`.

## 3. Switch the gate on

Set the variable on the host and redeploy:

```sh
# Render dashboard → Environment → Add
ASTRA_UPGRADE_URL=https://buy.stripe.com/xxxxxxxx
```

```sh
# Fly
fly secrets set ASTRA_UPGRADE_URL=https://buy.stripe.com/xxxxxxxx
fly deploy
```

```sh
# Docker / VPS
# put ASTRA_UPGRADE_URL=... in .env, then:
docker compose up -d --build
```

Verify: open `https://your-service/pricing.html` — the **Get Pro** button now
points at your Stripe link (`/upgrade.json` returns the URL). In the bot, a
fresh chat sending `/plan` should read `free` and show the upgrade line.

Optional tuning (defaults shown): `ASTRA_FREE_LEVELS=2`,
`ASTRA_PRO_LEVELS=50`, `ASTRA_PRO_CHATS=` (comma-separated always-Pro ids).

## 3b. Multi-user accounts (recommended once you have more than one buyer)

The single `ASTRA_ACCESS_TOKEN` is fine for one operator, but it cannot be
revoked or expire, and every visitor shares it. For real customers, switch on
per-user accounts:

```sh
# Render dashboard → Environment
ASTRA_ACCOUNTS=1
ASTRA_ACCOUNTS_FILE=/root/.astra/accounts.json   # on the persistent disk
```

Then mint one key per paying customer. The key is shown **once**; only a salted
hash is stored, so it cannot be recovered — re-issue if lost:

```sh
python3 mobile/accounts.py create --email buyer@example.com --days 30
# created acc_ab12cd  email=buyer@example.com  tier=pro  expires=2026-11-24
#
#   key (shown once — copy it now, it cannot be recovered):
#   astra_acc_ab12cd.K7f2...Qo5A
```

Send that key to the buyer. They open the app at
`https://your-service/?k=astra_acc_ab12cd.K7f2...Qo5A` once; the app stores a
cookie and never asks again. Manage accounts with:

| Command | What it does |
| --- | --- |
| `python3 mobile/accounts.py list` | every account, its tier, status, days left |
| `python3 mobile/accounts.py extend --id acc_ab12cd --days 30` | renew |
| `python3 mobile/accounts.py revoke --id acc_ab12cd` | cut access now |
| `python3 mobile/accounts.py restore --id acc_ab12cd` | undo a revoke |
| `python3 mobile/accounts.py link --id acc_ab12cd --chat-id 123456` | bind to a Telegram chat |

The running server picks these up **without a restart**: it reloads the store
whenever the file changes on disk, so a `revoke` cuts access on the next
request and a `restore` gives it back just as fast. (It watches the file's
content, not its timestamp, so two edits in the same second are not missed.)

Binding a chat id makes the **bot** honour the same tier: a valid, unexpired
account turns that chat Pro automatically, and revoking it or letting it expire
turns it back to free — one place to manage, both surfaces follow. The static
`ASTRA_ACCESS_TOKEN` keeps working as a master key, so the operator is never
locked out. Accounts are off unless `ASTRA_ACCOUNTS` is truthy; leave it unset
and nothing changes.

Verify: `curl https://your-service/me.json?k=YOUR_KEY` returns
`{"mode":"account","tier":"pro","days_left":30,...}`.

## 3c. Usage metering (a daily analysis quota per account)

Accounts answer *who* may use the app and *until when*; they do not limit *how
much*. Metering adds a per-account daily request count over the model-backed
routes (`/engine/`, `/advisor/`) — the ones that spend provider tokens — so a
free tier can be genuinely limited without cutting off the price alerts. It is
off until you switch it on, and it needs accounts on to have an account to
charge:

```sh
# Render dashboard → Environment
ASTRA_METERING=1
ASTRA_METERING_FILE=/root/.astra/metering.json   # on the persistent disk
ASTRA_FREE_DAILY=20    # free-tier analyses per day; 0 = unlimited
ASTRA_PRO_DAILY=500    # pro-tier analyses per day; 0 = unlimited
```

A spent allowance answers `429` with a JSON body the app shows as its own
state ("daily analysis quota spent, resets at …"), distinct from "engine
unreachable". The reset is the next UTC midnight. Two deliberate choices worth
knowing:

- **It fails open.** If the meter's file cannot be read or written, the request
  is allowed and a warning goes to stderr. Locking a paying user out over a disk
  hiccup is worse than granting one free request; the access gate still stands
  in front of every route regardless.
- **The operator is never metered.** The static `ASTRA_ACCESS_TOKEN` is exempt,
  so the master key always works.

Inspect or clear counters with:

| Command | What it does |
| --- | --- |
| `python3 mobile/metering.py show` | today's usage per account |
| `python3 mobile/metering.py reset --id acc_ab12cd` | clear one account's counter |
| `python3 mobile/metering.py reset` | clear every counter |

## 4. Activate each purchase

Stripe emails you the payment with the buyer's chat id in the custom field. Turn
their access on either way:

- **From an admin chat** (an id in `ASTRA_PRO_CHATS`):
  `/grant <chat_id> pro`
- **By config:** append the id to `ASTRA_PRO_CHATS` and redeploy.
- **With accounts on (§3b):** `python3 mobile/accounts.py create --email ...
  --chat-id <chat_id> --days 30` — one key that opens the app *and* the bot, and
  that expires on its own.

Buyers confirm with `/plan` (reads `pro`) and can set more price levels.

## 5. The plan page copy

`mobile/pricing.html` carries this and is written in **Arabic** (RTL) to match
the bot and the product's market. Edit it if you change the price or wording. It
shows Free vs Pro, an explicit **"what it does / what it does not do"** block,
and a "How to subscribe" list, and the buy button reads its URL from
`/upgrade.json`. Keep the footer disclaimer:

> ASTRA alerts you to price levels you choose. It does not predict prices and
> does not place trades. Nothing here is financial advice.

The honesty block names the measured result on purpose — calibration is
excellent but discrimination is near chance (AUC ≈ 0.50). Saying it up front is
what makes the "price-alert tool, not a signal service" pitch credible.

## 6. What NOT to promise

These would break the honesty contract the code enforces:

- "Profitable signals", "buy/sell alerts", "predicts the market" — there is no
  measured edge (OOS 2025, AUC ≈ 0.50).
- "Guaranteed", "risk-free", performance numbers you cannot reproduce.
- Anything implying ASTRA trades for the user. It never does.

Sell the tool for what it is: it watches a price you name and tells you when it
moves. That is a real, useful, honest product.
