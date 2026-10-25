# Git-LFS for the research corpus

## Why

A fresh clone of this repository is heavy, and almost all of the weight is a
handful of data files that nobody edits by hand:

| Path | Size |
| --- | --- |
| `research/features_real/corpus/real_corpus_2021_2025.json.gz` | ~49 MB |
| `research/data/xauusd_m1/*.csv.gz` (2021–2025, bid + ask) | ~4–5 MB each |
| `research/data/xauusd_m1/xauusd_m1_real.csv` | ~5 MB |
| `research/features_real/news_ledger.jsonl.gz` | ~0.7 MB |

Together they are ~70 MB of the ~107 MB pack. They are *regenerated*, not
edited: `scripts/` re-derives the corpus from the M1 candles, and the candles
come from a Dukascopy fetch (`research/data/xauusd_m1/fetch.sh`). Storing each
regeneration as a full git blob makes the repository grow without bound.

## What is already done (safe, additive)

`.gitattributes` marks those paths with the Git-LFS filter:

```
research/features_real/corpus/*.json.gz       filter=lfs diff=lfs merge=lfs -text
research/features_real/news_ledger.jsonl.gz   filter=lfs diff=lfs merge=lfs -text
research/data/xauusd_m1/*.csv                 filter=lfs diff=lfs merge=lfs -text
research/data/xauusd_m1/*.csv.gz              filter=lfs diff=lfs merge=lfs -text
research/data/xauusd_m1/ask/*.csv.gz          filter=lfs diff=lfs merge=lfs -text
```

From now on, **when the client is installed**, any new version of these files is
stored as an LFS pointer and the content lives in LFS storage instead of the
pack.

This changes nothing on its own:

- The blobs already in history are untouched. No commit is rewritten.
- A clone **without** `git-lfs` installed receives the existing files unchanged
  — git ignores a filter it does not know. So CI, the deploy images and any
  teammate who has not installed LFS keep working exactly as before.
- The deterministic gzip stays the committed artifact (see `.gitignore`); only
  its storage location changes.

Install the client once per machine so the filter is honoured locally:

```sh
git lfs install          # once, sets up the smudge/clean filters
git lfs track            # shows the patterns (from .gitattributes)
```

## The one-time size win (needs a decision)

To also move the ~70 MB that is **already in history** into LFS, run a history
rewrite:

```sh
git lfs migrate import --include="research/features_real/corpus/*.json.gz,\
research/features_real/news_ledger.jsonl.gz,\
research/data/xauusd_m1/*.csv,research/data/xauusd_m1/*.csv.gz,\
research/data/xauusd_m1/ask/*.csv.gz"
git push --force-with-lease
```

Understand the cost before doing it:

- **Every commit id changes.** Anyone with a clone must re-clone or reset hard.
- It needs a **force-push**, which is exactly the kind of history rewrite this
  project avoids by default. It is deliberately left out of the committed work.
- GitHub also has to have LFS storage enabled for the repository, and the
  objects must be pushed (`git lfs push --all origin`).

If the goal is only "stop the repo growing from here", the committed
`.gitattributes` already achieves it and nothing else is needed. Do the rewrite
only if reclaiming the existing history size is worth the force-push.

## Verifying

After a rewrite (or on a fresh clone with the client installed):

```sh
git lfs ls-files                     # the paths now tracked as pointers
git cat-file -p HEAD:research/features_real/corpus/real_corpus.json.gz | head -1
# expect: version https://git-lfs.github.com/spec/v1
```

CI does not need the corpus content for the Python suites; the akoan study and
the news-move table read the *committed* gzip, which LFS checks out on clone
when the client is present. If a CI job ever needs the data and runs in an
environment without `git-lfs`, add `lfs: true` to its `actions/checkout@v4`
step — the runner ships the client.
