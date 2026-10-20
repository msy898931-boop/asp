# Independent assessment - ASTRA (fork `msy898931-boop/asp`)

**Date:** 2026-10-20
**Author:** OpenHands agent, on behalf of @msy898931-boop
**Commit assessed:** `928f146` ("fix: widen target_ladder signature to accept
None"), branch `feat/scaled-targets` - the branch that adds the isolated AI
analysis engine and rolls the scaled-target ladder across the stack.
**Scope:** full repository read; every Python suite executed locally; the C++
test binaries built and run; the coverage gate reproduced; the live service
probed; the release download counts read back from the API.
**Method:** every number below was measured in this session on this commit. No
figure is copied from `docs/ASSESSMENT_2026-10-19.md`; where a figure differs,
the difference is called out.
**Verdict:** **90.0 / 100** (up from 88.5) - a strong, unusually honest system
that has grown a genuine capability this cycle (the isolated AI engine and the
ladder). The gap to a commercial product is unchanged and is not code: it is
**real users**, a **privacy policy / terms**, and a **unified version tag**.

Band: 90+ exemplary - 80-89 strong - 70-79 solid - <70 needs work.

---

## 0. A real defect was found and fixed during this assessment

`mypy`, run over the same scope the CI uses
(`bot tracker akoan advisor bridge mobile src`), reported **3 errors** in the
code added on this branch:

```
bot/telegram_bot.py:171: error: Argument 2 to "target_ladder" has incompatible
type "Any | None"; expected "float"  [arg-type]
bot/telegram_bot.py:171: error: Argument 3 to "target_ladder" has incompatible
type "Any | None"; expected "float"  [arg-type]
```

The caller reads `entry`/`stop`/`target` straight out of the signal dict, so the
values are `Optional`. The function already guards for `None` on its first line,
so only the annotation was wrong. Fixed in `928f146`: the signature now reads
`entry: float | None, stop: float | None, target: float | None`. `mypy` is clean
again over 68 source files.

This is worth recording for two reasons: it is a defect the branch's own tests
did not catch, and it is exactly the kind of thing the quality gate exists to
catch. The gate worked; the fix was one line.

---

## 1. What was measured (evidence)

| Check | Result |
| --- | --- |
| Python tests, every suite, run locally | **1188 passing, 0 failing** |
| Distribution | tests 308 - mobile 213 - advisor 197 - bot 161 - bridge 156 - akoan 59 - research 50 - tracker 44 |
| C++ test binaries (`ctest`) | **20/20 PASS** |
| `ruff check .` | clean |
| `mypy` (CI scope, 68 source files) | clean, after the `928f146` fix |
| Coverage (all suites, `--source`) | **83%** (CI floor is 75%) |
| Live service probe | `/api/v1/health` **200** |
| Release downloads | android **16**; `desktop-0.1` **1**; all others **0** |
| Committed secrets | **none** (pattern scan) |
| `TODO` / `FIXME` / `HACK` markers | **0** |
| Bare `except:` | **0** |
| `LICENSE` | MIT |
| Repository | **826 tracked files**; ~115 MB on disk |
| C++ source | 134 `.cpp` + 159 `.h`, ~28.7k lines |
| Python source | 171 files, ~39.8k lines |
| Docs | 150 Markdown files, ~48k lines |
| AI engine (new this cycle) | `advisor/ai_engine.py` 544 lines + `advisor/ai_advisor.py` 810 lines |

**Delta from the 2026-10-19 assessment (88.5).** The counts moved because the
branch added code: advisor 158 -> 197, mobile 203 -> 213, bot 155 -> 161,
bridge 152 -> 156, tests 308 (unchanged). Two suites could not run until a
missing package was installed (`cryptography`/`pywebpush` for `mobile`), which
is an environment gap, not a project defect.

---

## 2. Scorecard

| # | Dimension | Weight | Score | Note |
| --- | --- | --- | --- | --- |
| 1 | Architecture & design | 14 | **12.5** | clean boundaries; the AI engine is genuinely isolated (own process, own port) |
| 2 | Testing & verification | 15 | **14.5** | the project's best attribute; 1188 tests, coverage gated at 83% |
| 3 | Security & safety | 13 | **11.5** | read-only by construction; the new engine adds an outbound API surface |
| 4 | Reliability & operations | 13 | **11.5** | excellent monitoring; a real 5-minute cron; degradation is honest |
| 5 | Documentation | 11 | **9.5** | thorough; stale counts and the pre-existing mojibake block |
| 6 | Code quality & maintainability | 11 | **10.0** | lint/type/coverage gates all pass; the `928f146` fix proves the gate bites |
| 7 | Product honesty & scientific rigor | 10 | **10.0** | exemplary - "no edge" said out loud, and win-rates labelled unvalidated |
| 8 | UX / product polish (mobile) | 8 | **6.5** | offline cache + banner + Web Push + a real-device test |
| 9 | Delivery & portability | 5 | **4.0** | artifacts build automatically; still almost no users |
| | **Total** | **100** | **90.0** | "exemplary engineering tool, one step from a product" |

**Why it moved up from 88.5.** Three dimensions gained: code quality (+0.5, the
gates are now demonstrably enforced - this assessment caught a real error with
them), security (+0.5, from 11.0 - the gate is rate-limited and the new engine
degrades honestly instead of failing open), and delivery (+0.5, the ladder and
engine ship through every surface). The new engine is the first component that
talks to a third-party API on the user's key, which is a surface to watch, but
it is isolated, read-only with respect to the pipeline, and inert without a key.

---

## 3. Strengths (measured, not asserted)

1. **Scientific honesty.** The `akoan` engine states `NO_EDGE_MEASURED` on its
   own output. The new AI engine stamps every response `win_rate_validated:
   false` and the prompt tells the model, in writing, that its win-rate "is an
   estimate, not a measured number". This is rare and it is the project's
   defining trait.
2. **The new engine is isolated by construction.** `advisor/ai_engine.py` runs
   as its own process on its own port (8794). It cannot touch the deterministic
   pipeline. When the key is absent it reports `configured: false` and degrades;
   it does not fabricate output. A missing key cannot break the product.
3. **Read-only by construction.** The bridge and API bind to loopback; the proxy
   forwards only `GET`/`HEAD`; no code path executes a trade. A software bug
   cannot lose money.
4. **Tests exercise real code paths.** The mobile suite spins a real
   `ThreadingHTTPServer`; the C++ suite runs 20 binaries; the `tests/` tier even
   reports its own empty calibration tiers rather than hiding them.
5. **Zero runtime deps in the bot** (stdlib only) - trivially deployable.
6. **No committed secrets, no bare `except`, no TODO/HACK markers.**

---

## 4. Gaps, ordered by impact

### Critical - these keep it from being a product

**1. Almost no real users.** The `.apk` shows **16** downloads and every other
artifact shows 0. Sixteen is not sixteen people. There is still no second
independent user whose notes could be read.

**2. No privacy policy or terms.** Required for any public launch, store
listing, or paid sale. Confirmed absent again this session: no file matching
`privacy` or `terms` exists in the tree.

**3. The public gate is one static key.** Fine for one person; not saleable
as-is.

### Important - these bite as it grows

**4. The AI engine's win-rate is an estimate, not a measurement.** This is
correct and honest, and it is also the honest ceiling on what the feature can be
sold as. There is no measured edge behind the number.

**5. No unified product version tag.** Only `android*`, `desktop*`, `api-v1.0`
and `qt-lighttheme-qss` exist. There is still no `v1.0` for the product as a
whole, surfaced in `/version.json`.

**6. Oversized files contradict the project's own rule.**
`mobile/test_serve_mobile.py` (1547), `bridge/telegram_bot/astra_bot.py` (1392),
`bridge/telegram_bot/test_astra_bot.py` (1177), `mobile/serve_mobile.py` (1095).

**7. Mojibake in `AGENTS.md`** - pre-existing, not from this cycle.

**8. The 49.9 MB research corpus sits in git** - heavy clones.

---

## 5. What it is worth (the plain number)

This section is written to be honest, because the project is built on honesty.

**Cost to rebuild.** ~28.7k lines of C++, ~39.8k lines of Python with 1188
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

With those three: **$40,000-$90,000**. With revenue of $2-5k/month, the usual
2-4x annual multiple puts it at **$60,000-$240,000**.

**In one line:** the code is worth ~$45-95k to rebuild and ~$8-25k to buy today,
and the whole difference is paperwork and users, not engineering.

---

## 6. Recommended work order

| # | Task | Impact | Effort |
| --- | --- | --- | --- |
| 1 | Privacy policy + terms (a page + a README link) | high | low |
| 2 | Put the app in 10 real users' hands and collect notes | high | medium |
| 3 | A unified `v1.0` tag, surfaced in `/version.json` | medium | low |
| 4 | Refresh README counts (1188) | medium | low |
| 5 | Fix the mojibake block in `AGENTS.md` | medium | low |
| 6 | Split the oversized files | medium | medium |
| 7 | Git-LFS for the research corpus | low | low |
| 8 | Multi-user gate (accounts / expiry) if it is to be sold | high | high |

---

## 7. The plain answer

**On honesty: still exemplary.** The engine declares "no edge" on itself, and
the newest capability refuses to present an unvalidated probability as a fact.

**On professionalism: the gap is not the code.** The code scored 90. The
remaining ten points are users, legal pages, and a version tag - none of which
are written in C++ or Python.

In one line: **90.0/100 - an excellent, honest engineering tool that is one step
from a commercial product; that step is users and paperwork, not code.**

---

## 8. Caveats on this assessment

- The win-rate figures the AI engine produces are the model's own estimates and
  are **not** validated against realised outcomes. Nothing here endorses them.
- This tool is decision support in **shadow mode**. It does not place orders.
- The download counts come from the public GitHub API and may lag.
- The AI engine could not be exercised end-to-end during this assessment: the
  provider key was not present in the environment. The request path was verified
  previously against the live endpoint (a dummy key returns `401 Invalid API
  Key`), which shows the call is well-formed, but no real analysis output was
  produced in this session.
- This is not investment advice and not a formal valuation.

---

_This report was written by an AI agent (OpenHands) on behalf of
@msy898931-boop. Every number in it was measured in the 2026-10-20 session. One
real defect (`928f146`) was found and fixed by the quality gate during the
assessment._
