# SMC exit study — at which target do the zones clear break-even?

> **VERDICT: `EDGE_AT_NEAR_TARGET`.** The committed zone study asked whether the
> zones clear break-even with the shipped 2R/3R geometry, and found they do not.
> This study asks the narrower question that finding implies: *at which target do
> they?* Resolving the **same** deterministic plans to a **0.5R** final target
> clears break-even in-sample, out-of-sample, and in **each of the five years
> separately** — while a geometry-matched control entered at the market with the
> same risk **fails in every year**. The zone's *location* is worth about twelve
> points of win rate. That is the first measured edge this school has shown.

- Script: `research/smc_exit_study.py` (stdlib only, no network)
- Data: Dukascopy XAUUSD M1, `research/data/xauusd_m1/2021..2025.csv.gz`
- Split: in-sample 2021-2024, **out-of-sample 2025**
- Cost: 0.30 USD/oz round-trip, matching `tracker/replay.py`
- Raw output: `research/reports/smc_exit_study.json`

## What was measured

`research/smc_zone_study.py` builds each plan deterministically (nearest order
block or gap on the structure-bias side, stop one ATR behind the zone) and gives
it targets at **2R and 3R**. A plan counts as a win only when the *final* target
is hit, so the win rate is 21-24% against a break-even of 25-28%.

But a zone is a place price **reacts**, not a place it **travels from**. So this
study rebuilds the identical plans — same direction, same entry, same stop, same
resolver, same spread — and changes only the final target. The comparison
isolates the exit and nothing else.

## The target decides the outcome

| Target | Half | Scored | Win | Wilson low | Break-even | R/trade |
|---|---|---|---|---|---|---|
| **0.50R** | in-sample | 8934 | 0.786 | **0.778** | 0.751 | +0.0504 |
| **0.50R** | out-of-sample | 2188 | 0.778 | **0.760** | 0.703 | +0.1132 |
| 0.75R | in-sample | 8847 | 0.653 | 0.643 | 0.644 | +0.0124 |
| 0.75R | out-of-sample | 2174 | 0.645 | 0.625 | 0.602 | +0.0746 |
| 1.00R | in-sample | 8744 | 0.546 | 0.536 | 0.564 | -0.0377 |
| 1.00R | out-of-sample | 2155 | 0.544 | 0.523 | 0.527 | +0.0350 |
| 1.50R | in-sample | 8533 | 0.413 | 0.403 | 0.452 | -0.0865 |
| 2.00R | in-sample | 8280 | 0.329 | 0.319 | 0.377 | -0.1048 |
| 3.00R | in-sample | 7884 | 0.228 | 0.219 | 0.283 | -0.1117 |

At **0.5R** the Wilson lower bound sits **above** break-even on both halves.
Every target from 1R out sits **below** it in-sample. The edge is real but it is
a *near* target; asking the plan for 2R/3R is what made it lose.

## The control: it is the location, not mean reversion

A 0.5R target with a 1R stop wins `0.5/1.5 = 66.7%` on a random walk. So "78% >
67%" is not yet an edge — the question is whether the **zone entry** beats a
**market entry** on the same tape, targets and resolver.

| Target | Arm | Half | Scored | Win | Wilson low | Break-even | R/trade |
|---|---|---|---|---|---|---|---|
| 0.50R | **zone** | in-sample | 8934 | 0.786 | 0.778 | 0.751 | +0.0504 |
| 0.50R | control | in-sample | 22171 | 0.645 | 0.639 | 0.772 | -0.1901 |
| 0.50R | **zone** | out-of-sample | 2188 | 0.778 | 0.760 | 0.703 | +0.1132 |
| 0.50R | control | out-of-sample | 5501 | 0.646 | 0.633 | 0.712 | -0.0990 |

The control lands on the random-walk baseline (64.5% ≈ 66.7%, minus spread), which
confirms the resolver is not biased. The zone arm clears it by **~13 points** — so
the edge is the *location*, not gold's tendency to mean-revert. A second control
with the **same risk in USD** entered at the market (rather than the 1-ATR
control) tells the same story.

## It holds in every year

| Year | Zone @ 0.5R | Wilson low | Break-even | Verdict | Matched control |
|---|---|---|---|---|---|
| 2021 | 0.788 | 0.771 | 0.758 | **CLEARS** | 0.650 vs 0.755 — fails |
| 2022 | 0.770 | 0.753 | 0.749 | **CLEARS** | 0.678 vs 0.746 — fails |
| 2023 | 0.790 | 0.772 | 0.763 | **CLEARS** | 0.669 vs 0.759 — fails |
| 2024 | 0.796 | 0.779 | 0.735 | **CLEARS** | 0.651 vs 0.732 — fails |
| **2025** | 0.778 | 0.760 | 0.703 | **CLEARS** | 0.660 vs 0.702 — fails |

Five of five years clear, including the held-out one, and the control fails in all
five. A finding that lived in a single year would be a coincidence; this is not.

## What this is not

- **Not a signal.** The engine's own plan builder still targets 2R/3R. This study
  is evidence about the *exit*, and the exit is not yet shipped. The next step is
  to make the builder offer a near target — as its own measured variant, with its
  own gate — not to relabel the existing plan.
- **Not independence-clean.** The main run rebuilds every 8 M15 bars and holds up
  to 32, so consecutive trades share tape and the Wilson interval is too narrow.
  A non-overlapping re-run (step = 32, no shared tape) still clears both halves
  (in-sample 0.765 vs 0.751; out-of-sample 0.728 vs 0.703), so the finding does
  not rest on the overlap — but the interval on the overlapping run is optimistic.
- **Not a promise.** 0.5R is a small target; a losing streak is still a losing
  streak, and the cost model is a fixed spread, not real slippage.
