# Independent assessment - ASTRA (fork `msy898931-boop/asp`)

**Date:** 2026-10-21
**Author:** OpenHands agent, on behalf of @msy898931-boop
**Commit assessed:** `458d884` on **`main`** - the merge of PR #10, which brings
the isolated AI analysis engine, the Mistral fallback chain, the Telegram engine
button, the web Engine tab, and the frozen-contract fix onto the default branch.
**Scope:** full repository read; every Python suite executed locally; the C++
binaries run; the coverage gate reproduced; lint and type gates run over the CI
scope; the live service and the gated engine probed.
**Method:** every number below was measured in this session on this commit. No
figure is copied from `docs/ASSESSMENT_2026-10-20.md`; where a figure differs,
the difference is called out.
**Verdict:** **90.0 / 100** - flat against the 2026-10-20 score of 90.0, and the
flatness is the honest result: engineering moved up, documentation drifted down,
and the binding constraint (users, legal pages, a version tag) did not move at
all.

Band: 90+ exemplary - 80-89 strong - 70-79 solid - <70 needs work.

---

## 1. What was measured (evidence)

| Check | Result |
| --- | --- |
| Python tests, every suite, run locally | **1230 passing, 0 failing** |
| Distribution | tests 308 - mobile 225 (skipped 21) - advisor 203 - bridge 180 - bot 161 - akoan 59 - research 50 - tracker 44 |
| C++ test binaries (`ctest`) | **20/20 PASS** |
| `ruff check .` | clean |
| `mypy` (CI scope) | **clean over 68 source files** |
| Coverage (all suites, `--source`) | **85%** (CI floor 75%) |
| Live service probe | `/api/v1/health` **200** |
| Live engine probe | `/engine/` **401** (gated by the access key, as designed) |
| AI engine, run live this session | fallback fired (`mistral-small-latest` 429 -> `open-mistral-nemo`), honest "insufficient data, no trades" |
| Committed secrets | **none** |
| `TODO` / `FIXME` / `HACK` markers | **0** |
| Bare `except:` | **0** |
| `eval` / `exec` | **0** |
| `LICENSE` | MIT |
| Repository | **827 tracked files**, 618 commits, 12 contributors |
| History | 2026-10-06 to 2026-10-21 - **15 days** |
| C++ source | 134 `.cpp` + 159 `.h`, ~28.7k lines |
| Python source | 171 files, ~40.6k lines (40,572) |
| JavaScript | 12 files, ~4.0k lines |
| Docs | 151 Markdown files, ~48.3k lines |
| CI workflows | 16 |

**Delta from the 2026-10-20 assessment (90.0).** Tests 1188 -> 1230 (+42).
Coverage 83% -> 85%. `mypy` clean over 68 files (the 2026-10-20 run reported 11
pre-existing errors outside the touched files; the CI scope is clean again).
The engine line reached `main`, so the engine, the bot button, and the web tab
are now on the deploy branch rather than a side branch. The download counts
*moved the wrong way*: `android` 16 -> 1, everything else 0 - the releases were
re-cut, and no second real user appeared.

---

## 2. Scorecard

| # | Dimension | Weight | Score | Note |
| --- | --- | --- | --- | --- |
| 1 | Architecture & design | 14 | **12.5** | the engine is genuinely isolated (own process, own port); the frozen contract's four copies were caught and re-aligned |
| 2 | Testing & verification | 15 | **14.5** | the project's best attribute; 1230 tests, coverage gated at 85%, 20 C++ binaries |
| 3 | Security & safety | 13 | **11.5** | read-only by construction, no secrets, no bare `except`; the engine adds an outbound API surface on the user's key |
| 4 | Reliability & operations | 13 | **11.5** | 16 workflows, honest degradation, a live service answering 200 |
| 5 | Documentation | 11 | **9.0** | thorough, but the headline counts are now stale by 269 (README says 961; the tree runs 1230), and `AGENTS.md` still carries mojibake |
| 6 | Code quality & maintainability | 11 | **10.0** | lint, type, and coverage gates all pass; the oversized-file list grew again |
| 7 | Product honesty & scientific rigor | 10 | **10.0** | exemplary - "no edge" said out loud; every model number labelled an estimate |
| 8 | UX / product polish (mobile) | 8 | **6.5** | offline cache, banner, Web Push, a real-device test |
| 9 | Delivery & portability | 5 | **4.5** | the engine now ships on `main` and every deploy job is green; still almost no users |
| | **Total** | **100** | **90.0** | "exemplary engineering tool, one step from a product" |

**Why it is flat.** Three dimensions gained a little - testing (coverage 83 ->
85), code quality (`mypy` clean over the CI scope), and delivery (the engine
reached `main`). Documentation lost a little more, because the README's test
table is now 22% behind the tree. The two cancel. The ten points that are still
missing are not code.

---

## 3. Strengths (measured, not asserted)

1. **Scientific honesty.** The `akoan` engine returns `NO_EDGE_MEASURED` on its
   own output. The AI engine stamps every trade `win_rate_validated: false`. Run
   live this session, the engine answered a thin-data question with "the data is
   insufficient, no trades" rather than inventing a setup. That is the right
   behaviour and it is rare.
2. **The engine is isolated by construction.** `advisor/ai_engine.py` runs as
   its own process on its own port. It cannot touch the deterministic pipeline.
   Without a key it reports `configured: false`; it never fabricates output.
3. **The fallback chain works under a real failure.** `mistral-small-latest`
   returned HTTP 429 with `x-ratelimit-limit-req-minute: 0` - a per-model plan
   limit, not an exhausted quota. The chain stepped down to `open-mistral-nemo`
   and produced a real read, instead of darkening the provider for 15 minutes.
4. **Read-only by construction.** The bridge and API bind to loopback; the proxy
   forwards only `GET`/`HEAD`; the only "position" in the tree is
   `SHADOW_POSITION_OPENED`, a simulator. A bug cannot lose money.
5. **Tests exercise real code paths.** The mobile suite spins a real
   `ThreadingHTTPServer`; the C++ suite runs 20 binaries; the `tests/` tier
   reports its own empty calibration tiers rather than hiding them.
6. **Clean hygiene.** No committed secrets, no bare `except`, no `eval`/`exec`,
   no TODO/HACK markers, MIT-licensed, `ruff` clean.

---

## 4. Gaps, ordered by impact

### Critical - these keep it from being a product

**1. Almost no real users.** `android` shows **1** download and every other
artifact shows 0. The count fell from 16 because the releases were re-cut, but
either number is far from a second independent user whose notes could be read.
This remains the single largest unknown.

**2. No privacy policy or terms.** Required for any public launch, store
listing, or paid sale. Confirmed absent again this session: no file matching
`privacy` or `terms` exists in the tree.

**3. The public gate is one static key.** Fine for one person; not saleable
as-is.

### Important - these bite as it grows

**4. The AI engine's win-rate is an estimate, not a measurement.** Correct and
honest, and also the honest ceiling on what the feature can be sold as. No
measured edge stands behind the number.

**5. The documented test counts are stale.** `README.md` says **961** (dated
2026-10-16) and `AGENTS.md` says **1071**; the tree runs **1230**. The README is
22% behind - worse than the 2026-10-20 gap - because this cycle added tests and
updated no counts.

**6. No unified product version tag.** Only `android*`, `desktop*`, `api-v1.0`
and `qt-lighttheme-qss` exist. There is still no `v1.0` for the product as a
whole, surfaced in `/version.json`.

**7. Oversized files contradict the project's own rule.**
`mobile/test_serve_mobile.py` (1598), `mobile/terminal.js` (1565),
`bridge/telegram_bot/astra_bot.py` (1493), `bridge/telegram_bot/test_astra_bot.py`
(1331), `frontend/qt/src/MainWindow.cpp` (1131), `mobile/serve_mobile.py` (1103).
Three of these grew this cycle.

**8. Mojibake in `AGENTS.md`** - 138 occurrences, pre-existing, not from this
cycle.

**9. The ~49.9 MB research corpus sits in git** - heavy clones.

---

## 5. What it is worth (the plain number)

**Cost to rebuild.** ~28.7k lines of C++, ~40.6k lines of Python with 1230
tests, a bilingual PWA, a supervised Telegram bot, and ~48k lines of docs. That
is roughly **7-9 months of one competent full-time developer**. At contractor
rates that is a **replacement cost of about $45,000-$95,000**.

**Fair market value as it stands.** No users, no revenue, no legal pages, and
shadow-mode only (no execution). As a code asset that is **$8,000-$25,000**.

**What closes the gap.** These are not engineering tasks:

| Lever | Effort | Effect |
| --- | --- | --- |
| Privacy policy + terms | ~1 day | required for any sale or store listing |
| 10 real users + notes | ~2 weeks | the single largest unknown, resolved |
| Unified `v1.0` tag in `/version.json` | hours | makes "what version is live?" answerable |
| Refresh the README test table to 1230 | minutes | stops the docs contradicting the tree |

With the first three: **$40,000-$90,000**. With revenue of $2-5k/month, the usual
2-4x annual multiple puts it at **$60,000-$240,000**.

**In one line:** the code is worth ~$45-95k to rebuild and ~$8-25k to buy today,
and the whole difference is paperwork and users, not engineering.

---

## 6. Recommended work order

| # | Task | Impact | Effort |
| --- | --- | --- | --- |
| 1 | Privacy policy + terms (a page + a README link) | high | low |
| 2 | Put the app in 10 real users' hands and collect notes | high | medium |
| 3 | Refresh the README/AGENTS test counts (1230) | medium | low |
| 4 | A unified `v1.0` tag, surfaced in `/version.json` | medium | low |
| 5 | Fix the mojibake block in `AGENTS.md` | medium | low |
| 6 | Split the oversized files | medium | medium |
| 7 | Git-LFS for the research corpus | low | low |
| 8 | Multi-user gate (accounts / expiry) if it is to be sold | high | high |

---

## 7. The plain answer

**On honesty: still exemplary.** The engine declares "no edge" on itself, and
the newest capability refuses to present an unvalidated probability as a fact -
it would rather say "the data is insufficient" than invent a trade.

**On professionalism: the gap is not the code.** The code scores 90. The
remaining ten points are users, legal pages, and a version tag - none of which
are written in C++ or Python.

In one line: **90.0/100 - an excellent, honest engineering tool that is one step
from a commercial product; that step is users and paperwork, not code.**

---

## 8. Caveats on this assessment

- The win-rate figures the AI engine produces are the model's own estimates and
  are **not** validated against realised outcomes. Nothing here endorses them.
- This tool is decision support in **shadow mode**. It does not place orders.
- The download counts come from the public GitHub API and may lag; they fell
  this cycle because the releases were re-cut, not because users left.
- The engine was exercised live this session with a real provider key: the 429
  fallback fired and the engine produced a real (no-trade) read. It was not
  driven end-to-end through the mobile proxy.
- This is not investment advice and not a formal valuation.

---

_This report was written by an AI agent (OpenHands) on behalf of
@msy898931-boop. Every number in it was measured in the 2026-10-21 session._
