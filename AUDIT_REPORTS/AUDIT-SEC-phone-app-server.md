# Audit Report — Phone-app server: three exploitable holes, all fixed

- **Auditor:** OpenHands agent (Verification & Security)
- **Date:** 2026-10-20
- **Repo HEAD:** `608e9bf` (fix on top of it)
- **Severity:** **material** — one hole leaked the server's own source over
  HTTP, one let a request read a file outside the shell directory, and one made
  the access-key rate limiter a no-op. All three are reachable by an
  unauthenticated caller when `ASTRA_ACCESS_TOKEN` is set, and by any caller
  when it is not.
- **Zones:** phone-app server (`mobile/serve_mobile.py`), deploy config
  (`render.yaml`, `fly.toml`).
- **Method:** static read of the request path, then **live exploitation** of a
  locally-started server, then a fix and a re-exploit to confirm it is closed.
  Every finding below was reproduced against a running process, not inferred.

---

## Context

The phone app is served by one process, `mobile/serve_mobile.py`, which is the
**only** internet-facing surface. Everything else (the C++ host, the MT5 bridge,
the advisor and engine services) binds to loopback and, in the C++ case,
*refuses* to bind a non-loopback host (`src/api/LoopbackApiServer.cpp`). So the
audit focused there, plus a check of secrets, injection sinks, and the Telegram
bot's authorization.

Everything that was checked and found **already sound** is listed at the end, so
the clean result is on the record too.

## Findings

### F1 — Server source served over HTTP (material)

`_static()` mapped a request path onto `HERE` (the `mobile/` directory) and
served whatever file it found, with the content type chosen by extension and a
generic fallback of `application/octet-stream`. The server's own source lives in
that same directory.

Reproduced live:

```
$ curl -o /dev/null -w '%{http_code}' http://127.0.0.1:PORT/serve_mobile.py
200
$ curl -o /dev/null -w '%{http_code}' http://127.0.0.1:PORT/webpush.py
200
$ curl -o /dev/null -w '%{http_code}' http://127.0.0.1:PORT/__pycache__/serve_mobile.cpython-313.pyc
200
```

Impact: full read of the access-control logic, the push key handling, and the
compiled cache — a map of exactly what else to probe, handed out for free.

### F2 — Containment check defeated by a shared name prefix (material)

The guard was a string prefix test:

```python
target = os.path.normpath(os.path.join(HERE, rel))
if not target.startswith(HERE) or not os.path.isfile(target):
```

`"mobile"` is a prefix of `"mobile-secret"`, so a sibling directory whose name
merely starts with `mobile` passed the check.

Reproduced live (with a sibling directory created for the test):

```
$ curl --path-as-is http://127.0.0.1:PORT/../mobile-secret/leak.txt
TOPSECRET-LEAK
```

Impact: read any file reachable by a `..` hop into a sibling directory sharing
the prefix — e.g. `mobile-backup/`, `mobile-old/` if either ever exists. The
plain `../VERSION` case happened to 404 only because `os.path.normpath` collapsed
it; the prefix hole is the real one.

### F3 — Rate limiter bypassed by a spoofed header (material)

`_client_key()` read the **leftmost** `X-Forwarded-For` value unconditionally,
with no check that a trusted proxy was actually in front. The header is
client-supplied, so rotating it gave every guess a fresh budget.

Reproduced live (`ASTRA_GATE_MAX_FAILURES=3`):

```
# no header — the limiter works
try1 401  try2 401  try3 429  try4 429  try5 429
# rotating header — the limiter is a no-op
xff 10.0.0.1 401  xff 10.0.0.2 401  xff 10.0.0.3 401
xff 10.0.0.4 401  xff 10.0.0.5 401  xff 10.0.0.6 401
```

Impact: the brute-force oracle the limiter exists to close, reopened. When
`ASTRA_ACCESS_TOKEN` is set this is an unlimited guessing channel against the
phone app's only credential.

## Fixes (all additive)

1. **`_static()` — realpath containment + extension allowlist.** Containment is
   now `os.path.realpath(target).startswith(os.path.realpath(HERE) + os.sep)`,
   which is not fooled by a shared prefix or by a symlink. On top of that, only
   extensions in `_SERVE_EXTS` (the same set as `_CONTENT_TYPES`) are served, so
   the server's `.py` and `.pyc` are a 404 even though they sit inside `HERE`.
   Two independent guards, because either alone has a hole.

2. **`_client_key()` — trust the header only when declared, and only the
   rightmost hop.** New `ASTRA_TRUST_FORWARDED_FOR`, default **off**. When off,
   the socket peer address is used and a spoofed header is ignored. When on, the
   **rightmost** hop is used: a well-behaved proxy appends the address it saw, so
   everything to its left is whatever the client sent. Both deploy files
   (`render.yaml`, `fly.toml`) set the flag, because both sit behind a proxy that
   overwrites the header.

3. **Defense in depth — two response headers.** `X-Frame-Options: DENY` and
   `Referrer-Policy: no-referrer`. The shell is never an iframe, and the access
   key can appear in the URL on a first visit, so it must not leak through
   `Referer`. `X-Content-Type-Options: nosniff` was already present.

Re-exploited after the fix, on a clean server:

```
/serve_mobile.py                       404   (was 200)
/webpush.py                            404   (was 200)
/__pycache__/...pyc                    404   (was 200)
/../mobile-secret/leak.txt             404   (was 200)
/styles.css, /app.js, /index.html      200   (unchanged)
xff 10.0.0.1..5 → 401 401 429 429 429   (limiter now trips)
```

## Tests added

Nine tests, all running the real server against real sockets — no mocks:

- `StaticFileSecurityTest` — index and a real asset are served; the server's
  source and its `.pyc` are 404; a non-asset extension is 404; `..` does not
  escape; a sibling directory with a shared prefix is unreachable; the three
  security headers are present.
- `GateRateLimitTest` — the header is **ignored by default** (a rotated header
  does not buy fresh guesses); when trusted, distinct clients stay apart; only
  the **rightmost** hop is used, so a spoofed left side cannot rotate identity.

## Checked and found sound (no change needed)

- **Secrets:** no `.env`, keystore, or credential is tracked; `.gitignore` covers
  `.env*`, `*.secret`, `*.key`, `secrets/`, and the Android keystore. No
  high-entropy literal in tracked source.
- **Injection:** no `shell=True`, `os.system`, `os.popen`, `pickle.load`,
  `yaml.load`, `eval`, or `exec` in non-test code. No SQL at all. No path built
  from user input except the one fixed above.
- **Other servers:** the advisor and engine handlers route on **fixed** path
  strings only (no user-controlled path, no file read); the C++ host refuses a
  non-loopback bind; the bridge binds loopback only.
- **Telegram bot:** `/grant` is gated on `entitlements.is_admin(chat_id)`
  against an explicit admin set — no self-promotion path.
- **Access control:** the cookie comparison uses `hmac.compare_digest`
  (constant time); a bare page load is not counted as a failed guess, so a real
  user cannot be locked out by refreshing.

## Residual risk

- `_static` serves `index.html` and friends to anyone when
  `ASTRA_ACCESS_TOKEN` is empty (the documented default). That is the product's
  intended open mode, not a defect — but it means the shell, not the source, is
  public in that mode.
- No CSP header is set. The shell loads Google Fonts and runs inline scripts, so
  a correct policy needs testing against the real pages; deferred rather than
  guessed.
