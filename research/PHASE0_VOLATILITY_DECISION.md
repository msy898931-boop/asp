# Phase 0 — volatility-target decision (GO)

**Question.** T27 found directional accuracy 0.517 at a one-bar horizon, which
is a coin flip. Before building anything on top of that, this phase asked
whether the *size* of the next move is predictable when its *sign* is not.

**Verdict: GO.** Volatility is strongly and stably predictable; direction is not.

## Results

`research/horizon_probe.py` on the decision-grade corpus
(`real_corpus_2021_2025.json.gz`, 113,083 rows), out-of-sample = 2025.

| horizon | minutes | vol Spearman | **vol AUC** | dir base | dir momentum | dir reversal |
|---|---|---|---|---|---|---|
| 1   | 15   | 0.000 | (undefined) | 0.517 | 0.490 | 0.510 |
| 4   | 60   | 0.388 | **0.688** | 0.532 | 0.492 | 0.508 |
| 16  | 240  | 0.455 | **0.729** | 0.554 | 0.499 | 0.501 |
| 96  | 1440 | 0.619 | **0.794** | 0.590 | 0.504 | 0.496 |

Stability — vol AUC within each calendar year at horizon 16:

| 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|
| 0.646 | 0.669 | 0.655 | 0.668 | 0.729 |

The edge holds in every year. It is not a 2025 regime artifact.

## What this means

1. **Direction has no edge.** Momentum and reversal both sit on the base rate
   (0.49–0.51). The direction baselines are coin flips, confirming T27.
2. **Direction's 0.517 was drift, not skill.** The OOS up-move base rate at
   horizon 1 is 0.517 — *identical* to T27's headline directional accuracy.
   T27's "0.517" was the market drifting up, not the model predicting it. This
   is the most useful thing the probe found.
3. **Size has a large, stable edge.** Trailing volatility ranks forward
   volatility with AUC 0.69–0.79. That is a real, usable signal.

## The product this justifies

A **"do not trade now" volatility filter**: when trailing volatility is in its
top band, the bot says the next window is likely violent and to stand aside.
This is honest (it never claims direction), useful (it prevents trading into
spikes), and backed by AUC 0.73 rather than a coin flip.

## What to build next (Phase 1+)

1. A calibrated volatility-band classifier (reuse `run_calibrated`; the label is
   "forward vol above the trailing median"). Report ECE and coverage, not
   accuracy alone.
2. Feed it to the bot as a filter, and fold the news-quiet window (the level
   feature's "interpretation 2") into it rather than as separate code.
3. Re-derive the transparency page numbers from the volatility model, replacing
   the AUC 0.50 directional story with the AUC 0.73 volatility story.

## Reproduce

```
python3 research/horizon_probe.py
python3 -m unittest discover -s research -p "test_*.py"
```
