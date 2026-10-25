# Documentation index

The repository carries a lot of history (144 Markdown files). This index says
what each area is, and which files are **current** versus **historical**. It
adds nothing to the code and removes nothing: it is a map.

If you are new, read these four, in order:

1. `README.md` — what the project is.
2. `AGENTS.md` — how to build, test, and ship in this repo.
3. `docs/RUNBOOK.md` — what to do when something breaks.
4. `mobile/README.md` — the phone app, which is the shipping surface.

## Current (read these)

| Area | File | What it is |
| --- | --- | --- |
| Repo | `README.md` | Project identity and scope |
| Repo | `AGENTS.md` | Build/test/ship instructions, learned rules |
| Ops | `DEPLOY.md` | How the live service is deployed |
| Ops | `docs/RUNBOOK.md` | Incident runbook |
| Ops | `docs/FORK_UPSTREAM_DECISION.md` | Fork vs upstream: what to do, and the hard constraint |
| Audit | `docs/PROJECT_AUDIT_2026-10-14.md` | Full-repo audit and 100-point score (85.0) |
| Audit | `docs/ASSESSMENT_2026-10-16.md` | Independent re-assessment (84.5), measured live; supersedes the audit's test counts |
| Audit | `docs/ASSESSMENT_2026-10-19.md` | Independent assessment (88.5); names the live Telegram bot |
| Audit | `docs/ASSESSMENT_2026-10-20.md` | Independent assessment (90.0) on `feat/scaled-targets`; supersedes the 2026-10-19 counts and adds the worth estimate |
| Audit | `docs/ASSESSMENT_2026-10-21.md` | Independent assessment (90.0) on `main` (`458d884`); 1230 tests, coverage 85%, and the engine reached the deploy branch |
| Audit | `docs/ASSESSMENT_2026-10-21_v1.0.md` | The v1.0.0 release assessment (**93.5**), measured on `1da233e` after the legal, release and mojibake PRs landed; superseded by the 2026-10-25 valuation |
| Audit | `docs/ASSESSMENT_2026-10-25_valuation.md` | **Latest** — the **94.0** assessment and the sale-price analysis, measured on `main` at `ebd0800` after the multi-user accounts gate (PR #17) landed; supersedes every earlier score |
| Legal | `docs/PRIVACY.md` | Privacy policy: what is stored (a chat id and a push endpoint, only on opt-in), and how to remove it |
| Legal | `docs/TERMS.md` | Terms of service: decision support in shadow mode; AI win-rates are estimates, not measurements |
| Product | `docs/SELLING.md` | Pricing and what the tool honestly does |
| Mobile | `mobile/README.md` | The PWA: routes, tabs, offline shell |
| Evidence | `docs/evidence/FIELD_TEST_apk_2026-10-17.md` | The `.apk` run on real hardware (first such record) |
| Bot | `bridge/telegram_bot/README.md` | Telegram bot, entitlements, watchdog |
| akoan | `akoan/README.md` | The news-statistics engine and its verdicts |
| Advisor | `advisor/README.md` | The external second-opinion model |
| Android | `android/README.md` | Building the `.apk` |
| Packaging | `packaging/README.md` | Building the Windows `.exe` |
| Qt | `frontend/qt/README.md` | The desktop front end |
| Architecture | `docs/architecture/` | API schema, bridge, decision model, build order |
| Frontend spec | `docs/frontend/` | Handoff and design review |
| Brand | `docs/brand/ASTRA_VISUAL_IDENTITY.md` | Visual identity |
| Research | `research/PHASE0_VOLATILITY_DECISION.md` | The one measured edge (volatility) |
| Research | `research/features_real/README.md` | Feature corpus and T27 note |

## Historical (kept for the record — do not treat as current)

These are how the project got here. They are useful when you need to know
*why* a decision was made, not what is true now.

| Area | Path | What it is |
| --- | --- | --- |
| Audit trail | `AUDIT_REPORTS/` | One audit per task (T01–T30), plus contract/history audits |
| Agent coordination | `coordination/` | Per-agent logs, daily summaries, heartbeat, task board |
| Project control | `project-control/` | Bootstrap, decisions log, scope, state, test log |
| AI prompts | `ai-prompts/` | The original build prompts and review gates |
| Superseded | `docs/archive/` | The v3.0 master and the original MT5 reference |
| Master spec | `docs/AURA_ASTRA_MASTER_UNIFIED_PROJECT_v4.0.md` | The V4 canonical spec (large; reference) |
| Interim reports | `docs/FINAL_REPORT.md`, `coordination/FINAL_REPORT.md` | Draft reports, marked PENDING where unfinished |
| Session notes | `session-setup.md` | Local environment notes |

## Rule for future docs

Add a line here when you add a current doc. Historical files are never deleted
in place: if a doc is superseded, move it under `docs/archive/` and say so here.
