#!/usr/bin/env python3
"""Usage metering for the ASTRA gate: a per-account daily quota.

`accounts.py` answers *who* may use the app and *until when*. It does not answer
*how much* — a valid key can call the model-backed routes as fast as it likes.
That is the gap this module closes: a small, per-account counter with a daily
window, so a plan can mean something and a runaway client cannot spend the
model budget of every other user.

What it deliberately is not:

  * **Not a security boundary.** It is a billing meter, so it fails *open*: if
    the store cannot be read or written, the request is allowed and a warning is
    printed. Locking a paying user out because a disk hiccuped is a worse
    failure than granting a free request, and the gate in `serve_mobile.py`
    still stands in front of every route.
  * **Not per-IP.** The unit is the account, not the address, because the thing
    being metered (model tokens) is spent by the account, not by the network.
    With accounts off there is no account to charge, so nothing here runs.
  * **Not a token accountant.** It counts *requests*, not tokens: the model
    cost of a call is not visible to this process, and pretending to measure it
    would be a fabricated number. A request is an honest, checkable unit.

Design constraints, matching the rest of the project:

  * **Standard library only.** No third-party dependency, no service to run.
  * **Additive.** With `ASTRA_METERING` unset, nothing here runs and the server
    behaves exactly as it did before this module existed.
  * **Cross-process reload.** The CLI can reset or inspect the store while the
    server runs; the server reloads whenever the file's bytes change, the same
    content-digest rule `accounts.py` uses (a hash, not an mtime, because two
    writes inside one clock tick share an mtime).

CLI (operator use):

    python3 mobile/metering.py show
    python3 mobile/metering.py reset --id acc_ab12cd
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

# Daily request allowances, per tier. 0 means "no limit". Free is deliberately
# small: it is enough to try the analysis surfaces for real, not enough to run a
# trading desk on. Pro is generous because the point of Pro is not to think
# about the meter. Both are env-overridable so a plan change needs no code edit.
DEFAULT_FREE_DAILY = 20
DEFAULT_PRO_DAILY = 500

# Which tiers exist, mirrored from accounts.py. Kept as plain strings so this
# module does not import accounts (and its store) just to name a tier.
TIER_FREE = "free"
TIER_PRO = "pro"

# Bound on how many accounts the store tracks, so a flood of one-off ids cannot
# grow the file without limit. Well above any real user count.
_MAX_TRACKED = 4096

DAY_S = 86400


def _now() -> float:
    return time.time()


def _day_index(now: float) -> int:
    """The UTC day number a timestamp falls in, as a plain integer.

    Integer days, not dates: the reset is then exact arithmetic (`(day+1)*DAY_S`)
    with no timezone table to get wrong. UTC is the honest choice for a server
    that does not know where its users are.
    """
    return int(now // DAY_S)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(0, value)


def quota_for(tier: str) -> int:
    """The daily request allowance for a tier; 0 means unlimited."""
    if tier == TIER_FREE:
        return _int_env("ASTRA_FREE_DAILY", DEFAULT_FREE_DAILY)
    return _int_env("ASTRA_PRO_DAILY", DEFAULT_PRO_DAILY)


@dataclass
class Decision:
    """The answer to "may this account spend one request now?".

    ``allowed`` is the gate's verdict; the rest is what the caller needs to tell
    the user honestly: how much is used, how much is left, and when it resets.
    ``limit`` is 0 for an unlimited tier, in which case ``remaining`` is None
    rather than a made-up large number.
    """

    allowed: bool
    tier: str
    limit: int
    used: int
    remaining: Optional[int]
    reset_at: int

    def as_dict(self) -> Dict:
        return {
            "allowed": self.allowed,
            "tier": self.tier,
            "limit": self.limit,
            "used": self.used,
            "remaining": self.remaining,
            "reset_at": self.reset_at,
        }


class MeteringStore:
    """A JSON-backed, thread-safe per-account daily counter.

    One small record per account (`{"day": <int>, "count": <int>}`), so the file
    stays tiny and is rewritten whole on each spend. Writes are atomic (temp
    file, then rename) so a crash mid-write cannot truncate the store.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._counts: Dict[str, Dict[str, int]] = {}
        self._digest: Optional[str] = None
        self._load()

    # ── persistence ───────────────────────────────────────────────────────
    def _file_digest(self) -> Optional[str]:
        try:
            return hashlib.sha256(self.path.read_bytes()).hexdigest()
        except OSError:
            return None

    def _refresh(self) -> None:
        """Reload from disk if another process changed the file (under `_lock`)."""
        digest = self._file_digest()
        if digest != self._digest:
            self._load()

    def _load(self) -> None:
        try:
            data = self.path.read_bytes()
        except OSError:
            data = b""
        self._digest = hashlib.sha256(data).hexdigest()
        try:
            raw = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._counts = {}
            return
        items = raw.get("counts", {}) if isinstance(raw, dict) else {}
        loaded: Dict[str, Dict[str, int]] = {}
        if isinstance(items, dict):
            for key, value in items.items():
                if not isinstance(value, dict):
                    continue
                try:
                    loaded[str(key)] = {"day": int(value.get("day", 0)),
                                        "count": int(value.get("count", 0))}
                except (TypeError, ValueError):
                    continue
        self._counts = loaded

    def _save_locked(self) -> None:
        payload = {"version": 1, "counts": self._counts}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), "utf-8")
            os.replace(tmp, self.path)
            self._digest = self._file_digest()
        except OSError as exc:
            # Fail open: a meter that cannot persist must not lock a user out.
            print(f"[warn] could not persist metering: {exc}", file=sys.stderr)

    # ── the one operation that matters ────────────────────────────────────
    def spend(self, account_id: str, tier: str,
              now: Optional[float] = None) -> Decision:
        """Count one request against `account_id`, or refuse it.

        A request is counted only when it is allowed, so a refused call does not
        deepen the hole. A new day resets the counter, and an unlimited tier
        (limit 0) is allowed without writing, so a Pro user's meter stays at 0
        and the store is not rewritten on every call.
        """
        now = _now() if now is None else now
        day = _day_index(now)
        reset_at = (day + 1) * DAY_S
        limit = quota_for(tier)
        with self._lock:
            self._refresh()
            record = self._counts.get(account_id)
            if record is None or record.get("day") != day:
                record = {"day": day, "count": 0}
            used = int(record.get("count", 0))
            if limit and used >= limit:
                return Decision(False, tier, limit, used, 0, reset_at)
            if limit == 0:
                # Unlimited: nothing to persist, so a Pro user never writes.
                return Decision(True, tier, 0, 0, None, reset_at)
            record["count"] = used + 1
            self._counts[account_id] = record
            self._prune_locked(day)
            self._save_locked()
            return Decision(True, tier, limit, used + 1, limit - (used + 1),
                            reset_at)

    def peek(self, account_id: str, tier: str,
             now: Optional[float] = None) -> Decision:
        """The current standing, without spending a request.

        Used by `/me.json`, which reports the meter rather than moving it. A
        stale day reads as zero used, exactly as the next `spend` would see it.
        """
        now = _now() if now is None else now
        day = _day_index(now)
        limit = quota_for(tier)
        with self._lock:
            self._refresh()
            record = self._counts.get(account_id) or {}
            used = int(record.get("count", 0)) if record.get("day") == day else 0
        remaining = None if limit == 0 else max(0, limit - used)
        return Decision(remaining is None or remaining > 0, tier, limit, used,
                        remaining, (day + 1) * DAY_S)

    def reset(self, account_id: str) -> bool:
        """Forget an account's counter. True when a record was removed."""
        with self._lock:
            self._refresh()
            existed = self._counts.pop(account_id, None) is not None
            if existed:
                self._save_locked()
            return existed

    def reset_all(self) -> None:
        with self._lock:
            self._counts = {}
            self._save_locked()

    def _prune_locked(self, day: int) -> None:
        """Drop other days' records when the store grows past its bound.

        Only the current day is ever read, so anything from another day is dead
        weight; dropping it first keeps the bound from ever evicting a live
        counter.
        """
        if len(self._counts) <= _MAX_TRACKED:
            return
        self._counts = {k: v for k, v in self._counts.items()
                        if v.get("day") == day}


# ── process-wide store, built from the environment ────────────────────────
_STORE: Optional[MeteringStore] = None
_STORE_LOCK = threading.Lock()


def metering_path() -> Path:
    env = os.environ.get("ASTRA_METERING_FILE", "").strip()
    if env:
        return Path(env)
    return Path(os.environ.get("ASTRA_RUN_DIR", "run")) / "metering.json"


def enabled() -> bool:
    """True when the operator has switched metering on.

    Off by default, so a deployment that has not opted in is untouched: no file
    is read, no request is counted, and the gate behaves as it always did.
    """
    flag = os.environ.get("ASTRA_METERING", "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def store() -> MeteringStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = MeteringStore(metering_path())
        return _STORE


def reset_store() -> None:
    """Drop the cached store so the next access re-reads from disk (tests)."""
    global _STORE
    with _STORE_LOCK:
        _STORE = None


# ── CLI ───────────────────────────────────────────────────────────────────
def _cmd_show(args: argparse.Namespace) -> int:
    st = MeteringStore(Path(args.file))
    day = _day_index(_now())
    print(f"{'account':<16} {'used today':>10} {'free/day':>9} {'pro/day':>8}")
    if not st._counts:
        print("(no usage recorded)")
    for account_id, record in sorted(st._counts.items()):
        used = record.get("count", 0) if record.get("day") == day else 0
        print(f"{account_id:<16} {used:>10} {quota_for(TIER_FREE):>9} "
              f"{quota_for(TIER_PRO):>8}")
    return 0


def _cmd_reset(args: argparse.Namespace) -> int:
    st = MeteringStore(Path(args.file))
    if args.id:
        ok = st.reset(args.id)
        print("reset " + args.id if ok else f"no usage for: {args.id}")
        return 0 if ok else 1
    st.reset_all()
    print("reset all counters")
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="ASTRA usage metering")
    parser.add_argument("--file", default=str(metering_path()),
                        help="metering JSON path (default: %(default)s)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("show", help="show today's usage per account")
    p.set_defaults(func=_cmd_show)

    p = sub.add_parser("reset", help="clear a counter, or all of them")
    p.add_argument("--id", default="", help="account id; omit to reset all")
    p.set_defaults(func=_cmd_reset)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
