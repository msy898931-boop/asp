# SMC zone study on H1 — is the M15 verdict an artefact of the frame?

> **VERDICT: `NO_EDGE_MEASURED` — the M15 result holds on H1.** Moving the
> decision frame from M15 to H1 changes the sample, the cadence and the
> resolution bars, but not the conclusion: deterministic zone plans still do
> **not** clear break-even on either half, and the Wilson lower bound still sits
> **below** break-even in-sample *and* out-of-sample.

- Script: `research/smc_zone_study.py --tf H1` (stdlib only, no network)
- Data: Dukascopy XAUUSD M1, `research/data/xauusd_m1/2021..2025.csv.gz`
- Split: in-sample 2021-2024, **out-of-sample 2025** (the tracker's own boundary)
- Cost: 0.30 USD/oz round-trip, matching `tracker/replay.py`
- Raw output: `research/reports/smc_zone_study_h1.json`

## Why a second frame

The M15 study (`smc_zone_study.md`) found no edge. One honest objection to any
single-frame result is that it may be a property of *that frame* rather than of
the zones: M15 is noisy, so a zone plan there might be dragged under by
intra-bar chop that a slower frame would filter out. This run answers that
directly. It re-runs the identical method — the same fact sheet, the same plan
builder, the same resolver, the same cost and split — with H1 as the **decision
frame**: H1 structure bias and H1 zones build the plan, and H1 bars resolve it.
If the zones carried an edge on a cleaner tape, H1 is where it should show.

## The ruler is still straight (control)

The symmetric 1R/1R control, entered at the market and resolved on the same H1
bars, must resolve near 50%. It does:

| Strategy | Half | Scored | Win rate | Wilson low | Break-even | R/trade |
|---|---|---|---|---|---|---|
| control | in-sample | 21798 | 0.486 | 0.479 | 0.535 | -0.097 |
| control | out-of-sample | 5396 | 0.491 | 0.478 | 0.515 | -0.049 |

48.6-49.1% is a fair coin plus spread drag. The resolver is not biased on this
frame either, so the SMC result below is real and not an artefact of the
accounting.

## The zones still do not clear break-even

| Strategy | Half | Scored | Win rate | Wilson low | Break-even | R/trade | Total R |
|---|---|---|---|---|---|---|---|
| SMC | in-sample | 7962 | 0.230 | 0.221 | 0.264 | -0.047 | -411.7 |
| SMC | out-of-sample | 1969 | 0.237 | 0.219 | 0.256 | +0.024 | +51.0 |
| confluence | in-sample | 6815 | 0.239 | 0.229 | 0.264 | -0.012 | -92.4 |
| confluence | out-of-sample | 1651 | 0.237 | 0.218 | 0.256 | +0.034 | +62.0 |

The **Wilson lower bound** is below break-even in every row, exactly as on M15.
The 2025 half again has a faintly positive point estimate and again its Wilson
low (0.219) sits under its break-even (0.256) — the same "not distinguishable
from a losing rate" reading, on a different frame. The confluence filter once
more does not turn the reading positive with any confidence.

## M15 against H1

| | M15 | H1 |
|---|---|---|
| decision frame | 15 min | 1 hour |
| window / step | 220 bars / 8 | 150 bars / 2 |
| control (OOS) | 0.493 | 0.491 |
| SMC win (OOS) | 0.244 | 0.237 |
| SMC Wilson low (OOS) | 0.225 | 0.219 |
| SMC break-even (OOS) | 0.264 | 0.256 |
| verdict | `NO_EDGE_MEASURED` | `NO_EDGE_MEASURED` |

The two frames agree to within a point on every column that matters. The
conclusion is a property of the zones, not of the frame they were read on.

## What this means for the engine

Nothing changes in the product: the engine's `MEASURED_RECORD` still cites the
M15 study, because M15 is the frame the engine actually reads. What this run adds
is **robustness**: the honest negative result is not a one-frame accident, so the
`NO_EDGE_MEASURED` stamp is that much harder to dismiss. The M15 report remains
the record of the frame in production; this one is the cross-check.

## Reproduce

```
python3 research/smc_zone_study.py --tf H1
python3 -m unittest research/test_smc_zone_study.py
```
