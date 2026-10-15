# Plan C — one rule, fixed before looking, and its honest verdict

## The rule

Plan A (H4-alignment) and Plan B (the news windows) pointed the same way, so
this fixes **one** rule that combines them and asks for a verdict. No threshold
was tuned; both come from the earlier studies.

> **THE RULE:** take a trade only when it is **H4-aligned** *and* within **120
> minutes after a high-impact release**.

`rule_test.py` gates it, and adds the test the gate cannot give: a **permutation
test** on the difference in R/trade between the rule's trades and the rest,
because the rule's subset is small and "INSUFFICIENT" is a real but unsatisfying
answer.

## The result

```
baseline book: 7,084 trades, win=36.2%, R=-569.4, R/trade=-0.080

RULE: H4-aligned AND within 120 min after a release
  trades taken : 282 of 7084  (4.0% of the book)
  all years    : n=208 win=43.8% R=+32.9 R/trade=+0.117
  in-sample    : n=140 R=+26.4 R/trade=+0.188
  out-of-sample: n=142 R= +6.5 R/trade=+0.046
  permutation  : diff R/trade = +0.205, two-sided p = 0.0067
  gate verdict : REJECT (win-rate Wilson lower bound below break-even on both halves)
```

Read against the two components it is built from:

| rule | trades | R/trade | permutation p |
|------|--------|---------|---------------|
| **H4-aligned AND post-news** | 282 (4.0%) | **+0.117** | **0.0067** |
| H4-aligned only | 3,624 (51.2%) | −0.062 | 0.2105 |
| post-news only | 538 (7.6%) | +0.056 | 0.0084 |

The combination is better than either half alone, and it is the only one of the
three whose improvement survives a Bonferroni correction for three tests
(threshold ≈ 0.017). H4-alignment on its own is *not* significant — the signal
is in the news window, with H4-alignment sharpening it.

## Robustness — and the honest caveat

**The window is not load-bearing.** R/trade stays positive across every window
from 30 to 240 minutes (+0.10 to +0.20), so the result does not depend on the
120-minute choice:

```
  <= 30 min  n= 87  R= +17.5  R/trade=+0.201
  <= 60 min  n=181  R= +32.1  R/trade=+0.178
  <=120 min  n=282  R= +32.9  R/trade=+0.117
  <=240 min  n=445  R= +48.1  R/trade=+0.108
```

**But it is not every year.** Three years carry it and three are flat:

```
  2021  n=21  R=  -2.4   (flat)
  2022  n=63  R= +15.9   (strong)
  2023  n=56  R= +12.8   (strong)
  2024  n=46  R=  -0.1   (flat)
  2025  n=56  R= +11.4   (strong)
  2026  n=40  R=  -4.8   (flat)
```

So the honest statement is: **a real, statistically significant lead that is
not yet a proven edge.** The permutation test says the difference is unlikely
to be chance (p ≈ 0.007); the gate says the sample is too small to claim an edge
against break-even; and the year split says the effect comes and goes. All three
are true at once, and the project's standard is to report all three.

## Verdict and what it earns

The gate returns **REJECT**, not PROMOTE, and that verdict stands — nothing here
is allowed near live. But REJECT on a *significant positive* lead is different
from REJECT on noise, and the difference matters:

- The rule trades **4% of the book** and turns a −569R loss into +33R in sample.
- It is significant after correcting for the three rules tested.
- It is robust to the one threshold it contains.

What it has **not** earned is a claim of edge. What it **has** earned is a
**paper-trading trial**: run the rule on live bars, with no capital, and see
whether the out-of-sample rate (+0.046 R/trade) holds on trades it has never
seen. That is the only honest next step, and it is cheap.

## How to reproduce

```bash
python3 research/features_real/rule_test.py      # the rule + comparators + robustness
python3 -m unittest discover -s research -p "test_*.py"
```

Report: `rule_report.txt`.
