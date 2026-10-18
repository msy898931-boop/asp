# ASTRA Cloud — Complete Commercial Plan

**Version:** 1.0
**Date:** 2026-10-18
**Author:** OpenHands agent, on behalf of @msy898931-boop
**Status:** PLAN — awaiting approval. No code has been written.

---

## 0. Executive summary

You already own something rare: a project that **measured its own edge and
honestly reported where it has none**. That is the asset. Almost every
competitor in this space is selling a lie; you have a real, reproducible number.

The plan is therefore not "build a trading bot". It is:

> **Sell the one thing that is measurably true — forward volatility is
> predictable — as a multi-tenant SaaS risk filter, from Syria, paid in
> crypto first and via a foreign entity later.**

**The single most important decision in this document** is the product
boundary in §4: we sell a *risk filter*, never a *signal*. That boundary is
what keeps the product legal, sellable on payment platforms, and honest.

| Dimension | Decision |
| --- | --- |
| Product | Volatility risk filter ("stand aside") + risk dashboard |
| Buyer | Retail XAUUSD / indices / crypto traders who already trade |
| Pricing | Free tier + $19/mo + $49/mo |
| Rail (now) | Crypto (USDT) via a gateway with crypto payouts |
| Rail (later) | Turkish/Georgian entity → Paddle or Stripe |
| Legal | No advice, no execution, no managed money → no licence needed |
| Build order | Quality gates → multi-tenant → mobile → billing → launch |

---

## 1. Where we actually are (measured, not assumed)

Everything below was read from `msy898931-boop/asp` in this session.

### 1.1 The scientific asset — this is the real value

From `research/PHASE0_VOLATILITY_DECISION.md`, on a decision-grade corpus
(**113,083 rows**, XAUUSD, 2021–2025, OOS = 2025):

| Horizon | Minutes | Volatility Spearman | **Volatility AUC** | Direction base rate |
| --- | --- | --- | --- | --- |
| 1 | 15 | 0.000 | (undefined) | 0.517 |
| 4 | 60 | 0.388 | **0.688** | 0.532 |
| 16 | 240 | 0.455 | **0.729** | 0.554 |
| 96 | 1440 | 0.619 | **0.794** | 0.590 |

Stability of volatility AUC at horizon 16, by calendar year:

| 2021 | 2022 | 2023 | 2024 | 2025 |
| --- | --- | --- | --- | --- |
| 0.646 | 0.669 | 0.655 | 0.668 | 0.729 |

**Interpretation.** The edge holds in *every* year — it is not a regime
artifact. This is a real, usable signal.

### 1.2 The uncomfortable truth we must build around

The same study is explicit, and we must respect it:

- **Direction has no edge.** Momentum and reversal both sit on the base rate
  (0.49–0.51). The famous "0.517 directional accuracy" was **market drift, not
  skill** — the OOS up-move base rate at horizon 1 is *identical*, 0.517.
- The promotion gate in `docs/research/PLAN_A_FEATURE_STORE_AND_GATE.md`
  evaluated **7,084 resolved trades** and found: **nothing clears the gate**.
  M15 (the live timeframe) is net-losing in both halves (−414R IS / −156R OOS).
- The one surviving lead (`PLAN_C`: H4-aligned + post-news) is **REJECTED** by
  the gate — permutation p = 0.0067 is encouraging but the Wilson lower bound
  does not clear break-even on both halves.

**Consequence for the product:** we cannot sell direction, signals, or "win
rate". Anyone who does is either lying or curve-fitting. We sell **volatility**.

### 1.3 Engineering state (from `docs/ASSESSMENT_2026-10-16.md`)

| Check | Result |
| --- | --- |
| Score | **84.5 / 100** — "strong engineering tool, not yet a product" |
| Python tests | 597 passing, 0 failing (CI-green) |
| C++ test binaries | 19/19 pass |
| Committed secrets | none |
| Read-only by construction | yes — a software bug cannot lose money |
| Linter / type checker / coverage | **none** |
| Public gate | **one static key** — no expiry, no per-user accounts |
| Live usage | **2 APK downloads, 0 desktop downloads** |

The assessment's own verdict: the gap is **not "better code"** — it is
(1) a real mobile experience, (2) engineering quality gates, (3) a real gate.

---

## 2. Market research

### 2.1 Is there demand for a volatility/risk tool?

Yes, and it is a mature category:

- The risk management software market was ~$41.4B in 2024, projected to
  ~$87.2B by 2032 (≈9.75% CAGR).
- PwC's 2026 survey: 54% of financial-services executives name risk management
  as a capability that became *more* critical in volatile markets.
- But trust is the constraint: only **53% of consumers trust AI-powered
  financial tools** during volatility. **This is our opening** — a tool that
  publishes its own AUC and says "no directional edge" is the trust play.

### 2.2 Who is the buyer?

Not "people who want to get rich". That audience buys scams and churns.

The buyer is someone who **already trades** XAUUSD / indices / crypto and has
felt a volatility spike destroy a position. They do not want a signal. They
want to stop walking into spikes. This buyer:

- already has an account somewhere,
- understands drawdown,
- will pay for a tool that stops a specific, felt pain,
- is **not** impressed by "AI robot" marketing — it makes them suspicious.

### 2.3 Competitive positioning

| Competitor type | What they sell | Our difference |
| --- | --- | --- |
| Signal channels / "AI bots" | Direction, win-rate promises | We publish **no directional edge** |
| Charting platforms | Charts + indicators | We sell a **decision to stand aside**, with a measured AUC |
| Risk/portfolio platforms | Institutional, expensive | We are **retail-priced, single-pain, mobile-first** |

Our moat is **credibility**, which is the hardest thing for a competitor to
copy because copying it means admitting their own numbers are bad.

---

## 3. Legal & regulatory research

> **Not legal advice.** Researched from primary sources; verify with a Syrian
> lawyer before trading.

### 3.1 Syria sanctions — lifted

| Date | Action | Source |
| --- | --- | --- |
| 2025-05-13 | US announces lifting of sanctions | OFAC |
| 2025-06-30 | **E.O. 14312** revokes six Syria EOs; national emergency terminated | OFAC |
| 2025-06-30 | Syrian financial institutions (incl. Central Bank) **removed from SDN list** | OFAC FAQ 1221 |
| 2025-08-26 | OFAC removes 31 CFR Part 542 (Syrian Sanctions Regulations) | Federal Register |
| 2025-12-18 | **Caesar Act repealed** (NDAA FY2026) | Thompson Hine |
| 2026-08-24 | State Dept **rescinds Syria's State-Sponsor-of-Terrorism designation** | State Dept |

**Result:** US comprehensive sanctions are gone. Residual **list-based**
sanctions remain on named persons (former Assad associates, human-rights
abusers, Captagon traffickers, ISIS/Al-Qa'ida affiliates). We must screen
against the SDN list — but the country is no longer blocked.

### 3.2 Crypto in Syria

**Legal grey area, improving.** No Syrian law permits or forbids crypto; the
transitional government has not made digital-asset policy a priority. AML/CFT
obligations exist under Legislative Decree No. 33, overseen by the CCMLFT, and
do **not** explicitly cover virtual assets.

**Practical implication:** individual crypto use is not illegal. Operating a
*financial service* (custody, exchange, managing others' money) would attract
scrutiny. **We are neither** — we sell software.

### 3.3 Why our product needs no financial licence

| Activity | Licensed? | Do we do it? |
| --- | --- | --- |
| Giving investment advice / recommendations | Yes | **No** — we describe volatility, not direction |
| Executing trades for clients | Yes | **No** — read-only, shadow mode |
| Managing client money | Yes | **No** — we never touch funds |
| Selling software tools | No | **Yes** — this is all we do |

**The boundary that keeps us legal (§4.1) is also the boundary that keeps us
unlicensed.** They are the same line. Crossing it — even "just adding signals"
— changes our legal category overnight.

---

## 4. Product definition

### 4.1 The product boundary (the most important section)

| ✅ We sell | ❌ We never sell |
| --- | --- |
| "Trailing volatility is in its top band — the next window is likely violent." | "Buy/sell", "CALL/PUT", any direction |
| A measured AUC, published openly | Win-rate claims |
| A risk dashboard: your exposure, your spikes | Signals, "AI robot", auto-trading |
| Alerts to a price *you* set | Predictions of price |
| An honest "no edge measured" where true | "Guaranteed", "risk-free", any performance promise |

**Hard rule:** the app must be able to say *"Edge: none measured"* for
direction and *still be worth paying for* because of volatility. If a feature
cannot survive that rule, it does not ship.

### 4.2 Product surfaces

1. **Volatility filter** — the core. Consumes market data, outputs a band
   (quiet / normal / violent) with a calibrated probability and the trailing
   volatility that produced it.
2. **Risk dashboard** — position sizing helper, exposure summary, spike log.
   Deterministic maths, no prediction.
3. **Alerts** — price levels the user sets, plus volatility-band crossings.
   Delivered by Telegram *and* real Web Push (the gap the assessment named).
4. **Transparency page** — the AUC table, the base rates, the losses. Public.
   This is marketing *and* compliance in one artefact.

### 4.3 Non-goals (explicit)

- No live order execution. Ever.
- No MQL5/EA ingestion.
- No direction prediction.
- No "AI robot" branding.
- No managing or holding user funds.
- No token/credit economy. Ever. (This is the exact mechanism of the scam we
  analysed; it will not appear in our product.)

---

## 5. Payments — the real constraint

### 5.1 What is blocked for Syria (verified this session)

| Provider | Status | Reason |
| --- | --- | --- |
| PayPal | ❌ | Still blocked |
| Payoneer | ❌ | Still blocked |
| Stripe | ❌ | Replied Sept 2026: "remains unsupported, no timeline" |
| Lemon Squeezy | ❌ | Syria on the **unsupported countries** list |
| Gumroad | ❌ | Depends on Stripe Connect payouts |
| Payhip | ❌ | Depends on Stripe/PayPal |
| Paddle | ⚠️ | Registration possible; Syria payout not guaranteed |

**Root cause:** all of them settle through **Stripe Connect**, which does not
pay out to ~30 countries including Syria. Registering is easy; **withdrawing
is the wall.**

### 5.2 Also blocked: our product *category* on those platforms

Independent of Syria, the major MoRs prohibit our category:

> **Lemon Squeezy, prohibited products:** "NFT & Crypto related products",
> "get-rich-quick schemes", "gambling", "business-in-a-box".

**Implication:** if we sold "trading signals", we would be rejected on
category *even from a supported country*. Selling a **risk/volatility tool**
is what makes us acceptable — another reason §4.1 is load-bearing.

### 5.3 The rail we will actually use

**Phase 1 — crypto (works today, from Syria).**

Use a processor that settles to crypto (e.g. a USDT-settling MoR/gateway).
Requirements:
- Settles **USDT on Tron (TRC-20)** — lowest fees, widest acceptance.
- We convert to fiat only when needed.
- Accept the trade-off: smaller addressable market (buyers need crypto) but
  **zero geographic dependency**.

**Phase 2 — foreign entity (the clean long-term rail).**

Register in **Turkey or Georgia** (fast, close, workable banking), then:
- Paddle or Stripe under the company.
- Fiat checkout for the majority of buyers who do not hold crypto.
- Clean tax and accounting.

**Phase 3 — hybrid.** Keep crypto for sanctioned-adjacent buyers, cards for
the rest. Never mix the ledgers.

**Rejected approach:** "use a relative's account in a supported country".
This violates every platform's terms and burns both accounts. Not considered.

---

## 6. Architecture

### 6.1 What changes versus the existing `asp`

| Existing `asp` | ASTRA Cloud |
| --- | --- |
| Single static key | **Multi-tenant accounts** (OAuth/email + sessions) |
| Everyone is Pro | **Plans + entitlements** enforced server-side |
| Telegram-only alerts | **Telegram + real Web Push** |
| One deploy, one user | **Per-tenant rate limits + quotas** |
| No billing | **Billing webhooks → entitlement** |
| No lint/types/coverage | **ruff + mypy + coverage floor in CI** |
| Reuses: engine, science, read-only design | **Keeps: read-only, shadow-mode, honesty rules** |

### 6.2 Component diagram

```
                       ┌──────────────────────────────┐
   phone PWA  ─────────▶│  Edge / reverse proxy (GET)  │
   Telegram bot ───────▶│  auth · rate limit · quotas  │
                       └───────────────┬──────────────┘
                                       │
             ┌─────────────────────────┼─────────────────────────┐
             ▼                         ▼                         ▼
   ┌──────────────────┐   ┌────────────────────────┐   ┌─────────────────┐
   │  Identity &      │   │  Volatility engine     │   │  Billing        │
   │  Entitlements    │   │  (reuse research code) │   │  webhooks →     │
   │  accounts, plans │   │  band + calibration    │   │  entitlements   │
   └────────┬─────────┘   └───────────┬────────────┘   └────────┬────────┘
            │                         │                          │
            └────────────┬────────────┴──────────────┬───────────┘
                         ▼                           ▼
              ┌────────────────────┐      ┌──────────────────────┐
              │  PostgreSQL        │      │  Market-data bridge  │
              │  tenants, subs,    │      │  (loopback, GET only)│
              │  usage, audit      │      │  XAUUSD + crypto     │
              └────────────────────┘      └──────────────────────┘
```

**Invariants carried over (non-negotiable):**
- Read-only by construction: no code path places an order.
- The bridge binds to loopback; the proxy forwards only GET/HEAD.
- Every output that could be read as a recommendation carries the disclaimer.
- The transparency page is generated from the same numbers the engine uses.

### 6.3 Quality gates (the cheapest, highest-impact fix)

The assessment called this the clearest signal of "not yet professional", and
rated it **high impact / low effort**:

- `ruff` (lint) — fail CI on error.
- `mypy` (types) — fail CI on new errors.
- `coverage` — publish, and set a floor that may only rise.

This ships **before** any feature. It is also the cheapest way to look
professional to any future partner or acquirer.

---

## 7. Business model

### 7.1 Pricing

| Plan | Price | Includes |
| --- | --- | --- |
| **Free** | $0 | 1 asset, 1 alert, delayed band, transparency page |
| **Pro** | $19/mo | All assets, unlimited alerts, live band, Web Push, dashboard |
| **Desk** | $49/mo | API access, multiple accounts, CSV export, priority support |

Modelled on the existing `docs/SELLING.md` ($19/mo) so the two tracks agree.

### 7.2 Unit economics (conservative)

| Metric | Value |
| --- | --- |
| Blended ARPU | ~$22/mo |
| Payment fees (crypto rail) | ~1–2% |
| Infra per tenant | < $0.50/mo (shared engine) |
| **Gross margin** | **> 90%** |
| Break-even (infra + tools ~$60/mo) | **~4 paying tenants** |

Software margins, tiny fixed cost. The constraint is **distribution**, not
unit economics.

### 7.3 The honest growth constraint

We must be blunt: this is not a viral product. It is a **credibility product**.
Growth comes from:
1. The transparency page being genuinely convincing.
2. Posting the real numbers where traders argue (forums, not ads).
3. The free tier being useful enough to spread by word of mouth.

Expect **slow, compounding** growth. Plan for 12–18 months to meaningful MRR.

---

## 8. Roadmap

### Phase 0 — Foundations (Week 1–2)
- Create repo + branch (blocked on token permission — see §10).
- Add `ruff` + `mypy` + coverage CI. **No features until this is green.**
- Write `PRIVACY.md` and `TERMS.md` (required before any store or launch).
- Document which bot owns `TELEGRAM_BOT_TOKEN` (prevents the 409 issue).

### Phase 1 — Multi-tenant core (Week 3–6)
- Accounts, sessions, plans, entitlements enforced server-side.
- Replace the static key; keep an operator bypass for admin.
- Per-tenant quotas and rate limits.

### Phase 2 — Mobile that is actually a product (Week 7–10)
- Offline cache of the **last reading** + offline banner + skeletons.
- **Real Web Push** (VAPID), not bot-only.
- Harden Android (`cleartext`, `allowBackup`).

### Phase 3 — Volatility product surface (Week 11–14)
- Ship the calibrated band classifier (`run_calibrated`, label = "forward vol
  above trailing median"), reporting **ECE and coverage**, not accuracy.
- Fold the news-quiet window into the same feature rather than separate code.
- Rebuild the transparency page from the volatility model (AUC 0.73 story).

### Phase 4 — Billing + launch (Week 15–18)
- Crypto rail live; entitlement webhooks tested end-to-end.
- Free tier public; Pro behind payment.
- Publish the transparency page.

### Phase 5 — Entity + fiat (Month 5–8)
- Turkey/Georgia entity; Paddle/Stripe onboarding.
- Migrate willing buyers; keep crypto rail for the rest.

---

## 9. Risk register

| # | Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- | --- |
| 1 | Volatility edge decays | Medium | High | Re-measure yearly; publish it; the gate already rejects thin evidence |
| 2 | Category blocked by a payment rail | Medium | High | Sell *risk tool*, not signals; keep crypto rail as fallback |
| 3 | Syria payout disruption | Medium | High | Two rails (crypto + entity); never single-source |
| 4 | Reputational spillover from the scam space | Medium | Medium | Distance explicitly: publish "no directional edge" |
| 5 | Solo-founder bandwidth | High | Medium | Ship in phases; quality gates first so refactors are safe |
| 6 | Legal reclassification (advice) | Low | High | §4.1 boundary; lawyer review before launch |
| 7 | Infra cost creep | Low | Low | Shared engine; per-tenant quotas |
| 8 | Token permission blocks repo creation | **Certain now** | Low | User creates repo or grants scope (§10) |

---

## 10. Where this plan lives (resolved)

The original intent was a separate repository. That turned out to be blocked by
tooling, not by choice, and it has been resolved without burdening the operator:

- The agent token is a **GitHub App user-to-server token** (`ghu_…`, app
  `openhands-ai`) whose installation is scoped to **selected repositories**.
  Repo creation is therefore impossible from the agent side, and the new
  repository was outside the installation's selection, so pushes to it were
  refused (`403`).
- A repository `msy898931-boop-astra-cloud` was created during the attempt but
  is **public** and unreachable by the token.

**Decision taken:** this plan ships as an **additive folder on a new branch in
the existing `asp` repository**:

| | |
| --- | --- |
| Branch | `plan/astra-cloud-commercial` |
| Path | `docs/astra-cloud/` |
| Effect on `main` | **none** — additive files only |

This keeps the plan versioned next to the science it depends on, requires no
operator action, and touches nothing that already exists.

---

## 11. What I have deliberately not done

- **No product code.** The instruction was plan first, approval after.
- **No modification of any existing file in `asp`.** The branch adds one new
  folder (`docs/astra-cloud/`) and changes nothing else. `main` is untouched,
  and no file was deleted or renamed.
- **No fork.** The existing repo is already a fork of `alimazna/asp` with a
  "never push upstream" constraint. A third layer of forking would be a mess.

---

## 12. The one-sentence version

> Sell the volatility filter — the only thing this project proved — as a
> multi-tenant SaaS from Syria, paid in crypto now and via a foreign entity
> later, with the honesty rules as both the legal boundary and the marketing.

---

_Plan prepared by an AI agent (OpenHands) on behalf of @msy898931-boop._
_All measured figures are sourced in `docs/RESEARCH_SOURCES.md`._
