# Independent assessment — ASTRA (fork `msy898931-boop/asp`)

**Date:** 2026-10-16
**Author:** OpenHands agent, on behalf of @msy898931-boop
**Scope:** full repository read, the Python suites executed locally, the C++
test binaries executed, the live service probed, and the two release artifacts
(`.apk`, Windows `.zip`) downloaded and opened.
**Method:** every number below was measured in this session, not copied from an
earlier document. Where a figure differs from
`docs/PROJECT_AUDIT_2026-10-14.md`, the difference is called out.
**Verdict:** **84.5 / 100** — a genuinely well-built, unusually honest system,
held back from "commercial product" by a thin mobile experience, the absence of
engineering quality gates, and a token-only public gate.

This is a point-in-time, independent read. It adds no code. It exists so the
next reader does not have to re-derive it, and so the gaps are stated plainly.

---

## 1. What was measured (evidence)

| Check | Result |
| --- | --- |
| Python tests, all suites, run locally | **597 passing, 0 failing** |
| Distribution | bot 152 · mobile 109 · advisor 92 · bridge 91 · akoan 59 · research 50 · tracker 44 |
| C++ test binaries (19 prebuilt) | **19/19 PASS** |
| CI runs on GitHub (last 20) | **all success** — CI, Deploy (Render), Android APK, Windows, watchdog, chain-guard |
| Latest commit `b5d2eb1` deployed | **yes** — Render deploy success at 21:06 UTC |
| Live routes | `/api/v1/health` **200**; `/akoan.json /vol.json /bot.json /upgrade.json` **401** (access gate ON) |
| `akoan` engine verdict (live run) | `NO_EDGE_MEASURED` · scored 353 · base rate 0.348 · net **−33.44 R** |
| Committed secrets | **none** (`.env`, `*.keystore`, `*.pem`, `*.key` ignored) |
| `LICENSE` | MIT |
| Linters / type checkers / coverage in CI | **none** |
| Tracked size | 797 files, ~110 MB (**49 MB is one research corpus `.gz`**) |
| Largest source files | `mobile/app.js` 1675 · `mobile/terminal.js` 1281 · `bridge/telegram_bot/astra_bot.py` 1021 |
| Android `.apk` | **25 KB** WebView shell (`classes.dex` 12.6 KB); release download count **2** |
| Windows `astra_desktop.exe` | 413 KB + Qt runtime; release download count **0** |

---

## 2. Scorecard

| # | Dimension | Weight | Score | Note |
| --- | --- | --- | --- | --- |
| 1 | Architecture & design | 14 | **12.5** | clean boundaries; a few oversized files |
| 2 | Testing & verification | 15 | **14.0** | the project's best attribute; no coverage measured |
| 3 | Security & safety | 13 | **10.5** | read-only by construction; primitive gate |
| 4 | Reliability & operations | 13 | **11.0** | excellent monitoring; ephemeral state |
| 5 | Documentation | 11 | **9.0** | thorough; stale counts and a gate blind spot |
| 6 | Code quality & maintainability | 11 | **8.5** | no lint/typing; large files |
| 7 | Product honesty & scientific rigor | 10 | **10.0** | exemplary — "no edge" said out loud |
| 8 | UX / product polish (mobile) | 8 | **5.5** | brittle WebView; no offline, no push |
| 9 | Delivery & portability | 5 | **3.5** | artifacts exist but are untested by real users |
| | **Total** | **100** | **84.5** | "strong engineering tool, not yet a product" |

Band: 90+ exemplary · 80–89 strong · 70–79 solid · <70 needs work.

**Delta from the 2026-10-14 audit.** That audit scored 85.0 and gave the mobile
UX **7.0/8**; this read gives **5.5/8**, because it actually downloaded and
opened the `.apk` (a 25 KB remote-URL WebView) rather than reading its source.
Its test count (443 Python) is also now stale — the measured figure is **597**.
The two totals land within 0.5 points, which is a good sign the scoring is
stable; the disagreement is concentrated in the mobile dimension.

---

## 3. Strengths (measured, not asserted)

1. **Scientific honesty.** The engine states `NO_EDGE_MEASURED` on its own
   output and the studies reject their own leads at the gate. This is rare.
2. **Read-only by construction.** The bridge and API bind to loopback; the
   proxy forwards only `GET`/`HEAD`; no code path executes a trade. A software
   bug cannot lose money.
3. **Tests exercise real code paths.** The mobile suite spins a real
   `ThreadingHTTPServer`; CI builds the actual container image and asks the
   running service for `/akoan.json` — the exact shape that once shipped the
   News tab blank.
4. **Operational maturity.** Watchdog + keepalive chain + `/version.json` +
   a bot heartbeat. "Is my fix live?" and "is the poller alive?" are one curl.
5. **Zero runtime deps in the bot** (stdlib only) — trivially deployable.
6. **No committed secrets, no bare `except`, no TODO/HACK markers.**

---

## 4. Gaps, ordered by impact

### Critical — these keep it from being a product

**1. The mobile experience is thin (and mobile is the top priority).**
- The `.apk` is **25 KB** — a WebView pointing at a remote URL. If the server
  is down or the phone is offline, the user sees a blank/error screen.
- **No real offline**: the service worker caches the shell but not the last
  reading, so with no network there is no data at all.
- **No push notifications.** Alerts live inside the app; nothing reaches the
  user when it is closed. Telegram has push; the phone app does not.
- **Zero field use**: 2 APK downloads, 0 desktop downloads. It has not been
  exercised on a real device by anyone but the builder.

**2. No engineering quality gates.**
- No **linter** (`ruff`), no **type checker** (`mypy`), no **coverage**. There
  is no way to answer "how much of this is tested?" from the repo. This is the
  single clearest signal of "not yet professional" to an outside engineer.

**3. Oversized files contradict the project's own rule.**
- `app.js` (1675), `terminal.js` (1281), `astra_bot.py` (1021) mix transport,
  view and state. The repo's own docs say they should be split.

### Important — these bite as it grows

**4. The public gate is one static key** — no expiry, no multi-user, no rate
limit. Fine for one person; not saleable.

**5. Loose Android settings** — `usesCleartextTraffic="true"` (permits plain
HTTP) and `allowBackup="true"` (app data is backed up). Acceptable personally;
a hole if the APK is distributed widely.

**6. Two bots, one token.** `bot/telegram_bot.py` and
`bridge/telegram_bot/astra_bot.py` both read `TELEGRAM_BOT_TOKEN`. If both run,
Telegram returns `409`. Which one is live must be documented explicitly.

**7. Documentation does not mention the access gate.** The README hands out the
live URL with no note that it now requires a key, so a first visitor reads the
401 as "broken".

### Nice to have

**8.** The 49 MB research corpus sits in git → heavy clones (Git-LFS or
on-demand fetch).
**9.** No privacy policy / terms (needed for a store or public launch).
**10.** No unified product version tag (only `desktop*` / `android*`).
**11.** UX details: no skeleton loaders, no transitions, no explicit offline
banner.

---

## 5. Recommended work order

| # | Task | Impact | Effort |
| --- | --- | --- | --- |
| 1 | Mobile: offline cache of the last reading + offline banner + skeleton loaders | high | medium |
| 2 | Real push notifications for the phone (Web Push), not bot-only | high | medium-high |
| 3 | Quality gates: `ruff` + `mypy` + coverage in CI, with a floor | high | low |
| 4 | README: state the gate, refresh the test counts | medium | low |
| 5 | Split the oversized files | medium | medium |
| 6 | Tighten Android (`cleartext`, `allowBackup`) + rate-limit the gate | medium | low |
| 7 | Document which bot owns the token (prevent `409`) | medium | low |
| 8 | Privacy policy + terms | medium | low |
| 9 | Git-LFS for the corpus | low | low |

---

## 6. The plain answer

**On honesty: the project is already honest, and that is rare.** The engine
declares "no edge" on itself. The only dishonest surfaces are the *optimistic
docs*: the README does not say the site is keyed, and the earlier audit's test
counts are stale.

**On professionalism: the gap is not "better code" — the code is strong.** The
gap is (1) a real mobile experience (offline, notifications, no blank screen),
(2) engineering quality gates, and (3) a real gate if it is ever distributed.

In one line: **84.5/100 — an excellent engineering tool that is not yet a
commercial product; the difference lives in the mobile surface and delivery.**

---

_This report was written by an AI agent (OpenHands) on behalf of
@msy898931-boop._
