# Research sources

Every claim in `PLAN.md` traces to a source below. Measured figures come from
the `msy898931-boop/asp` repository, read in this session.

## A. Internal evidence (from `msy898931-boop/asp`)

| Claim | Source file | What it says |
| --- | --- | --- |
| Volatility AUC 0.688 / 0.729 / 0.794 at horizons 4 / 16 / 96 | `research/PHASE0_VOLATILITY_DECISION.md` | Measured on 113,083 rows, OOS 2025 |
| Volatility AUC stable by year (0.646–0.729) | same | 2021–2025, horizon 16 |
| Direction has no edge (0.49–0.51) | same | Momentum and reversal on the base rate |
| "0.517 directional accuracy was drift, not skill" | same | OOS up-move base rate = 0.517 |
| 7,084 resolved trades; nothing clears the gate | `docs/research/PLAN_A_FEATURE_STORE_AND_GATE.md` | M15 −414R IS / −156R OOS |
| News-window finding; pre-release is worst (−0.148 R/trade) | `docs/research/PLAN_B_HISTORICAL_NEWS.md` | 27,398 events, 2,147 days |
| Combined rule REJECTED (perm p = 0.0067) | `docs/research/PLAN_C_PRESPECIFIED_RULE.md` | Wilson lower bound fails |
| 84.5/100; "strong engineering tool, not yet a product" | `docs/ASSESSMENT_2026-10-16.md` | Independent re-assessment |
| 597 Python tests, 19 C++ binaries, CI green | same | Measured in that session |
| No linter / type checker / coverage | same | Named as the clearest gap |
| Static key gate; 2 APK downloads, 0 desktop | same | Delivery gap |
| Read-only by construction; no code path places a trade | `README.md` | Design invariant |
| Pricing $19/mo, honesty rules | `docs/SELLING.md` | Existing selling doc |

## B. Syria sanctions relief

| Claim | Source |
| --- | --- |
| E.O. 14312 (2025-06-30) revoked six Syria EOs | OFAC — "Sanctions and Export Controls Relief for Syria" |
| Syrian financial institutions removed from SDN list | OFAC FAQ 1221 (updated 2026-08-24) |
| 31 CFR Part 542 removed from the CFR | Federal Register, 2025-09-25 |
| Caesar Act repealed | Thompson Hine — NDAA FY2026 |
| SST designation rescinded 2026-08-24 | US State Dept — "Syria Sanctions" |
| Residual list-based sanctions remain | OFAC — PAARSS program page |

## C. Crypto in Syria

| Claim | Source |
| --- | --- |
| Legal grey area; no permitting or forbidding law | Lightspark — "Is Crypto Legal in Syria? 2026" |
| AML/CFT under Legislative Decree No. 33 (CCMLFT); no VASP framework | same |
| Exchanges began offering services after sanctions relief | same |

## D. Payment rails

| Claim | Source |
| --- | --- |
| Stripe remains unavailable in Syria; no timeline (Sept 2026) | Unblock Syria — Stripe page |
| PayPal does not support Syria | PayPal supported-countries lists (2026) |
| Lemon Squeezy: Syria on unsupported countries | Lemon Squeezy docs — Supported Countries |
| Lemon Squeezy prohibited products (crypto, get-rich-quick, gambling) | Lemon Squeezy docs — Prohibited Products |
| Stripe Connect payouts unavailable to ~30 countries | Getly — Gumroad alternatives analysis |
| "No PayPal or Payoneer; all banned" | LA Progressive — "Sanctions Lifted, But Syrians Still Struggle" |
| MoR crypto/stablecoin support is rare | Fungies — MoR platforms with crypto (2026) |
| USDT TRC-20 lowest fees / widest acceptance | CollectNExchange — USDT guide 2026 |

## E. Market

| Claim | Source |
| --- | --- |
| Risk-management software ~$41.4B (2024) → ~$87.2B (2032), ~9.75% CAGR | Data Bridge Market Research, via TradeTally |
| 54% of FS executives: risk management more critical in volatility | PwC 2026 Market Volatility Survey |
| Only 53% of consumers trust AI financial tools in volatility | same |

## F. Things we checked and could not confirm

| Item | Status |
| --- | --- |
| Direct documented link between `botpocket.biz` and Pocket Robot AI | **Not found.** The domain is 3 weeks old, WHOIS-redacted, Cloudflare-blocked to automation, absent from search and archives. The link is a strong circumstantial inference (name, funnel shape, timing), not a proven fact. Stated as inference, not fact. |
| Whether Paddle will pay out to a Syria-registered entity | **Unknown.** Registration is possible; payout was not confirmed. Treat as unverified. |
| Whether a Turkish/Georgian entity can onboard Paddle/Stripe without a local director | **Unknown.** Requires local counsel. |

---

_Sources gathered by an AI agent (OpenHands) on behalf of @msy898931-boop.
Verification before acting on any legal or financial point is the user's
responsibility._
