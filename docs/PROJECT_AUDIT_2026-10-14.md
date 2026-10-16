# Project audit — ASTRA (fork `msy898931-boop/asp`)

**Date:** 2026-10-14
**Scope:** full repository read — C++ engine, Python bridge/bot/akoan/advisor,
mobile PWA, CI, deployment, docs, git state.
**Method:** static read of every subsystem, the test suites executed locally,
the C++ test binaries executed, and the live Render service probed.
**Verdict:** **85.0 / 100** — a genuinely well-built, unusually honest system
that is production-capable today for its stated use, held back by a few real
gaps (single-instance deployment, ephemeral bot state, no public auth, and
some portability/branding drift).

This document is a point-in-time assessment. It adds nothing to the code; it
records what was measured so the next reader does not have to re-derive it.

---

## 1. What was measured (evidence)

| Check | Result |
| --- | --- |
| Python test suites (bot/advisor/research/akoan/mobile/tracker) | **333 passing**, 0 failures |
| bridge tests (entitlements/astra_bot/volatility/parity/mt5) | **110 passing** |
| C++ test binaries (19, prebuilt tree) | **19/19 pass** |
| C++ tests executed in CI | **Yes** — `ctest --test-dir build --output-on-failure` (19/19) |
| Committed secrets (tokens/keys/keystore) | **none** (`.env`, `*.keystore`, `*.pem` ignored) |
| Bare `except:` / TODO / FIXME / HACK | **0 / 0 / 0 / 0** |
| Live routes `/ /version.json /bot.json /vol.json /api/v1/health /upgrade.json` | all **200** |
| Live `/akoan.json` | **200 after ~101 s** cold (600 s cache thereafter) |
| Bot poller live | `{"available":true,"pid":17}` — running on Render |
| Working tree / unpushed commits | **clean / none** (all branches on origin) |
| Tracked size | ~110 MB (51 MB is the committed research corpus `.gz`) |

---

## 2. Scorecard

| # | Dimension | Weight | Score |
| --- | --- | --- | --- |
| 1 | Architecture & design | 14 | **12.5** |
| 2 | Testing & verification | 15 | **14.0** |
| 3 | Security & safety | 13 | **10.0** |
| 4 | Reliability & operations | 13 | **10.5** |
| 5 | Documentation | 11 | **9.0** |
| 6 | Code quality & maintainability | 11 | **9.0** |
| 7 | Product honesty & scientific rigor | 10 | **9.5** |
| 8 | UX / product polish | 8 | **7.0** |
| 9 | Delivery & portability | 5 | **3.5** |
| | **Total** | **100** | **85.0** |

Band: 90+ exemplary · 80–89 strong · 70–79 solid · <70 needs work.

---

## 3. Detail by dimension

### 1. Architecture & design — 12.5 / 14

**Strengths.** Clean, defensible boundaries. The system is *read-only by
construction*: the API and bridge bind to loopback, the proxy forwards only
`GET`/`HEAD`, and no code path executes a trade. The C++ host supervises the
bridge and the bot as child processes; the phone app is one origin that
reverse-proxies `/api/v1`, `/bridge`, `/advisor`, `/tracker`. Data-source
adapters (live gold-api+Kraken / replay / MT5) sit behind one closed-bar
contract, so the pipeline runs without a Windows terminal. The bot re-scores
the *same* volatility artifact with the *same* code as the phone
(`volatility.py`), so the two cannot disagree by construction — a deliberate,
good choice.

**Weaknesses.** (a) Project identity is muddled: the README titles it "AURA /
ASTRA — Backend-First Repository Build Pack" while the product is ASTRA; the
V4 master spec is a large historical artifact still at the repo root. (b) Two
files carry a lot: `mobile/app.js` (~1.5k lines) and
`bridge/telegram_bot/astra_bot.py` (~800 lines) mix transport, formatting and
state. (c) `src/` is ~35 directories and ~28.5k lines of C++ for what the
shipping product (phone + bot) barely exercises — the C++ surface is far larger
than the live product needs.

### 2. Testing & verification — 14.0 / 15

**Strengths.** This is the project's best attribute. 443 Python tests across
six suites plus the bridge, with real (not mocked) code paths — the mobile
suite spins a real `ThreadingHTTPServer`, the bot tests hit real JSON, the
akoan study reproduces over the committed corpus in CI. The container job
builds the actual image and asks the running service for `/akoan.json`, which
is exactly how the "News tab shipped blank" bug would be caught again. The
baseline job re-verifies the published research bit-for-bit. Tests assert the
*absence* of claims ("no edge measured", "n/a" over invented confidence).

**Weaknesses.** The suite is small relative to the surface: 19 targets cover the
feature engine, API contracts, bridge recovery, persistence, and startup, but
`src/` is ~28.5k lines across 35 directories, so much of the engine (regime,
scoring, risk, reconciliation) is exercised only indirectly through the
integration tests. There is no coverage measurement, so "how much is tested" is
not answerable from the repo. These are depth concerns, not a missing gate — the
suite does run in CI (verified live: the `Test` step passed on `32eeaae`).

### 3. Security & safety — 10 / 13

**Strengths.** No secrets in git (verified: no tokens, keys, or keystore;
`.env*`, `*.pem`, `*.key`, `secrets/` are ignored). Read-only, loopback-only
upstreams. Static file serving normalises the path and rejects escapes
(`target.startswith(HERE)`). `X-Content-Type-Options: nosniff` on every
response. JSON is `no-store`. The bot token/chat id live only as host secrets.

**Weaknesses.** (a) **No authentication on the public surface** — the phone
app, `/upgrade.json`, and the proxied API are open to anyone with the URL.
Acceptable for a shadow-mode personal tool; a blocker if it is sold as-is.
(b) A **mutable admin surface** (the bot's tier/level commands) shares the same
token path. (c) The API is plain `http://127.0.0.1` (fine on the host, but
nothing forces TLS at the edge). (d) Free/pro enforcement is per-chat state in
a JSON file the operator edits — fine for now, weak if it becomes a real
paywall (no server-side receipt verification; Stripe link only *grants* nothing
automatically).

### 4. Reliability & operations — 10.5 / 13

**Strengths.** Genuinely thoughtful. `/version.json` makes "is my fix live?"
one curl. The hourly watchdog is a real dead-man's switch (bot, server, engine,
poller, idle-repo) that alerts on the transition into trouble, re-alerts on a
window, and announces recovery — and deliberately never cries wolf on a 404 or
a dead connection. `check_engine` catches the 200-but-empty bug. The keepalive
cron (every 5 min) matches the free tier's 15-min sleep. `/bot.json` now makes
the interactive poller observable. The bot writes a heartbeat so a dead poller
is visible instead of silent.

**Weaknesses.** (a) **Single instance is a hard constraint** — two pollers on
one token return `409`; scaling is impossible without a webhook. (b) **State is
ephemeral on a free host** — the subscriber/entitlements/levels files live on
the container disk; a restart loses them unless a paid disk is mounted (and
Render's free tier forbids disks). (c) The free plan sleeps, so first open is a
cold start; the keepalive mitigates but depends on GitHub cron being on time.
(d) `/akoan.json` cold-computes for ~100 s. **Fixed after this audit** (commit
`4ee8ff7`): the server now warms the akoan cache in a background thread at
startup, the app bounds every request with `AbortController`, and the News panel
shows elapsed time past 20 s instead of a frozen "loading…". (e) A push does not
always redeploy: Render stayed on the previous commit for ~1 h across two pushes
with CI green — see `deploy-render.yml` (a deploy hook that makes the deploy
deterministic once the secret is set).

### 5. Documentation — 9 / 11

**Strengths.** `AGENTS.md` (~1000 lines) is an outstanding engineering memory:
it records not just *what* but *why*, including the mistakes ("do not force a
trade every bar", "never let the server sleep"). `docs/INDEX.md` cleanly
separates current from historical. `RUNBOOK.md`, `DEPLOY.md`, `SELLING.md`, and
per-module READMEs are real. Comments explain invariants, not syntax.

**Weaknesses.** (a) **Drift — fixed.** `bridge/telegram_bot/README.md` and
`RUNBOOK.md` §1 listed the old command set (`/analysis /signal /bridge`) while
the live menu is Arabic; those were corrected in `32eeaae`, and the last
offender, `AGENTS.md`, now matches the `setMyCommands` menu exactly. (b) 144
Markdown files, most historical (`AUDIT_REPORTS/`, `coordination/`,
`project-control/`) — well-indexed by `docs/INDEX.md` and referenced by the
tooling, so kept deliberately; a lot of weight to carry. (c) Root README framed
the project as an extract-and-push "build pack" — **fixed:** it now describes the
shipped system and points to `docs/INDEX.md`.

### 6. Code quality & maintainability — 9 / 11

**Strengths.** Zero bare `except:` and zero TODO/FIXME/HACK in shipping code.
Stdlib-only Python (no dependency drift in the bot/bridge). Type hints and
docstrings throughout. Consistent Arabic copy in one place (`STRINGS`,
`REASON_AR`). Failure modes are explicit (`available:false` + `reason`).
`except Exception` appears 39× but almost always to degrade honestly rather
than crash.

**Weaknesses.** (a) Large files (`app.js`, `astra_bot.py`) would benefit from
splitting. (b) The 39 broad excepts occasionally swallow detail that would help
debugging. (c) Some duplicated formatting logic between `bot/telegram_bot.py`
and the mobile app (kept in sync by hand, with a doc note — acceptable, but a
drift risk).

### 7. Product honesty & scientific rigor — 9.5 / 10

**Strengths.** This is rare and should be protected. The system states its own
limits everywhere: the volatility model predicts *size, not direction*; the
akoan engine reports `NO_EDGE_MEASURED` with `net_r: -33.44` and refuses
predictive language; confidence reads `n/a` rather than inventing a number;
"unknown is not safe" is a flag in the health payload. The repo documents a
measured negative result and chose silence over forced trades. For a
trading-adjacent product this honesty is the single most valuable property.

**Weaknesses.** Minor: the honesty is mostly *documented*; a few UI labels
(e.g. "Signal" tab naming) still flirt with predictive framing even though the
content is descriptive.

### 8. UX / product polish — 7 / 8

**Strengths.** Bilingual EN/AR with real RTL (116/116 strings translated), dark
and light themes, live ticker, installable PWA, an APK, and offline-shell
service-worker handling with a build stamp so redeploys are seen immediately.
The tab bar now shows edge fades on the overflowing side (direction-aware) and
scrolls the selected tab into view, and Arabic drops Latin letter-spacing.

**Weaknesses.** (a) Nine tabs on one phone-width bar is a lot; a
two-row or priority-ordered layout would be cleaner. (b) The News tab can spin
~100 s cold with no visible timeout. (c) No skeleton/loading states on some
panels.

### 9. Delivery & portability — 3.5 / 5

**Strengths.** Real artifacts ship: a valid `astra.apk` (manifest + dex) and a
64 MB `ASTRA-full.zip` (backend + embedded Python). CI builds Windows, Android,
and the container. Fly.io and Render configs both exist; the fork is cleanly
separated from upstream with a written decision and a hard "never push upstream"
constraint.

**Weaknesses.** (a) The deployed host (`astra-xlp9.onrender.com`) and the fork
owner (`msy898931-boop`) were hardcoded across workflows, bot defaults, the
mobile README and the `serve_mobile.py` APK fallback. **Mostly fixed after this
audit:** the bot scripts now read a single shared `bot/astra_env.py`
(`ASTRA_URL`, `GITHUB_REPOSITORY`, with the current values as defaults), the
watchdog takes `GITHUB_REPOSITORY` from Actions, `serve_mobile.py` builds the
APK link from `ASTRA_REPO`, and the Android workflow resolves the server URL
from `vars.ASTRA_URL`. What remains is the intended override-with-default
pattern (`vars.ASTRA_URL || 'https://astra-xlp9.onrender.com'` in keepalive and
watchdog; the same default in `check_deploy.sh`), which is exactly how a clone
moves host without a code change. The rest is documentary (READMEs, the audit
itself) and the `LICENSE` copyright line, which is correct as-is. (b) A second live
poller location (Fly) would collide with Render's on the same token. (c) The
free tier is a constraint the product design leans on.

---

## 4. Top fixes, ranked by value

1. **Fix the bot-command docs** (`bridge/telegram_bot/README.md`, `RUNBOOK.md`)
   to match the Arabic menu.
2. **Externalise the host + owner** (`ASTRA_URL`, `ASTRA_REPO`) with the
   current values as defaults, so a rename is config, not a code sweep.
3. **Persist bot state off the free container** (a committed "seed" or an
   external store), or document the loss on restart.
4. **Give `/akoan.json` a warm cache** (compute once at startup) and a client
   timeout, so the News tab is never a 100-second blank.
5. **Add a request timeout** to `getJson()` in `app.js` so no panel hangs.
6. **Split `app.js` and `astra_bot.py`** along transport / view / state lines.
7. **Decide the paywall posture** before selling: today the Stripe link grants
   nothing automatically and the gate is per-chat local state.

> **Correction (2026-10-14, same day).** The first draft of this audit claimed
> the C++ suite was built but not run in CI, and ranked "add `ctest`" as fix #1.
> That was **wrong**: `.github/workflows/ci.yml` has run
> `ctest --test-dir build --output-on-failure` since commit `5cb9ea7`
> (2026-10-10), and the live run on `32eeaae` shows the `Test` step passing.
> The testing dimension is corrected to 14.0 and the total to 85.0. This note
> stays so the mistake is not repeated.

## 5. Overall

ASTRA is a **strong, honest, well-tested system (85.0/100)**. Its testing,
documentation-as-memory, and scientific honesty are well above what a project
of this size usually carries, and the live service is genuinely operational
(phone app, bot, engine, watchdog all verified live). The remaining points are
concentrated in three places: the deployment is single-instance with ephemeral
state, the public surface has no authentication, and some identity/portability
strings are hardcoded to one fork. None of these is architectural; each is a
bounded, low-risk change.

---

## 6. Addendum — changes since this audit (2026-10-16)

Kept as a delta rather than rewriting the sections above, so the point-in-time
measurement stays intact.

**Authentication (security §3, previously the top weakness).** The phone
surface is no longer open. `mobile/serve_mobile.py` gained an optional access
gate: when `ASTRA_ACCESS_TOKEN` is set, every route but `/api/v1/health` and
`/unlock` requires the `astra_key` cookie, compared with `hmac.compare_digest`.
A 401 serves a phone-sized RTL page with a one-time form (the cookie is stored
for a year). `/astra.apk` is deliberately exempt — it is the same artifact as
the public rolling release, and gating it broke the in-app download. The gate is
off when the variable is unset, so dev and CI are unchanged.

**Monitoring that survives the gate.** The advisor/akoan/bot routes are behind
the gate, so the watchdog, keepalive canary, `miss_alert` and `review_digest`
would have read a 401 as an outage. All four now send
`astra_env.auth_headers()` and treat a 401/403 as "gate on, no token" — a
missing credential, not a dead engine — so they stay quiet instead of paging.

**Watchdog liveness (reliability §4).** `check_bot`'s 90-minute threshold
assumed GitHub's cron fires every 5 minutes. Measured over 96 runs, the median
gap was ~28 min and the worst 4.6 h, so the check fired false "bot offline"
alarms on every stall. Liveness now rides `check_keepalive` — the self-re-arming
keepalive chain, which pings every 5 minutes — and `check_bot` stays as a loose
(6 h) secondary signal.

**Dead weight removed.** The standalone `astra-paper.yml` (the paper observer's
earlier home, superseded by the `paper` job of `astra-bot.yml`) fired only twice
in total and was deleted.

**Documentation.** `AGENTS.md` had been saved through a CP775 round-trip; 95
lines of em dashes, section signs and Arabic direction words were mojibake and
were decoded back to UTF-8 with the ASCII skeleton unchanged.

**Watchdog and the pending run.** A keepalive successor can sit `pending` for
hours when no runner picks it up (seen live: 3.6 h with no update while the real
work happened in another run). `check_keepalive` treated any `queued`/`pending`/
`in_progress` status as a running chain, so a dead chain whose last run was
still labelled `pending` read as healthy. It now looks for an `in_progress` run
explicitly and, when none is running, lets the newest run's age decide.

**The phone's tab bar (frontend §8).** The audit flagged "nine tabs on one
phone-width bar is a lot". The bar had since grown into a two-row grid that ate
~80px of the bottom of every screen. It is now one row of six: the five views
that carry the daily read (Status, State, Chart, Vol, News) plus **More**, which
opens the other four (Signals, AI, Data, Bridge) in a sheet on top of the bar.
More reads as selected while one of its views is open, and a tap outside
dismisses the sheet.

**Zero-tap Android build.** The APK already baked in the server URL; a personal
build can now also bake in the gate key (`build.sh --key`, or the workflow's
`access_key` input) so it opens straight to the data. The publish step is
skipped when a key is set — the rolling release is a public download and must
never carry the key. A first-load race that could show the key prompt over the
saved-key auto-unlock was also fixed.

**Concurrency is not the fix for a weak edge (tracker).** A review question was
whether the one-live-idea rule in `tracker/replay.py` is too strict. A new probe,
`research/multi_trade_probe.py`, replays the same 2026 M15 bars at 1/2/3/5
concurrent ideas. It is not the fix: concurrency multiplies the trade count
roughly by N but does not raise the win rate (~31% at every level) and deepens
the loss streak (12 → 37). Mean R per trade also falls. That is the inflation the
single-idea rule prevents — the same move counted several times. The probe also
shows the live rule is itself net-negative on this window (mean R -0.042), which
is the project's own finding and a separate problem from concurrency.
