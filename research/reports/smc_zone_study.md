# SMC zone study — do order blocks and fair-value gaps have an edge?

> **VERDICT: `NO_EDGE_MEASURED`.** Deterministic plans built from the SMC fact
> sheet's order blocks and fair-value gaps do **not** clear break-even on either
> half of the data. The Wilson lower bound sits **below** break-even in-sample
> *and* out-of-sample. This is a measurement, not an opinion, and it matches what
> the project already says about the school: these are *readings*, not a measured
> edge.

- Script: `research/smc_zone_study.py` (stdlib only, no network)
- Data: Dukascopy XAUUSD M1, `research/data/xauusd_m1/2021..2025.csv.gz`
- Split: in-sample 2021-2024, **out-of-sample 2025** (the tracker's own boundary)
- Cost: 0.30 USD/oz round-trip, matching `tracker/replay.py`
- Raw output: `research/reports/smc_zone_study.json`

## What was measured

The SMC engine's zones had never been checked for predictive value — nothing read
its plan log back. This study closes that gap without a model in the loop: at each
decision bar it computes the fact sheet from closed bars only, builds a plan
**deterministically** (enter the nearest order block or gap on the structure-bias
side, stop one ATR behind the zone, targets at 2R and 3R), and resolves it against
the bars that follow with `advisor/smc_resolve.py`.

## The ruler is straight (control)

A symmetric 1R/1R plan entered at the market must resolve near 50%. It does:

| Strategy | Half | Scored | Win rate | Wilson low | Break-even | R/trade |
|---|---|---|---|---|---|---|
| control | in-sample | 21924 | 0.492 | 0.486 | 0.579 | -0.173 |
| control | out-of-sample | 5480 | 0.493 | 0.480 | 0.534 | -0.081 |

49.2-49.3% is what a fair coin plus spread drag looks like. The resolver is not
biased, so the SMC result below is real and not an artefact of the accounting.

## The zones do not clear break-even

| Strategy | Half | Scored | Win rate | Wilson low | Break-even | R/trade | Total R |
|---|---|---|---|---|---|---|---|
| SMC | in-sample | 7885 | 0.229 | 0.220 | 0.283 | -0.111 | -999.5 |
| SMC | out-of-sample | 1960 | 0.244 | 0.225 | 0.264 | +0.008 | +18.4 |
| confluence | in-sample | 5450 | 0.230 | 0.219 | 0.282 | -0.103 | -640.9 |
| confluence | out-of-sample | 1358 | 0.247 | 0.224 | 0.263 | +0.019 | +29.2 |

Read the **Wilson lower bound**, not the point estimate — it is the honest end of
the interval, exactly as `tracker/validation_gate.py` insists. In every row it
sits **below** break-even. The 2025 half has a faintly positive R/trade, but its
Wilson low (0.225) is still under its break-even (0.264), which means a 24.4% rate
on 1960 trades is **not** distinguishable from a losing rate. A positive point
estimate on one half is not an edge; the gate exists to say so.

## Confluence is not an improvement

Filtering to plans whose direction agrees with the four-frame tally lifts the
out-of-sample R/trade from +0.008 to +0.019 and cuts the sample by 31% (1960 to
1358). The Wilson low stays below break-even either way. On this data the
confluence filter **does not** turn a losing reading into a winning one, so it is
shipped as an honest fact on the sheet and nothing more — never as a green light.

## What this means for the engine

This is the result the design anticipated, now with numbers behind it. The SMC
engine stays what it says it is: an isolated, opinionated *reading* of price
action, stamped `validated: false`, that may be switched off with
`ASTRA_DISABLE_SMC=1` without touching the validated pipeline. The value added by
this study is not a new signal — it is that the claim is now **falsifiable and
falsified**, which is the standard the rest of the project already holds itself
to.

## Reproduce

```
python3 research/smc_zone_study.py
python3 -m unittest research/test_smc_zone_study.py
```
