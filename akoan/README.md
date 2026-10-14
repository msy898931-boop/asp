# akoan — the news ↔ statistics bridge

akoan answers one question, with numbers instead of a story:

> **Does the news explain the trade results — or not?**

It is an *addition* to ASTRA. It changes no engine, deletes no file, and writes
nothing the rest of the system reads. It takes two things that already exist —
the tracker's trade ledger and the economic calendar — joins them causally, and
reports what the statistics actually support. Most of the time that answer is
"nothing measurable yet", and akoan says so plainly.

## Why it exists

The project already publishes an honest result: the decision model has **no
demonstrated edge** (OOS Brier skill +0.0012, directional accuracy 51.7% — the
base rate). It also records, in `AGENTS.md`, that the news-quiet window "cannot
be backtested" because *"we have no historical calendar in the corpus"*.

akoan closes that gap. It builds the missing calendar, joins it to real trades,
and runs the tests that would let the news claim an effect — if one were there.
So far it is not: over 353 scored trades across the committed corpus, every
news dimension sits on the base rate and nothing survives multiple-comparison
correction. That is the honest result, and it is worth having on record.

## How it is honest

| Rule | Where it lives |
| --- | --- |
| A trade is only ever explained by news knowable **at its open** | `state.py` — only events `<= ts`; "upcoming" uses the schedule, never `actual` |
| A win rate is meaningless **without its base rate** | `stats.edge_vs_base` — every bucket is compared to the overall rate, not to 0.5 |
| A rate is not stated **below a sample floor** | `stats.MIN_N` (8), the same floor the tracker uses |
| Scanning many dimensions **finds noise** | `stats.benjamini_hochberg` — a finding must survive FDR control |
| The default answer is **no** | `engine.verdict` — `NO_EDGE_MEASURED` unless a bucket clears every bar |
| Coverage is **stated, not assumed** | `events.coverage` — every report carries how thin the evidence is |

## Pieces

```
akoan/
  stats.py    Wilson intervals, Fisher exact, BH-FDR, permutation test, base rate
  events.py   append-only event ledger (backfill + self-accrual), dedup, coverage
  state.py    the causal news state at one instant (pure, no clock, no network)
  link.py     join the trade ledger with the news state; de-duplicates trades
  engine.py   scan every dimension, correct, and reach a verdict
  study.py    the same analysis over the committed M1 corpus (months, not days)
  report.py   Arabic Telegram text and a compact JSON view
  run.py      the CLI
```

## Usage

```bash
# refresh the calendar ledger (feeds prune old rows, so run this periodically)
python3 akoan/events.py --backfill --days 120

# the live ledger: what the running tracker actually traded
python3 akoan/run.py

# the historical study: the same chain replayed over the committed corpus
python3 akoan/study.py

# send the report to Telegram
python3 akoan/run.py --send

# the weekly Telegram digest of the study (Mondays, 06:00 UTC)
python3 bot/weekly_akoan.py --dry-run
```

## On the phone

The PWA (`mobile/`) has a **News** tab that shows the same result. It fetches
`/akoan.json`, which the mobile server computes by running this engine on the
server — same code as the CLI, so the panel and the report cannot disagree. The
route is cached for ten minutes and computed behind a lock, because the corpus
replay is heavy and every phone polls.

## Weekly on Telegram

`bot/weekly_akoan.py` sends the study as one Arabic message a week (workflow
`astra-weekly-akoan.yml`, Monday 06:00 UTC). Weekly, not daily: the answer moves
slowly and a daily message would be noise. A state file in the Actions cache
records which ISO weeks were sent, so a repeated cron slot changes latency only.
It names the baseline, the per-dimension buckets, the verdict, and the coverage
caveat — never an edge on thin data.

Environment knobs: `ASTRA_AKOAN_MIN_N`, `ASTRA_AKOAN_FDR_Q`,
`ASTRA_AKOAN_BEFORE_MIN`, `ASTRA_AKOAN_AFTER_MIN`, `ASTRA_AKOAN_RECENT_MIN`,
`ASTRA_AKOAN_EVENTS`, `ASTRA_AKOAN_TRADES`, `ASTRA_AKOAN_CORPUS`,
`ASTRA_WEEKLY_AKOAN_STATE`.

## Reading the output

- `n` — scored trades in the bucket (TARGET or STOP; timeouts are carried but
  never scored, because a timeout is not a decision the news made).
- `[lo–hi]` — the 95% Wilson interval. If it straddles the base rate, the
  bucket says nothing.
- `فرق` — the gap from the base rate, which is the only number that could be an
  edge.
- `⭐` — survived Benjamini-Hochberg correction. Without it, the difference is
  noise the scan happened to find.
- `ما بينقاس` — the dimension had only one populated bucket, so there was
  nothing to compare against. This is reported, not hidden.

## What it is not

akoan is **decision support, not financial advice**, and it is not a signal
generator. It measures whether news *explains* results; it never claims to
predict direction. When the ledger is young — as it is now, with three days of
live trades — the honest output is "insufficient data", and that is the output.
