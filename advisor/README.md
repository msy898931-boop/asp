# AI advisor (isolated)

A second opinion on XAUUSD from an external model (Gemini), kept strictly
outside the validated system.

## Why it is separate

The engine's out-of-sample edge is ~0 (AUC 0.50). An external model given the
same inputs adds no measured edge either, so this service never claims one. It
exists to add what the engine cannot see — context and news — and to record
itself so it can be scored later.

- Its own process on its own port (8792). If it dies, nothing else does.
- Reads the backend API and the bridge over loopback only. It never calls into
  the engine and never writes to the prediction ledger.
- Every advisory is stamped `validated: false` and logged to
  `run/advisor_advisories.jsonl` for later scoring.
- Without a provider key it still answers with the factual snapshot and
  `configured: false`, so the UI degrades instead of erroring.

## Run

Groq is the default provider (global free tier):

```bash
GROQ_API_KEY=gsk_... python3 advisor/ai_advisor.py --port 8792
```

Gemini is opt-in (its free tier is region-restricted):

```bash
ASTRA_ADVISOR_PROVIDER=gemini GEMINI_API_KEY=... \
  python3 advisor/ai_advisor.py --port 8792
```

Mistral is opt-in too, and useful when Groq and Gemini refuse to issue a key
from your region — Mistral's sign-up is not region-locked:

```bash
ASTRA_ADVISOR_PROVIDER=mistral MISTRAL_API_KEY=... \
  python3 advisor/ai_advisor.py --port 8792
```

Override the model with `ASTRA_ADVISOR_MODEL` (default `mistral-small-latest`
for Mistral, `openai/gpt-oss-120b` for Groq). The same variables drive
`ai_engine.py`.

Mistral's free **Experiment** tier refuses some models with HTTP 429 and a
`x-ratelimit-limit-req-minute: 0` header — that is a per-model plan limit, not a
spent quota, so the key is fine and other models still answer. When it sees that
header the advisor does **not** pause the provider; it walks its free-tier
fallbacks (`open-mistral-nemo`, then `ministral-8b-latest`) and reports whichever
model actually replied in the payload's `model` field. A 429 without that header
is a real spent quota, so it backs off as before. Pin your own order with
`ASTRA_ADVISOR_MODELS` (comma-separated); it replaces the chain outright.

Started by `scripts/start_stack.sh` unless `ASTRA_DISABLE_ADVISOR=1`. The app
reaches it through the same-origin proxy at `/advisor/...`; the key stays on
the server and is never sent to the browser.

## AI engine (`ai_engine.py`)

The advisor answers with a stance and a rule-based card. The **AI engine** is a
separate, opt-in process for when you want the model itself to drive the whole
read: it is handed **every** timeframe the bridge serves (M1..MN1) plus the
headlines and the calendar, and it returns concrete trades — direction, entry,
stop, one to five targets and its own win-rate estimate — with the numbers it
read from the snapshot.

It is isolated in exactly the same way as the advisor, and off by default:

- Its own process on its own port (8794) and its own log
  (`run/ai_engine_trades.jsonl`). If it dies, nothing else does.
- Reads the bridge and the API over loopback only. It never calls into the
  engine, never writes to the ledger, and places no order of any kind.
- Every reply is `validated: false` and every trade carries
  `win_rate_validated: false`. The win-rate is the model's estimate, not a
  measured edge — the label says so wherever the number appears.

Before a trade is shown, `validate_trade` checks it is internally coherent: the
stop sits on the losing side of the entry, the targets run in the winning
direction nearest-first, the first target is beyond the entry, and R:R is
positive. An incoherent trade is **dropped with its reason** (`rejected`), never
silently repaired. The rule-based card is included as an anchor, and a trade
whose entry sits more than `FAR_ATR` (6) ATRs from the card's entry is flagged
`anchor.anchored: false` — kept, because the engine is independent by design,
but flagged so a number far from the day's range is visible.

```bash
# One read, printed to the terminal:
GROQ_API_KEY=gsk_... python3 advisor/ai_engine.py --once

# Serve it:
GROQ_API_KEY=gsk_... python3 advisor/ai_engine.py --port 8794
```

Started by `scripts/start_stack.sh` only when `ASTRA_ENABLE_ENGINE=1`; opt out
with `ASTRA_DISABLE_ENGINE=1`.

**Timeframe roles, and why the daily/weekly no longer suppress a trade.** The
engine reads every frame it is given and asks the model to weigh them by role:
the lower frames (M1..M15) time the entry, the middle frames (M30, H1) carry the
intraday trend, and the higher frames (H4, D1, W1, MN1) set the bias and the
room to move. The higher frames **inform** the direction; they do not veto it. A
setup running against the higher-frame bias is lower conviction — the model is
asked to say so in the rationale — but the trade is still given when the lower
and middle frames agree on a coherent plan. A frame that is missing or too short
(MN1 usually has fewer than `MIN_BARS`) is likewise not a reason to refuse.
Earlier the prompt let the model treat a higher-frame disagreement as grounds to
return NEUTRAL, which is why it so often returned no trades.

- `ASTRA_ENGINE_TIMEFRAMES` narrows which frames are handed to the model. Empty
  (the default) means all nine; set `M5,M15,M30,H1,H4` to drop the daily/weekly
  context entirely. Order is always canonical; unknown labels are dropped and a
  list with nothing recognisable falls back to all nine.
- The reply reports `engine.timeframes_prompted` beside `timeframes_read` and
  `timeframes_missing`, so it is always visible which frames were sent.

The **rule-based card** (`mtf_read.py`) has its own version of the same rule: it
stands aside when the structural frame (H4) opposes the lower-timeframe
agreement. That is the honest default. Set `ASTRA_MTF_STRUCTURAL_VETO=0` to let
the card through anyway, with the opposition recorded as
`card.structural_warning` and shown in the Arabic card text, instead of
suppressing it.

**The playbook, and where the win-rate number comes from.** A model's `win_rate`
is a claim; it is `win_rate_validated: false` everywhere and must never be read
as an edge. To give the user a number that *was* measured, the engine now
carries a **playbook** (`advisor/playbook.py`): a catalogue of named trading
schools, each written as an explicit rule over facts the engine already has
(higher-timeframe alignment, regime, RSI, and whether a high-impact release
fired within two hours). Each school quotes the record the project's own
research measured by replaying the real Dukascopy M15 book (2021-2025, 7084
resolved trades) through `tracker/replay.py` and scoring it with
`tracker/validation_gate.py`.

Every trade the engine returns is labelled with the school it belongs to and
that school's measured record — win rate, net R per trade, sample size, and
whether it cleared the promotion gate. The label is description, not
endorsement: **no school in the catalogue clears the gate**, and the engine says
so in the trade's `playbook.note` rather than implying an edge. The whole
catalogue is also on the reply under `engine.playbook`, and
`engine.playbook_clears_gate` is the count that did clear (currently 0).

```bash
# Print the catalogue and its measured records:
python3 advisor/playbook.py
```

The schools, and what the real replay measured. The numbers are copied
verbatim from `research/features_real/rule_report.txt` and `study_report.txt`;
"all years" rows come from the rule report, "in-sample" rows from the study
report (which splits at 2024):

| School | Rule | Measured | OOS R/trade | Gate |
| --- | --- | --- | --- | --- |
| Trend following | H4 aligned with the M15 direction | all years 36.9% win, R/trade −0.062 (n=3210) | −0.041 | reject |
| Counter-trend fade | H4 against the M15 direction | in-sample 35.0% win, R/trade −0.152 (n=1460) | −0.066 | reject |
| Post-release momentum | within 2h of a high-impact release | all years 40.1% win, R/trade +0.056 (n=409) | +0.043 | reject |
| Release + trend confluence | H4 aligned AND within 2h of a release | all years 43.8% win, R/trade +0.117 (n=208) | +0.046 | reject |
| Regime-filtered trend | M15 regime is TRENDING_UP/DOWN | in-sample 35.9% win, R/trade −0.111 (n=1306) | −0.055 | reject |

The baseline book for reference: 36.2% win, R/trade −0.080. The confluence
school is the strongest candidate (in-sample n=140, R/trade +0.188, permutation
p=0.0067) but its in-sample Wilson lower bound (39.1%) still misses break-even
(39.9%) and the sample is small, so it is a "revisit with more data", not a
green light. The measured table lives in `advisor/playbook.py`; a test
(`advisor/test_playbook.py`) re-reads the two reports and fails if any number
drifts, so the catalogue cannot quietly diverge from the research.

| Route | Meaning |
| --- | --- |
| `GET /v1/engine` | Current engine read (cached). `?refresh=1` forces a new one. |
| `GET /health` | Liveness and whether a key is configured. |

## SMC/ICT engine (`smc_engine.py`)

A third isolated analyst for a **Smart Money Concepts / Inner Circle Trader**
read. It is split in two, so the model interprets numbers it did not invent:

- **`smc_read.py` — the fact module.** Deterministic, closed-bar only. It
  computes swing pivots, market structure (BOS and CHoCH), liquidity pools
  (equal highs/lows) and **sweeps** (a wick beyond a level that closes back
  inside — a stop-hunt, not a break), fair-value gaps, order blocks, and
  premium/discount against the recent range, plus session ranges (Asian, London,
  New York) from bar timestamps. No network, no model, no wall clock: same bars
  in, same numbers out.
- **`smc_engine.py` — the engine.** Hands that fact sheet to the same external
  model and asks it only to interpret it — which structure is in play, where the
  point of interest is, and what setup follows. The model never authors a price.

It is isolated exactly like the advisor and the AI engine: its own process on
port 8795, its own log (`run/smc_engine_plans.jsonl`), loopback reads only, no
ledger write, no order. Every reply is `validated: false` and each plan carries
`confidence_validated: false`. Before a plan is shown, `validate_plan` checks it
is coherent — stop on the losing side, targets in travel order, first target
beyond the entry, and reward:risk at or above `MIN_RR` (**2.0**, the
methodology's own floor). An incoherent plan is **dropped with its reason**
(`rejected`), never repaired.

```bash
# One read, printed to the terminal:
GROQ_API_KEY=gsk_... python3 advisor/smc_engine.py --once

# Serve it:
GROQ_API_KEY=gsk_... python3 advisor/smc_engine.py --port 8795
```

Started by `scripts/start_stack.sh` only when `ASTRA_ENABLE_SMC=1`; opt out with
`ASTRA_DISABLE_SMC=1`. `ASTRA_SMC_TIMEFRAMES` narrows which frames the fact
sheet is computed over (empty = all).

| Route | Meaning |
| --- | --- |
| `GET /v1/smc` | Current SMC read (cached). `?refresh=1` forces a new one. |
| `GET /health` | Liveness and whether a key is configured. |

## Endpoints

| Route | Meaning |
| --- | --- |
| `GET /v1/advisor` | Current advisory (cached). `?refresh=1` forces a new one. |
| `GET /v1/card` | The model-free rule-based trade card. |
| `GET /health` | Liveness and whether a key is configured. |

## Test

```bash
python3 -m unittest discover -s advisor -p "test_*.py"
```
