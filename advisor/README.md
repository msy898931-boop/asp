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

Started by `scripts/start_stack.sh` unless `ASTRA_DISABLE_ADVISOR=1`. The app
reaches it through the same-origin proxy at `/advisor/...`; the key stays on
the server and is never sent to the browser.

## Endpoints

| Route | Meaning |
| --- | --- |
| `GET /v1/advisor` | Current advisory (cached). `?refresh=1` forces a new one. |
| `GET /health` | Liveness and whether a key is configured. |

## Test

```bash
python3 -m unittest discover -s advisor -p "test_*.py"
```
