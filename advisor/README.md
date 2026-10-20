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

| Route | Meaning |
| --- | --- |
| `GET /v1/engine` | Current engine read (cached). `?refresh=1` forces a new one. |
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
