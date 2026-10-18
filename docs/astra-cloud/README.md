# ASTRA Cloud — commercial plan

**Commercial layer for an honest volatility risk-filter.**

This folder holds the plan for turning the validated volatility edge into a
sellable, multi-tenant SaaS. It is documentation only: **no product code**, and
nothing in the existing `asp` tree is modified.

| | |
| --- | --- |
| **What it sells** | A volatility risk filter: "the next window is likely violent — stand aside." |
| **What it never sells** | Signals, direction, predictions, "profitable bot", managed money. |
| **Scientific base** | Trailing volatility ranks forward volatility with AUC 0.69–0.79 (measured, OOS 2025, 113,083 rows). |
| **Status** | Plan only. Awaiting approval. |

## Read in this order

1. **`PLAN.md`** — the complete, researched plan.
2. **`RESEARCH_SOURCES.md`** — every claim, with its source.
3. **`OPEN_DECISIONS.md`** — what must be decided before any code.

## Why a separate track

`asp` is a single-tenant, keyed, shadow-mode tool scored 84.5/100 as an
*engineering tool* — and explicitly "not yet a commercial product" (static key
gate, no accounts, no billing, thin mobile surface). Commercialising it means
adding a multi-tenant product surface on top of the science that is already
validated. This folder is that plan. It consumes `asp`'s engine; it does not
replace it.

## Branch

`plan/astra-cloud-commercial` — additive only. `main` is untouched.

---

_Plan prepared by an AI agent (OpenHands) on behalf of @msy898931-boop._
