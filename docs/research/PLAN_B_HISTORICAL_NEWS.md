# Plan B — the historical news calendar, and what it says about the losses

## The gap this closes

`AGENTS.md` stated the limit plainly: *"We have no historical calendar in the
corpus, so its effect cannot be backtested the way the volatility model's can."*
That was true of the only source then wired in — the Forex Factory mirror, which
serves **one week and no released `actual`**. The advisor's TradingView calendar
(`advisor/calendar_feed.py`) serves the *past* too, with `actual` populated, so
the gap could be filled without a key and without scraping.

`research/features_real/build_news_ledger.py` fetches it month by month and
writes **27,398 events over 2,147 days (2021 → 2026)**, 1,568 of them
high-impact, in the same append-only shape `akoan/events.py` reads — so the
existing join (`akoan/link.py`) and state machine (`akoan/state.py`) work on it
unchanged.

## The method

`news_study.py` joins the feature store (every trade the live rules took) with
the ledger, stamps each trade with its **causal** news state — only events at or
before the trade are ever consulted — and runs each condition through the same
promotion gate as Plan A: Wilson 95% lower bound clearing break-even on **both**
the in-sample (2021–23) and out-of-sample (2024–26) halves.

## Result

**Nothing clears the gate.** But the loss structure is the finding:

```
net R by news state (all years, 7,084 resolved trades)
  news in play       n=700   win=38.2%  R=     +3.8   R/trade=+0.005
  quiet (no news)    n=6384  win=36.0%  R=   -573.2   R/trade=-0.090
  after a release    n=538   win=40.1%  R=    +29.9   R/trade=+0.056
  before a release   n=170   win=33.9%  R=    -25.1   R/trade=-0.148
  blackout           n=109   win=41.1%  R=    +10.4   R/trade=+0.095
```

- **Almost all of the loss lives in the quiet window** (−573R of the −598R
  total). The news windows are net-flat-to-positive, on far smaller samples.
- **Trading before a release is the worst bucket** (−0.148 R/trade): entering
  into a scheduled print is, measurably, a bad moment. This is the one news
  rule the project already asserts; the data now agrees with it.

### The lead, stress-tested

"After a release" is positive R, so it was checked for the obvious trap — that
it is just **higher volatility** (a bigger bar makes the fixed spread a smaller
fraction of the move). Three independent checks:

1. **ATR-matched.** Inside each ATR quartile, "after a release" still beats
   "not after" — Q1 calm +0.239, Q2 +0.426, Q3 +0.148, Q4 volatile +0.039
   R/trade. The effect is *not* just volatility, and it is actually strongest
   where volatility is lowest (though those bands are thin).
2. **Split-year.** Positive R out-of-sample at split 2023 (+28.9), 2024 (+12.1)
   and 2025 (+16.1). Not an artefact of where the halves are cut.
3. **Window sensitivity.** Positive at 60/120/240 minutes and decaying smoothly
   toward zero as the window widens — the signature of a real, short-lived
   effect rather than noise.

**It still fails the gate**: its Wilson lower bound (~32.6–34.7%) sits below
break-even (~39%), because ~200 trades per half cannot separate a 40% rate from
a 39% break-even. It is a **lead**, not an edge — and the gate is right to say so.

## What this means for the engine

The honest read: **the news is not a signal to trade on, but the quiet window is
where the money is lost.** A "don't trade in the quiet" filter would, in this
data, remove the −573R and leave roughly break-even — but that is the *same*
lever as Plan A's "H4-aligned + volatility" thread wearing a news label, and
neither clears the gate yet. Nothing here is allowed near live.

## How to reproduce

```bash
python3 research/features_real/build_news_ledger.py            # 2021-2026 ledger
python3 research/features_real/news_study.py                   # gate every condition
python3 -m unittest discover -s research -p "test_*.py"        # 38 tests
```

Report: `news_report.txt`.

## Next step (not done here)

The two leads now point the same way — **high-volatility, post-news,
trend-aligned windows are where the rules are least bad.** The next honest test
is a single pre-specified rule that combines them (e.g. *trade only H4-aligned
bars within 2h after a high-impact US release*), run through the gate on both
halves. If it clears, it earns a paper-trading trial; if it does not, the answer
is that these rules have no edge at this timeframe and the search should move to
a different question entirely — which is itself a result worth having.
