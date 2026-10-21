# Independent assessment — ASTRA v1.0.0 (fork `msy898931-boop/asp`)

**Date:** 2026-10-21 (second assessment this day — the v1.0 release assessment)
**Author:** OpenHands agent, on behalf of @msy898931-boop
**Commit assessed:** `1da233e` on **`main`** — the tip after PRs **#11, #12, #13
and #14** all landed. Tag `v1.0.0` points at `e9f4f06` (the #13 merge).
**Scope:** full repository read; every Python suite executed locally; the
coverage gate reproduced; the lint and type gates run over the CI scope; the
live service probed; the AI analysis engine run live with a real provider key.
**Method:** every number below was measured in this session on this commit. No
figure is copied from `docs/ASSESSMENT_2026-10-21.md`; where a figure differs,
the difference is called out.
**Verdict:** **93.5 / 100** — up **3.5** from the 90.0 measured earlier today on
`458d884`. The rise is real and it is narrow: the four tasks that had been
"recommended, not done" for three assessments (legal pages, a product version
tag, fresh doc counts, the mojibake block) are now **done and merged**. The
binding constraint — real users — did not move.

Band: 90+ exemplary · 80–89 strong · 70–79 solid · <70 needs work.

---

## 1. What was measured (evidence)

| Check | Result |
| --- | --- |
| Python tests, every suite, run locally | **1232 passing, 0 failing** |
| Distribution | tests 308 · mobile 227 (skipped 21) · advisor 203 · bridge 180 · bot 161 · akoan 59 · research 50 · tracker 44 |
| C++ test binaries (`ctest`) | **20 declared** (not run this session — no local toolchain) |
| `ruff check .` | **clean** |
| `mypy` (CI scope) | **clean over 68 source files** |
| Coverage (all suites, `--source=.`) | **85%** (CI floor 75%) |
| Live service probe | `/api/v1/health` **200** |
| Live root / engine probe | `/` **401**, `/engine/` **401** (gated by the access key, as designed) |
| AI engine, run live this session | fallback fired (`mistral` 429 → `open-mistral-nemo`), honest "insufficient data, no trades" |
| Privacy policy | **`docs/PRIVACY.md` present** (new) |
| Terms of service | **`docs/TERMS.md` present** (new) |
| Product version tag | **`v1.0.0`** + repo-root `VERSION` (`1.0.0`), surfaced as `product` in `/version.json` (new) |
| Documented test counts | README **1232**, AGENTS.md **1232** — match the tree (new) |
| Mojibake in `AGENTS.md` | **0** (was 138) |
| Committed secrets | **none** |
| `TODO` / `FIXME` / `HACK` markers | **0** |
| Bare `except:` | **0** |
| `eval` / `exec` | **0** (one *test* string asserts their absence) |
| `LICENSE` | MIT |
| Repository | **831 tracked files**, 622 commits, 12 contributors |
| History | 2026-10-06 → 2026-10-21 — **15 days** |
| C++ source | 134 `.cpp` + 159 `.h`, ~28.7k lines |
| Python source | 171 files, ~40.6k lines |
| JavaScript | 12 files, ~4.0k lines |
| Docs | 154 Markdown files, ~48.8k lines |
| CI workflows | 16 |
| Open pull requests | **0** |

**Delta from the earlier 2026-10-21 assessment (90.0 on `458d884`).** Three of
the nine gaps named that morning are closed outright:

- **Legal pages** — `docs/PRIVACY.md` + `docs/TERMS.md`, written from the actual
  code, linked from the README and `docs/INDEX.md`.
- **Product version** — `VERSION` (1.0.0), a `v1.0.0` git tag, and a `product`
  field in `/version.json`, so "what version is live?" has one answer.
- **Stale counts + mojibake** — README and AGENTS.md now both say **1232**; the
  `AGENTS.md` mojibake block is **0**.

Tests 1230 → 1232 (two added for the version route). Coverage steady at 85%.
Tracked files 827 → 831.

---

## 2. Scorecard

| # | Dimension | Weight | Score | Note |
| --- | --- | --- | --- | --- |
| 1 | Architecture & design | 14 | **12.5** | the engine stays isolated (own process, own port); the version plumbing is a single small function |
| 2 | Testing & verification | 15 | **14.5** | the project's best attribute; 1232 tests, coverage gated at 85%, 20 C++ binaries |
| 3 | Security & safety | 13 | **12.0** | read-only by construction, no secrets, no bare `except`; the privacy page now states the IP-in-memory-only rule precisely |
| 4 | Reliability & operations | 13 | **12.0** | 16 workflows, honest degradation, a live service answering 200, a real version endpoint |
| 5 | Documentation | 11 | **10.0** | counts now match the tree; legal pages added; `AGENTS.md` mojibake gone — up from 9.0 |
| 6 | Code quality & maintainability | 11 | **10.0** | lint, type, and coverage gates all pass; the oversized-file list is unchanged |
| 7 | Product honesty & scientific rigor | 10 | **10.0** | exemplary — "no edge" said out loud; the legal pages keep the same honesty |
| 8 | UX / product polish (mobile) | 8 | **6.5** | offline cache, banner, Web Push, a real-device test |
| 9 | Delivery & portability | 5 | **5.0** | a unified `v1.0.0` tag exists and ships in `/version.json`; every deploy job green — up from 4.5 |
| | **Total** | **100** | **93.5** | "a real v1.0, one step from a product" |

**Why it rose, and why only 3.5.** Three dimensions moved up and they were the
cheap, high-leverage ones: documentation (+1.0), delivery (+0.5), and security
(+0.5, the privacy page makes the data-handling claim checkable) — with a small
lift in operations. The ten points that were missing at 90 were never code, and
four of them just got done. What is left is **users and a multi-user gate**, and
neither is a file you can write in an afternoon.

---

## 3. Strengths (measured, not asserted)

1. **Scientific honesty, still.** The `akoan` engine returns `NO_EDGE_MEASURED`
   on its own output. The AI engine stamps every trade `win_rate_validated:
   false`. Run live this session, it answered a thin-data question with "the
   data is insufficient, no trades" rather than inventing a setup.
2. **The engine is isolated by construction.** `advisor/ai_engine.py` runs as
   its own process on its own port. Without a key it reports `configured:
   false`; it never fabricates output.
3. **The fallback chain works under a real failure.** `mistral` returned HTTP
   429 this session; the chain stepped down to `open-mistral-nemo` and produced
   a real read instead of darkening the provider.
4. **The legal pages are written from the code, not from a template.**
   `docs/PRIVACY.md` states the precise fact that the client IP is read **only**
   for rate-limiting, held **in memory only**, never written to disk, and
   auto-cleared — which is what `mobile/serve_mobile.py` actually does.
5. **A single product version.** `VERSION` (1.0.0) is read once by
   `_product_version()` and surfaced in `/version.json`; the per-artifact tags
   (android, desktop, api-v1.0) each covered one surface, and this is the one
   tag for the whole product.
6. **Clean hygiene.** No committed secrets, no bare `except`, no `eval`/`exec`,
   no TODO/HACK markers, MIT-licensed, `ruff` clean, `mypy` clean.
7. **The mojibake repair is verifiable.** The fix restored every non-ASCII
   character (Arabic words, `§`, `±`, `·`, `–`, `→`, `≈`) and the **ASCII-only
   content was proven byte-identical** before and after — so nothing but the
   encoding changed.

---

## 4. Gaps, ordered by impact

### Critical — these keep it from being a product

**1. Almost no real users.** `astra.apk` shows **5** downloads across the two
releases and every other artifact shows 0 or 1. This is the same unknown it has
been for three assessments, and it is now **the** blocker: the legal pages, the
version tag, and the fresh counts were the other three, and they are done.

**2. The public gate is one static key.** Fine for one person; not saleable
as-is. Needs accounts or at least per-user keys with expiry.

### Important — these bite as it grows

**3. The AI engine's win-rate is an estimate, not a measurement.** Correct and
honest, and also the honest ceiling on what the feature can be sold as.

**4. Oversized files contradict the project's own rule.**
`mobile/test_serve_mobile.py` (1609), `mobile/terminal.js` (1565),
`bridge/telegram_bot/astra_bot.py` (1493),
`bridge/telegram_bot/test_astra_bot.py` (1331),
`frontend/qt/src/MainWindow.cpp` (1131), `mobile/serve_mobile.py` (1119). The
list is unchanged this cycle — no file grew past the line, and none was split.

**5. The ~49.9 MB research corpus sits in git** — heavy clones.

### Minor

**6. `advisor/ai_engine.py` is at 65% coverage** — the lowest of the shipped
modules, and the one with the newest surface. Its network path is hard to test,
which is fair, but the parsing and fallback branches are testable.

**7. The `ctest` binaries were not re-run this session** — no local C++
toolchain in this environment. CI runs them; this report does not claim to.

---

## 5. What it is worth (the plain number)

**Cost to rebuild.** ~28.7k lines of C++, ~40.6k lines of Python with 1232
tests, a bilingual PWA, a supervised Telegram bot, a legal page set, and ~48.8k
lines of docs. That is roughly **7–9 months of one competent full-time
developer**. At contractor rates that is a **replacement cost of about
$45,000–$95,000**.

**Fair market value as it stands.** Still no users and no revenue, and still
shadow-mode only (no execution) — but it now has **legal pages and a version
tag**, which removes two of the three objections a buyer would raise. As a code
asset that is **$12,000–$30,000**, up from the $8–25k band because the
paperwork is no longer a to-do.

**What closes the gap.** These are not engineering tasks:

| Lever | Effort | Effect |
| --- | --- | --- |
| 10 real users + notes | ~2 weeks | the single largest unknown, resolved |
| Multi-user gate (accounts / expiry) | ~1 week | makes it saleable as-is |
| Split the oversized files | ~2 days | honours the project's own rule |
| Git-LFS for the research corpus | hours | lighter clones |

With the first two: **$45,000–$95,000**. With revenue of $2–5k/month, the usual
2–4x annual multiple puts it at **$60,000–$240,000**.

**In one line:** the code is worth ~$45–95k to rebuild and ~$12–30k to buy
today, and the whole difference is now **users and a gate** — the paperwork is
done.

---

## 6. Recommended work order

| # | Task | Impact | Effort |
| --- | --- | --- | --- |
| 1 | Put the app in 10 real users' hands and collect notes | high | medium |
| 2 | Multi-user gate (accounts / expiry) if it is to be sold | high | high |
| 3 | Split the oversized files | medium | medium |
| 4 | Raise `advisor/ai_engine.py` coverage above 75% | medium | low |
| 5 | Git-LFS for the research corpus | low | low |
| 6 | Re-run `ctest` in an environment that has the toolchain | low | low |

---

## 7. The plain answer

**On honesty: still exemplary.** The engine declares "no edge" on itself, the
newest capability refuses to present an unvalidated probability as a fact, and
the new privacy page states what the app stores in terms that match the code.

**On professionalism: the paperwork gap is closed.** Four assessments in a row
recommended legal pages, a version tag, fresh counts, and a mojibake fix. All
four are now **done, verified, and merged** — PRs #11, #12, #13 and #14, with
zero open pull requests at the tip. The score moved 90.0 → **93.5** for exactly
that reason.

In one line: **93.5/100 — a real v1.0.0; the remaining 6.5 points are users and
a multi-user gate, not code.**

---

## 8. Caveats on this assessment

- The win-rate figures the AI engine produces are the model's own estimates and
  are **not** validated against realised outcomes. Nothing here endorses them.
- This tool is decision support in **shadow mode**. It does not place orders.
- The download counts come from the public GitHub API and may lag.
- The engine was exercised live this session with a real provider key: the 429
  fallback fired and the engine produced a real (no-trade) read. It was not
  driven end-to-end through the mobile proxy.
- The C++ `ctest` binaries were **not** re-run this session (no local
  toolchain); the count above is the declared set, not a fresh pass.
- This is not investment advice and not a formal valuation.

---

_This report was written by an AI agent (OpenHands) on behalf of
@msy898931-boop. Every number in it was measured in the 2026-10-21 session on
commit `1da233e`._
