# Independent assessment - ASTRA (fork `msy898931-boop/asp`)

**Date:** 2026-10-19
**Author:** OpenHands agent, on behalf of @msy898931-boop
**Commit assessed:** `e92eb77` ("news: ship a direction-free pre-news prep plan
across bot and phone")
**Scope:** full repository read; every Python suite executed locally; the C++
test binaries built and run; the live service probed; the release artifacts
downloaded and opened; the GitHub Actions history and the scheduled bot runs
read back from the API.
**Method:** every number below was measured in this session. Where a figure
differs from `docs/ASSESSMENT_2026-10-16.md`, the difference is called out.
**Verdict:** **88.5 / 100** (up from 84.5) - a genuinely well-built, unusually
honest system. The remaining gap to "commercial product" is no longer code: it
is **real users**, a **privacy policy / terms**, and a **unified version tag**.

Band: 90+ exemplary - 80-89 strong - 70-79 solid - <70 needs work.

---

## 1. What was measured (evidence)

| Check | Result |
| --- | --- |
| Python tests, every suite, run locally | **1129 passing, 0 failing** |
| Distribution | bot 155 - mobile 203 - advisor 158 - bridge 152 - tests 308 - akoan 59 - research 50 - tracker 44 |
| C++ test binaries (`cmake` + `ctest`) | **19/19 PASS** |
| Quality gates in CI | `ruff` clean - `mypy` clean (67 files) - `coverage --fail-under=75` (measured 79%) |
| CI on this commit | **10/10 success** - frontend, container, backend, baseline, quality, 2x build, deploy, 2x scan |
| Live service probe | `/api/v1/health` **200**; `/`, `/version.json`, `/akoan.json`, `/vol.json`, `/forecast.json` **401** (gate ON) |
| Scheduled bot (`astra-bot`) | cron every 5 min; **26/26 success**; runs `bot/paper_trade.py` with the real token |
| Release artifacts | `astra.apk` **25.9 KB, signed** (17 entries, manifest + dex + `META-INF/ASTRA.RSA`); Windows 22.4 MB + 61.3 MB |
| Release downloads (real users) | **0** for the `.apk`, **0** for Windows |
| Committed secrets | **none** (`.env`, `*.keystore`, `*.pem`, `*.key` ignored) |
| `TODO` / `FIXME` / `HACK` markers | **0** |
| Bare `except:` | **0** |
| `LICENSE` | MIT |
| Repository | 821 tracked files; one 49.9 MB research corpus in git |

---

## 2. Scorecard

| # | Dimension | Weight | Score | Note |
| --- | --- | --- | --- | --- |
| 1 | Architecture & design | 14 | **12.5** | clean boundaries; a few oversized files |
| 2 | Testing & verification | 15 | **14.5** | the project's best attribute; coverage now gated |
| 3 | Security & safety | 13 | **11.0** | read-only by construction; gate now rate-limited |
| 4 | Reliability & operations | 13 | **11.5** | excellent monitoring; a real 5-minute cron |
| 5 | Documentation | 11 | **9.5** | thorough; stale counts and one mojibake block |
| 6 | Code quality & maintainability | 11 | **9.5** | lint/type/coverage gates now present |
| 7 | Product honesty & scientific rigor | 10 | **10.0** | exemplary - "no edge" said out loud |
| 8 | UX / product polish (mobile) | 8 | **6.5** | offline cache + banner + Web Push + a real-device test |
| 9 | Delivery & portability | 5 | **3.5** | artifacts build automatically, but zero users |
| | **Total** | **100** | **88.5** | "strong engineering tool, one step from a product" |

**Delta from the 2026-10-16 assessment (84.5).** Four dimensions moved:
testing (+0.5, coverage floor added), reliability (+0.5), code quality (+1.0,
lint/type/coverage gates), and mobile UX (+1.0, offline reading, offline banner,
skeleton loaders, Web Push and the first real-device field test). The honesty
dimension was already a perfect 10 and stayed there.

---

## 3. Strengths (measured, not asserted)

1. **Scientific honesty.** The `akoan` engine states `NO_EDGE_MEASURED` on its
   own output; the new news-prep plan refuses to name a direction and says so
   in the copy. This is rare and it is the project's defining trait.
2. **Read-only by construction.** The bridge and API bind to loopback; the
   proxy forwards only `GET`/`HEAD`; no code path executes a trade. A software
   bug cannot lose money.
3. **Tests exercise real code paths.** The mobile suite spins a real
   `ThreadingHTTPServer`; CI builds the actual container image and asks the
   running service for `/akoan.json` and `/forecast.json`.
4. **Operational maturity.** Watchdog + keepalive chain + `/version.json` + a
   bot heartbeat. "Is my fix live?" and "is the poller alive?" are one curl.
5. **Zero runtime deps in the bot** (stdlib only) - trivially deployable.
6. **No committed secrets, no bare `except`, no TODO/HACK markers.**

---

## 4. Gaps, ordered by impact

### Critical - these keep it from being a product

**1. Zero real users.** Every release artifact shows 0 downloads. No one
outside the builder has run it. This is the single largest unknown: the field
test on a 2016 handset passed, but n=1.

**2. No privacy policy or terms.** Required for any public launch, store
listing, or paid sale. Neither exists in the repo.

**3. The public gate is one static key.** It now rate-limits (429 + lockout)
and it is honest about being "enough to keep casual visitors out, not a real
auth system". Fine for one person; not saleable as-is.

### Important - these bite as it grows

**4. README counts are stale.** It reports 961 Python tests; the measured figure
is **1129**. Per-suite: advisor 92 -> 158, mobile 151 -> 203.

**5. Mojibake in `AGENTS.md`.** 130 em dashes are stored as `C4 8C C4 86`
mojibake (`A` + `C` + `o`). This is **pre-existing in the repository**, not from
the 2026-10-19 work (whose added block was written clean). It is a display wart
in a current doc.

**6. Oversized files contradict the project's own rule.**
`test_serve_mobile.py` (1503), `astra_bot.py` (1384), `terminal.js` (1357),
`test_astra_bot.py` (1147). The repo's own docs say these should be split.

**7. Two Telegram entry points, one token.** `bot/telegram_bot.py` and
`bridge/telegram_bot/astra_bot.py` both read `TELEGRAM_BOT_TOKEN`. Which one is
live must be documented explicitly - see section 5.

**8. No unified product version tag.** Only `desktop*` and `android*` tags
exist; there is no `v1.0` for the product as a whole.

### Nice to have

**9.** The 49.9 MB research corpus sits in git -> heavy clones (Git-LFS or
on-demand fetch).

**10.** Loose Android settings - `usesCleartextTraffic="true"` and
`allowBackup="true"`. Justified by the comment (a private-LAN HTTP server), but
a hole if the `.apk` is ever distributed widely.

**11.** A local `node_modules` tree under `research/data/.../.work2026/` (not
tracked, but on disk) - local clutter.

---

## 5. Which Telegram bot is the live one

This is a question the docs never answered plainly, so it is answered here with
evidence.

| Entry point | Launched by | When | Live? |
| --- | --- | --- | --- |
| `bridge/telegram_bot/astra_bot.py` | the C++ backend host, `src/platform/windows/AuraBackendHost.cpp:188` | whenever `TELEGRAM_BOT_TOKEN` is set | **YES - this is the bot** |
| `bot/telegram_bot.py` | `scripts/start_stack.sh:140` only (local dev stack) | only when `ASTRA_ENABLE_BOT=1` | **NO** - local only |
| `bot/scan_once.py` + `bot/paper_trade.py` | GitHub Actions cron `.github/workflows/astra-bot.yml` | every 5 minutes | **YES** - serverless scheduled push |

**The interactive production bot is `bridge/telegram_bot/astra_bot.py`.** The
container entrypoint starts the backend host, which supervises it as a sidecar
("launched only when a bot token is present ... supervised exactly like the MT5
bridge"). It is the bot that carries the reply keyboard, `/forecast`, the
entitlements gate, `NewsWatch` and `ForecastWatch`. The Windows desktop bundle
runs the same script from `resources/bridge/telegram_bot`.

`bot/telegram_bot.py` is **not** part of the deployed service. It is a local
notifier for the developer stack, gated behind `ASTRA_ENABLE_BOT=1`, and its own
docstring says it "should be switched on deliberately".

Separately, the always-on, no-server half is the **GitHub Actions cron**: it
runs `bot/scan_once.py` (the M15 scanner) and `bot/paper_trade.py` (the paper
book) on a 5-minute schedule, using the same token from repository secrets. This
is the "24/7 for free" path - there is nothing to keep powered on.

**The `409 Conflict` risk** only appears if both interactive bots poll the same
token at once. In the deployed shape they do not: production runs only
`astra_bot.py`, and `bot/telegram_bot.py` is off unless a developer turns it on
locally with `ASTRA_ENABLE_BOT=1`.

---

## 6. Recommended work order

| # | Task | Impact | Effort |
| --- | --- | --- | --- |
| 1 | Privacy policy + terms (a page + a README link) | high | low |
| 2 | README: state the gate, refresh the counts (1129) | high | low |
| 3 | Put the app in 5-10 real users' hands and collect notes | high | medium |
| 4 | A unified `v1.0` tag, surfaced in `/version.json` | medium | low |
| 5 | Fix the 130-line mojibake block in `AGENTS.md` | medium | low |
| 6 | Split the oversized files | medium | medium |
| 7 | Document which bot owns the token (section 5 above) | medium | low |
| 8 | Remove the stray `node_modules`; Git-LFS for the corpus | low | low |
| 9 | Multi-user gate (accounts / expiry) if it is to be sold | high | high |

---

## 7. Status against the stated priorities

| Priority | Status | Evidence |
| --- | --- | --- |
| 1. Mobile / PWA UI | **working** | offline cache + banner + skeleton loaders + Web Push; real-device field test passed |
| 2. Telegram bot | **working** | `astra_bot.py` supervised by the backend; cron 26/26 success |
| 3. Windows `.exe` + Android `.apk` | **working** | both build and publish automatically; `.apk` signed and structurally valid |
| 4. Run the bot 24/7 for free | **achieved** | GitHub Actions cron every 5 minutes, no server to pay for |

All four stated priorities are met. What remains is productization, not
engineering.

---

## 8. The plain answer

**On honesty: the project is already honest, and that is rare.** The engine
declares "no edge" on itself, and the newest surface (the news-prep plan)
refuses to predict direction in so many words. The only dishonest surfaces are
the *optimistic docs*: the README does not say the site is keyed and its test
counts are stale.

**On professionalism: the gap is no longer the code - the code is strong.** The
gap is (1) real users, (2) a privacy policy and terms, and (3) a unified version
tag. Everything engineering-side that the 2026-10-16 assessment asked for
(quality gates, mobile offline/push) has since landed.

In one line: **88.5/100 - an excellent, honest engineering tool that is one step
from a commercial product; that step is users and paperwork, not code.**

---

_This report was written by an AI agent (OpenHands) on behalf of
@msy898931-boop._
