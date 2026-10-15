# Plan A — Feature store + validation gate (the honest "learn from mistakes" loop)

## What this is

A backtest can always be tuned until it looks good, so "the backtest improved"
is not evidence that a change should touch live. This work builds the two
pieces that make learning from mistakes *honest* rather than *self-deceiving*:

1. **A feature store** (`tracker/features.py`, `research/features_real/build_store.py`):
   every trade the live rules already took, recorded with the context it opened
   under — regime, session, weekday, RSI, ATR scale, higher-timeframe agreement.
2. **A promotion gate** (`tracker/validation_gate.py`): the single standard a
   change must clear before it is allowed near the engine. It is deliberately
   hard to satisfy.

## The gate (why it is hard)

A change is promoted only when **all** of the following hold:

- It is scored **in-sample** (2021–2023) **and out-of-sample** (2024–2026).
- On **both** halves, the **Wilson 95% lower bound** of the win rate clears the
  break-even the reward:risk implies — not the point estimate, the pessimistic
  end of the interval.
- On **both** halves, net R is positive and the sample is large enough
  (`min_n`, default 100 scored trades per half).

A candidate that looks good in-sample but not out-of-sample is rejected as
curve-fitting, with the specific failing test named. A candidate too thin to
judge is reported `INSUFFICIENT`, never as a pass.

## Result (2021-01 → 2026-10, XAUUSD, live M15 rules)

Store: **7,084 resolved trades**. Nothing clears the gate.

| Timeframe | Baseline (all trades) IS / OOS | Best condition | Verdict |
|-----------|-------------------------------|----------------|---------|
| M15 (live) | 35.8% / 36.7% win, −414R / −156R | none | **fail** |
| H1 | 36.1% / 39.2% win, −59R / +31R | TRENDING_UP OOS 43.0% | fail (IS below BE) |
| H4 | 37.7% / 37.6% win, −2R / +2R | aligned +TRENDING: +14.7R / +11.8R | fail (Wilson low < BE) |

Findings, stated plainly:

- **M15 is net-losing in both halves.** The live timeframe has no edge.
- **H1 comes closest**: out-of-sample it turns positive (+30.8R, 39.2% win) and
  `TRENDING_UP` reaches 43.0% OOS — but the in-sample half is still below
  break-even, so the gate correctly refuses it. It is a *lead*, not an edge.
- **On H4, two conditions are positive R in both halves** — `H4 aligned`
  (+10.0R / +8.1R) and `aligned + TRENDING` (+14.7R / +11.8R). They are the only
  conditions that make money on both halves, but their Wilson lower bounds
  (~33.5% / ~34.2%) sit below break-even (~38%), so the gate rejects them: a
  ~40% win rate on ~200 trades is not yet distinguishable from break-even. This
  is the single most promising thread to pull next — with more trades, not a
  looser standard.
- **No session, weekday, direction or regime filter clears the gate on its own.**
  The losses are spread across all of them, which is what a no-information
  process looks like — not a hidden pocket of edge.

## How to reproduce

```bash
python3 research/features_real/build_store.py                 # M15 store
python3 research/features_real/study.py                       # gate every condition
python3 research/features_real/timeframe_study.py --tfs 3600 14400
python3 -m unittest discover -s tracker -p "test_*.py"        # 44 tests
python3 -m unittest discover -s research -p "test_*.py"       # 26 tests
```

Reports: `study_report.txt` (M15), `timeframe_report.txt` (H1/H4).

## Honest conclusion

This does not make the system profitable, and it was never going to by itself —
the gate cannot manufacture information that is not in the data. What it does
is end the guessing: it shows, with an out-of-sample test, that the M15 rules
have no edge, that a longer bar is the only lever that moves the needle, and
that the H4-alignment signal is the one thread worth pulling next. Every future
change now has to pass the same gate, so a good-looking backtest can no longer
quietly become a live loss.

**Next step (not done here):** grow the H4-aligned sample — either by pooling
more years, or by testing H4-alignment as a *filter on H1* (which has the larger
sample) — and re-run the gate. If it still clears both halves, only then does it
earn a paper-trading trial before any live change.
