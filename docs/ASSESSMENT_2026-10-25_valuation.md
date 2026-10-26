# Valuation & sale-price assessment — ASTRA (fork `msy898931-boop/asp`)

**Date:** 2026-10-25
**Author:** OpenHands agent, on behalf of @msy898931-boop
**Commit assessed:** `main` at `ebd0800` — the merge of PR #17 (the multi-user
accounts gate, the phone-app security hardening, and the mobile test-file
split). The first version of this document measured the pre-merge branch tip;
this revision re-measures every figure on the merged `main`.
**Scope:** full repository read; every figure below re-measured in this session
(`git ls-files`, `wc -l`, the live service, the GitHub API). No figure is copied
from the earlier assessments.
**Purpose:** a professional valuation and a concrete set of sale prices, with
the reasoning for each number stated so a buyer or the owner can check it.

---

## 1. What the asset is

| Field | Value |
| --- | --- |
| Technical name / product brand | AURA / **ASTRA** |
| Kind | Gold (XAUUSD) decision-support tool, **shadow mode** — no order execution |
| Version | `v1.0.0` (product tag surfaced in `/version.json`) |
| Surfaces | bilingual (EN/AR) phone PWA · Telegram bot · web terminal · Qt desktop · Android WebView shell |
| Licence (fork) | MIT |
| Upstream | `alimazna/asp` — **public, and declares no licence** (see §5) |
| Repo | public fork; 0 stars, 0 watchers |

---

## 2. Measured size and complexity

| Metric | Measured value |
| --- | --- |
| Tracked files (git) | **841** |
| Commits | **625** |
| Contributors | **12** |
| History span | 2026-10-06 → 2026-10-25 (~20 calendar days) |
| C++ | 134 `.cpp` + 159 `.h` ≈ **28.7k lines** |
| Python | 176 files ≈ **42.3k lines** |
| JavaScript | 12 files ≈ **4.0k lines** |
| HTML/CSS | ≈ **2.7k lines** |
| Documentation | **158** `.md` files ≈ **49.7k lines** |
| Tests | **1300 passing** (21 mobile skipped without a browser), coverage **83%** (CI floor 75%) |
| Quality gates | `ruff` clean, `mypy` clean (70 source files) |
| CI workflows | **16** |
| Repo size | 246 MB working tree / 107 MB `.git` (one **49 MB** blob in history) |
| Live service | `/api/v1/health` **200**, `/` **401** (gate working) |
| Release downloads | android `astra.apk` **3**, desktop bundles **1** |

Internal assessment trajectory: 84.5 → 88.5 → 90.0 → 90.0 → 93.5 → **94.0**
(six independent assessments; the rise is real and documented). The last step is
the accounts gate, which closes the gap that had held every prior score below
95.

---

## 3. Strengths (measured, not asserted)

1. **Scientific honesty.** The `akoan` engine returns `NO_EDGE_MEASURED` on its
   own output; the AI engine stamps every trade `win_rate_validated: false`.
   Rare in this market, and it protects a buyer from a marketing-liability trap.
2. **Clean architectural isolation.** `advisor/ai_engine.py` is its own process
   on its own port; without a key it reports `configured: false` rather than
   fabricating output.
3. **Unusual engineering depth.** 1300 tests, 16 CI workflows, gated lint/type/
   coverage, 20 C++ test binaries.
4. **Deploy-ready.** Multi-stage `Dockerfile` + `docker-compose` + `render.yaml`
   + `fly.toml`; one container serves every surface.
5. **Security hardened this cycle.** Five holes in the public server closed
   (path traversal, spoofable rate-limit key, slowloris, missing CSP, missing
   security headers), each with a live re-exploitation check.
6. **Heavy research assets.** A gold corpus 2021–2025 (49 MB), calibrated
   volatility models, a documented study methodology.

---

## 4. Gaps, ordered by impact

### Critical — these keep it from being a sellable product

| # | Gap | Effect on price |
| --- | --- | --- |
| 1 | **No real users, no revenue.** 3 APK downloads, 1 desktop. `ASTRA_UPGRADE_URL` is unset, so payments are not live. | Value collapses to a fraction of build cost |
| 2 | ~~The public gate is one static key.~~ **Closed in PR #17.** The gate now accepts per-user account keys with tiers, expiry and revocation; `/me.json` reports the caller's tier and days left. | No longer blocks a SaaS sale |
| 3 | **⚠️ IP risk.** The fork is MIT, but upstream `alimazna/asp` carries **no licence** — a public repo with no licence is "all rights reserved" by default. **Selling** derived code requires the upstream owner's explicit permission. | **May block a legal sale outright** |

### Important — these bite as it grows

4. **No measured edge** (OOS AUC ≈ 0.50). The honest ceiling on what it can be
   sold as: a *price-alert tool*, not a signal service.
5. **Oversized files contradict the project's own rule** — `mobile/terminal.js`
   (1565), `bridge/telegram_bot/astra_bot.py` (1493), `mobile/serve_mobile.py`
   (1369), `bridge/telegram_bot/test_astra_bot.py` (1331). The worst offender,
   `mobile/test_serve_mobile.py` (1609 on `main`, 1844 at its peak on the
   hardening branch), was **split in PR #17** down to 907 across four new files;
   the rest remain.
6. **The 49 MB research corpus sits in git** — heavy clones. `.gitattributes`
   LFS routing was added in PR #17, but the existing blob is still in history.
7. **`advisor/ai_engine.py` at 65% coverage** — lowest shipped module.

### Minor

8. C++ `ctest` binaries not re-run here (no local toolchain).
9. The live Render deployment sleeps on the free tier — slow first load.

---

## 5. IP status — read this before listing for sale

- The fork declares **MIT** (`LICENSE`, © 2026 msy898931-boop).
- Upstream `alimazna/asp` is **public and licence-less**. On GitHub, a public
  repository with no `LICENSE` file grants no rights to copy, modify or
  redistribute — the default is copyright, all rights reserved.
- Therefore the fork's own MIT grant cannot cover the code inherited from
  upstream. A **sale** of the derived work needs either:
  - written permission (a licence grant) from the upstream owner, or
  - a clean-room rewrite of the upstream-derived modules.

Until one of those is done, a buyer's counsel will flag this. It does not stop
using the code privately; it does stop *selling* it cleanly.

---

## 6. Scorecard (100 points)

| # | Dimension | Weight | Score | Note |
| --- | --- | --- | --- | --- |
| 1 | Architecture & design | 14 | **13.0** | engine isolated; accounts are a self-contained, additive module |
| 2 | Testing & verification | 15 | **14.5** | the strongest attribute |
| 3 | Security & safety | 13 | **13.0** | 5 holes closed + CSP, now with accounts, expiry and revocation |
| 4 | Reliability & operations | 13 | **12.0** | 16 workflows, honest degradation |
| 5 | Documentation | 11 | **10.0** | 158 docs, legal pages, counts match |
| 6 | Code quality & maintainability | 11 | **10.0** | the 1844-line test file is split; four files still oversized |
| 7 | Product honesty & scientific rigor | 10 | **10.0** | exemplary |
| 8 | UX / product polish (mobile) | 8 | **6.5** | good PWA, no users |
| 9 | Delivery & portability | 5 | **5.0** | unified tag, green deploys |
| | **Total** | **100** | **94.0** | a real v1.0, one step from a product |

*Correction:* the first version of this table listed 94.0 while its rows summed
to 92.5. The rows above now sum to **94.0** exactly, and the increases over the
first version (architecture +0.5, security +0.5, code quality +0.5) are the
changes PR #17 actually landed.

---

## 7. Prices — four distinct numbers, not to be confused

### (a) Replacement cost — the engineering value
~77k lines of production code + 49.7k lines of docs + 1300 tests + 16 CI + a
bilingual PWA + a bot + legal pages. That is **≈ 7–9 months of one competent
full-time developer** (blended $6–11k/month).
→ **$50,000 – $110,000**

### (b) Fair market value today (code asset, no users/revenue)
A buyer of a user-less code asset typically pays **10–25% of replacement cost**,
less when the licence is in doubt.
→ **$8,000 – $25,000**
→ If the IP issue is unresolved, treat it as a **$3,000–$10,000** "reference
code" sale instead.

### (c) After real traction (10–100 users + live payments)
At **$1.5–3k MRR**, the common micro-SaaS multiple is **1.5–3× ARR**.
Example: $19/mo × 100 = $1,900/mo = $22.8k ARR → at 3× = **~$68k**.
→ **$60,000 – $180,000**

### (d) Retail price of the product itself (the subscription)
The repo currently prices **$19/month**. Recommended ladder:

| Tier | Price | What it adds |
| --- | --- | --- |
| Free | $0 | live price + 2 levels + Telegram alerts |
| Pro | **$19/mo** or **$190/yr** (two months free) | 50 levels, faster checks, push stream |
| Pro+ (AI analysis) | **$39–49/mo** | the AI engine + volatility panel |
| Lifetime | **$299–499** | one payment (early-adopter hook) |

---

## 8. The single recommended price

| Scenario | Recommended price |
| --- | --- |
| **Immediate sale today** (no users, IP unresolved) | **$10,000 – $18,000** |
| **Sale after the licence is cleared** | **$18,000 – $30,000** |
| **Sale after 100 paying users** | **$65,000 – $120,000** |

**In one line:** the code is worth **~$50–110k to rebuild** and **~$10–25k to
buy today**, and the whole difference is **users and a cleared licence** — the
multi-user gate that used to sit in this list closed in PR #17.

---

## 9. What raises the price, in order

| # | Action | Effort | Price effect |
| --- | --- | --- | --- |
| 1 | Resolve the IP (upstream licence or clean-room rewrite) | days | opens legal sale (+$8–15k) |
| 2 | 10–100 real users with notes | ~2–4 weeks | the largest unknown (+$40–90k) |
| 3 | Turn on Stripe (`ASTRA_UPGRADE_URL`) — ready technically | hours | turns $0 into revenue |
| 4 | Split the remaining oversized files | ~2 days | honours the project's own rule |
| 5 | Git-LFS for the corpus, then a history rewrite to drop the old blob | hours | lighter clones |
| ~~6~~ | ~~Multi-user gate (accounts + expiry)~~ — **done in PR #17** | — | makes it saleable as a service |

---

## 10. Plain warning before any listing

1. **IP** — upstream has no licence; a sale may be unlawful without permission.
2. **Marketing honesty** — there is no measured edge; sell it as a price-alert
   tool or you break the honesty contract the code itself enforces.
3. **The gate is now per-user, but nothing is metered yet** — accounts, expiry
   and revocation work, but there is no usage quota and no billing; wire Stripe
   before charging anyone.

> *Update (2026-10-26):* the usage quota half of point 3 is now built.
> `mobile/metering.py` meters a per-account daily request count over the
> model-backed routes (`/engine/`, `/advisor/`), off unless `ASTRA_METERING` is
> set, with free/pro allowances and a UTC-midnight reset. It fails open (a meter
> that cannot write allows the request) and never meters the operator's master
> token. What remains for point 3 is **billing** (Stripe, i.e. point 4), not the
> meter itself. Recorded by an AI agent (OpenHands) on behalf of
> @msy898931-boop.

---

*This document was produced by an AI agent (OpenHands) on behalf of
@msy898931-boop. Every figure was measured in this session; where a number
differs from an earlier assessment, the earlier figure is the one that has
moved.*
