# Fork vs upstream: what to do about the divergence

**Status:** OPEN — this records the decision, it does not make it.
**Date:** 2026-10-09

## The situation

This repository is a **fork**. It is not the original project.

- Fork (what we ship): `msy898931-boop/asp`
- Upstream (original): `alimazna/asp`
- Common ancestor: `3939a27`
- Fork is **124 commits ahead** and **101 commits behind** upstream `main`.

The two lines have grown apart in different directions, so "just merge" is not
free.

## What is fork-only (must keep, upstream does not have it)

- The mobile PWA (`mobile/`): the News tab, the volatility filter, the journal.
- The Telegram bot and its watchdog (`bot/`, `bridge/telegram_bot/`).
- The akoan news-statistics engine (`akoan/`).
- The container CI job and the deploy checks added on top of them.

## What is upstream-only (upstream has it, we do not)

All of it serves the **desktop / Qt / Windows** side:

- Qt autostart fixes (bind the API before spawning the bridge).
- Tracker clock alignment to the broker server clock.
- The `/health` cached-snapshot fix so health never blocks on the tick.
- A Windows installer CI job.

This matters because one of our goals is a Windows `.exe`, and that is exactly
the area upstream kept working on after the fork point.

## Options

| Option | Gains | Costs | Risk |
| --- | --- | --- | --- |
| **A. Hard fork** — never merge | Nothing to reconcile; the fork stays clean | We re-solve every Qt bug upstream already fixed | Lowest |
| **B. Merge upstream `main`** | All Qt/Windows fixes at once | Conflicts in shared C++/CI; every fix must be re-verified; upstream docs and reports flood the tree | Highest |
| **C. Cherry-pick the Qt fixes** | The Windows-specific fixes we actually need | Manual, one commit at a time; still needs re-verification | Medium |

## Recommendation

**Option C, and only when the Windows `.exe` work starts.** The upstream-only
commits are real fixes for a goal we still have. Pulling them one at a time
keeps the mobile/bot work — which upstream does not carry — untouched, and
avoids a large merge whose conflicts would land in code neither side has
verified together.

Until then, **Option A** is correct: the fork is ahead on everything that
shipped this week, and the mobile/bot/akoan lines are independent of the Qt
work.

## Hard constraint

**Never push to `alimazna/asp`.** Upstream is read-only for us. `git remote`
keeps `upstream` for fetching only; all pushes go to `origin` (the fork).

## How to act on Option C later

```bash
# Fetch the fixes without touching local work.
git fetch upstream

# Inspect the Windows/Qt commits that we do not have.
git log --oneline main..upstream/main -- '*.cpp' '*.h' '.github/workflows/*windows*'

# Bring one across and re-verify it here.
git cherry-pick <sha>
# then run the full suite (see AGENTS.md) before pushing to origin.
```
