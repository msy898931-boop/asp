# Open decisions — answer these before any code

Nothing gets built until each item below has a decision. Items are ordered by
how much they block.

## D1 — Where the plan lives (RESOLVED)

Resolved without operator action: the plan ships as an **additive folder on a
new branch** in this repo.

| | |
| --- | --- |
| Branch | `plan/astra-cloud-commercial` |
| Path | `docs/astra-cloud/` |
| Effect on `main` | none — new files only, nothing changed or deleted |

Background: the agent token is a GitHub App token (`openhands-ai`) scoped to
selected repositories, so it can neither create repositories nor push to one
outside that selection. Working inside `asp` removes the dependency entirely.

Nothing further is needed here.

## D2 — Product boundary

Confirm the line in `PLAN.md` §4.1:

- [ ] We sell a **volatility/risk filter** and never a direction signal.
- [ ] We keep the "Edge: none measured" honesty rule even where it costs sales.
- [ ] No token/credit economy, no auto-trading, ever.

If you want to cross this line, say so explicitly — it changes the legal
category, the payment rails, and the whole plan.

## D3 — Scope of this work (RESOLVED)

Resolved: **plan + docs only, on a branch**. When approved, product code lands
as new modules in this repo rather than a separate one — the science, tests and
engine are already here, so a second repo would only add release coupling.

- [x] Plan lives in `docs/astra-cloud/` on `plan/astra-cloud-commercial`
- [ ] Product code location (decide at build time): new top-level module vs.
      under an existing package

## D4 — Money rail, phase 1

- [ ] **A.** Crypto only (USDT TRC-20) — fastest, works today, smaller market.
- [ ] **B.** Wait for a foreign entity before charging anyone.
- [ ] **C.** Free-only for now; no payments until Phase 5.

Which one? And: **do you already have a crypto wallet** you would receive into?

## D5 — Entity plan (Phase 5)

- [ ] Turkey · [ ] Georgia · [ ] UAE · [ ] Not yet decided
- [ ] Do you have (or can you get) a partner/director in that country?

## D6 — Reuse strategy (simplified)

Now that the work lives inside `asp`, the reuse question collapses: the engine,
the research code and the 597 tests are **already in this repo**. New product
code can import them directly.

- [ ] **A.** Import the research/engine modules directly (recommended — no
      duplication, no version skew).
- [ ] **B.** Still factor a pinned internal package for a clean boundary.

No external dependency is needed either way.

## D7 — First thing to build

My recommendation is **quality gates first** (`ruff` + `mypy` + coverage), as
the assessment rated it high-impact / low-effort and it makes every later
refactor safe. Confirm or override:

- [ ] Quality gates first
- [ ] Multi-tenant auth first
- [ ] Volatility product surface first
- [ ] Something else: ______

## D8 — Language of the product

`SELLING.md` notes the existing pricing page is **Arabic (RTL)** to match the
bot and market. For ASTRA Cloud:

- [ ] Arabic-first · [ ] English-first · [ ] Both from day one

## D9 — Name

Is **"ASTRA Cloud"** right, or do you want the commercial product to have its
own brand separate from `asp`?

- [ ] Keep ASTRA Cloud
- [ ] New name: ______

---

_Questions prepared by an AI agent (OpenHands) on behalf of @msy898931-boop._
