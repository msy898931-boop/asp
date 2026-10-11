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

## 4. Activate each purchase

Stripe emails you the payment with the buyer's chat id in the custom field. Turn
their access on either way:

- **From an admin chat** (an id in `ASTRA_PRO_CHATS`):
  `/grant <chat_id> pro`
- **By config:** append the id to `ASTRA_PRO_CHATS` and redeploy.

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
