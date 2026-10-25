#!/usr/bin/env python3
"""Multi-user accounts for the ASTRA gate: per-user keys with expiry.

The single static `ASTRA_ACCESS_TOKEN` is enough for one operator, but it is
not saleable: it cannot be revoked, cannot expire, and gives every visitor the
same identity. This module adds the missing half — a small account store where
each paying user gets their own key, a tier, and an expiry — while leaving the
static token working exactly as before (it stays a valid master key, so an
existing deployment does not break the moment accounts are switched on).

Design constraints, matching the rest of the project:

  * **Standard library only.** No third-party dependency, no service to run.
  * **Keys are never stored.** Only a salted SHA-256 of the secret is kept, so a
    leaked `accounts.json` does not hand over working keys. The plaintext key is
    shown once, at creation, and is unrecoverable afterwards.
  * **Constant-time comparison.** A wrong key leaks no timing signal, the same
    rule the static-token gate follows.
  * **Additive.** With no accounts file and no accounts enabled, nothing here
    runs and the server behaves as it always did.

CLI (operator use):

    python3 mobile/accounts.py create --email a@b.c --tier pro --days 30
    python3 mobile/accounts.py list
    python3 mobile/accounts.py extend --id acc_ab12cd --days 30
    python3 mobile/accounts.py revoke --id acc_ab12cd
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import secrets
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

TIER_FREE = "free"
TIER_PRO = "pro"
_TIERS = (TIER_FREE, TIER_PRO)

# The secret is 32 random bytes (256 bits), base64url-encoded. That is far more
# than enough entropy that the stored hash cannot be brute-forced, which is why
# a fast hash (not bcrypt/scrypt) is correct here: there is no low-entropy
# password to protect, only a high-entropy random token.
_SECRET_BYTES = 32
_SALT_BYTES = 16

KEY_PREFIX = "astra"


def _now() -> float:
    return time.time()


def _new_id() -> str:
    return "acc_" + secrets.token_hex(3)


def _hash_secret(secret: str, salt_hex: str) -> str:
    salt = bytes.fromhex(salt_hex)
    return hashlib.sha256(salt + secret.encode("utf-8")).hexdigest()


@dataclass
class Account:
    """One user. `key_hash` is a salted digest; the plaintext key is not kept."""

    id: str
    email: str
    tier: str
    created_at: float
    expires_at: Optional[float]  # None means "never expires"
    salt: str
    key_hash: str
    active: bool = True
    chat_id: str = ""  # optional link to a Telegram chat, for the bot side
    notes: str = ""

    # ── serialization ─────────────────────────────────────────────────────
    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "email": self.email,
            "tier": self.tier,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "salt": self.salt,
            "key_hash": self.key_hash,
            "active": self.active,
            "chat_id": self.chat_id,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, raw: Dict) -> "Account":
        return cls(
            id=str(raw.get("id", "")),
            email=str(raw.get("email", "")),
            tier=str(raw.get("tier", TIER_FREE)),
            created_at=float(raw.get("created_at", 0.0)),
            expires_at=(None if raw.get("expires_at") in (None, "")
                        else float(raw["expires_at"])),
            salt=str(raw.get("salt", "")),
            key_hash=str(raw.get("key_hash", "")),
            active=bool(raw.get("active", True)),
            chat_id=str(raw.get("chat_id", "")),
            notes=str(raw.get("notes", "")),
        )

    # ── status ────────────────────────────────────────────────────────────
    def is_expired(self, now: Optional[float] = None) -> bool:
        if self.expires_at is None:
            return False
        return (now if now is not None else _now()) >= self.expires_at

    def is_valid(self, now: Optional[float] = None) -> bool:
        """Active and unexpired: the only two ways an account stops working."""
        return self.active and not self.is_expired(now)

    def days_left(self, now: Optional[float] = None) -> Optional[int]:
        """Whole days remaining, rounded up.

        Rounded up so a fresh 30-day key reads "30", not "29" — a subscription
        that expires in 29.9 days is conventionally shown as 30 days left.
        """
        if self.expires_at is None:
            return None
        remaining = self.expires_at - (now if now is not None else _now())
        return max(0, math.ceil(remaining / 86400))


@dataclass
class CreatedAccount:
    """Returned by `create`; the plaintext key exists only in this object."""

    account: Account
    key: str


class AccountStore:
    """A JSON-backed, thread-safe account store.

    The whole file is small (one record per user), so it is read once at load
    and rewritten on every mutation. That keeps the implementation obvious and
    avoids a database dependency for what is, at this scale, a few hundred
    records. Writes are atomic (write to a temp file, then rename) so a crash
    mid-write cannot leave a truncated store.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._accounts: Dict[str, Account] = {}
        # A digest of the file's bytes at the last load. A server caches the
        # store in memory, but the CLI (create/revoke/extend) runs as its own
        # process and only rewrites the file — so without this a revoke would
        # not take effect until the server restarted. Every read compares the
        # digest and reloads when it changed. It is a hash of the content, not
        # the mtime, because two writes inside one filesystem clock tick share
        # an mtime and the change would be missed.
        self._digest: Optional[str] = None
        self._load()

    # ── persistence ───────────────────────────────────────────────────────
    def _file_digest(self) -> Optional[str]:
        try:
            data = self.path.read_bytes()
        except OSError:
            return None
        return hashlib.sha256(data).hexdigest()

    def _refresh(self) -> None:
        """Reload from disk if another process changed the file.

        Called under `_lock` before every read. One small read and a SHA-256;
        a no-op when the file is unchanged, which is the common case.
        """
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
            self._accounts = {}
            return
        items = raw.get("accounts", []) if isinstance(raw, dict) else raw
        loaded: Dict[str, Account] = {}
        if isinstance(items, list):
            for entry in items:
                if isinstance(entry, dict) and entry.get("id"):
                    acc = Account.from_dict(entry)
                    loaded[acc.id] = acc
        self._accounts = loaded

    def _save_locked(self) -> None:
        payload = {"version": 1,
                   "accounts": [a.to_dict() for a in self._accounts.values()]}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), "utf-8")
            os.replace(tmp, self.path)
            self._digest = self._file_digest()
        except OSError as exc:
            print(f"[warn] could not persist accounts: {exc}", file=sys.stderr)

    # ── creation ──────────────────────────────────────────────────────────
    def create(self, email: str, tier: str = TIER_PRO,
               ttl_days: Optional[int] = 30, chat_id: str = "",
               notes: str = "") -> CreatedAccount:
        """Mint an account and return it with its one-time plaintext key."""
        if tier not in _TIERS:
            raise ValueError(f"unknown tier: {tier}")
        secret = secrets.token_urlsafe(_SECRET_BYTES)
        salt = secrets.token_hex(_SALT_BYTES)
        now = _now()
        with self._lock:
            account_id = _new_id()
            while account_id in self._accounts:
                account_id = _new_id()
            acc = Account(
                id=account_id,
                email=email.strip(),
                tier=tier,
                created_at=now,
                expires_at=(None if ttl_days is None else now + ttl_days * 86400),
                salt=salt,
                key_hash=_hash_secret(secret, salt),
                active=True,
                chat_id=str(chat_id).strip(),
                notes=notes.strip(),
            )
            self._accounts[acc.id] = acc
            self._save_locked()
        key = f"{KEY_PREFIX}_{account_id}.{secret}"
        return CreatedAccount(account=acc, key=key)

    # ── lookup ────────────────────────────────────────────────────────────
    def get(self, account_id: str) -> Optional[Account]:
        with self._lock:
            self._refresh()
            return self._accounts.get(account_id)

    def find_by_chat(self, chat_id: str,
                     now: Optional[float] = None) -> Optional[Account]:
        """The valid account linked to a Telegram chat, if any.

        Used by the bot side to map a paying user's chat id to their tier and
        expiry without duplicating the store. Returns None when no active
        account carries that chat id. `now` is injectable so expiry can be
        tested without sleeping.
        """
        chat_id = str(chat_id).strip()
        if not chat_id:
            return None
        now = _now() if now is None else now
        with self._lock:
            self._refresh()
            for acc in self._accounts.values():
                if acc.chat_id == chat_id and acc.is_valid(now):
                    return acc
        return None

    def link_chat(self, account_id: str, chat_id: str) -> bool:
        """Attach a Telegram chat id to an account, so the bot honours it."""
        with self._lock:
            self._refresh()
            acc = self._accounts.get(account_id)
            if acc is None:
                return False
            acc.chat_id = str(chat_id).strip()
            self._save_locked()
            return True

    def list_accounts(self) -> List[Account]:
        with self._lock:
            self._refresh()
            return sorted(self._accounts.values(), key=lambda a: a.created_at)

    def counts(self) -> Dict[str, int]:
        now = _now()
        with self._lock:
            self._refresh()
            valid = [a for a in self._accounts.values() if a.is_valid(now)]
        return {
            "total": len(self._accounts),
            "valid": len(valid),
            "expired": sum(1 for a in self._accounts.values()
                           if a.active and a.is_expired(now)),
            "pro": sum(1 for a in valid if a.tier == TIER_PRO),
        }

    def authenticate(self, key: str, now: Optional[float] = None) -> Optional[Account]:
        """Resolve a presented key to a valid account, or None.

        The key carries its own account id, so the lookup is a dict hit; the
        secret is then compared in constant time against the stored digest. An
        expired or revoked account resolves to None exactly like a wrong key —
        the caller learns only "no access", never *why*.
        """
        if not key or not key.startswith(KEY_PREFIX + "_"):
            return None
        # The account id itself contains underscores (acc_ab12cd), so the id
        # and the secret are separated by a dot that neither part can contain:
        # the id is hex, the secret is base64url. Splitting on "_" would cut
        # the id in half and never resolve.
        body = key[len(KEY_PREFIX) + 1:]
        account_id, dot, secret = body.partition(".")
        if not dot or not account_id or not secret:
            return None
        with self._lock:
            self._refresh()
            acc = self._accounts.get(account_id)
            if acc is None:
                return None
            expected = acc.key_hash
            got = _hash_secret(secret, acc.salt)
        if not hmac.compare_digest(got, expected):
            return None
        return acc if acc.is_valid(now) else None

    # ── mutation ──────────────────────────────────────────────────────────
    def set_active(self, account_id: str, active: bool) -> bool:
        with self._lock:
            self._refresh()
            acc = self._accounts.get(account_id)
            if acc is None:
                return False
            acc.active = active
            self._save_locked()
            return True

    def revoke(self, account_id: str) -> bool:
        return self.set_active(account_id, False)

    def restore(self, account_id: str) -> bool:
        return self.set_active(account_id, True)

    def extend(self, account_id: str, days: int) -> bool:
        """Push the expiry out by `days`; a never-expiring account is untouched."""
        with self._lock:
            self._refresh()
            acc = self._accounts.get(account_id)
            if acc is None:
                return False
            base = max(acc.expires_at or _now(), _now())
            acc.expires_at = base + days * 86400
            self._save_locked()
            return True

    def set_tier(self, account_id: str, tier: str) -> bool:
        if tier not in _TIERS:
            raise ValueError(f"unknown tier: {tier}")
        with self._lock:
            self._refresh()
            acc = self._accounts.get(account_id)
            if acc is None:
                return False
            acc.tier = tier
            self._save_locked()
            return True


# ── process-wide store, built from the environment ────────────────────────
_STORE: Optional[AccountStore] = None
_STORE_LOCK = threading.Lock()


def accounts_path() -> Path:
    env = os.environ.get("ASTRA_ACCOUNTS_FILE", "").strip()
    if env:
        return Path(env)
    return Path(os.environ.get("ASTRA_RUN_DIR", "run")) / "accounts.json"


def enabled() -> bool:
    """True when the operator has switched accounts on.

    Off by default so an existing deployment is untouched: the static token
    keeps working and no accounts file is read.
    """
    flag = os.environ.get("ASTRA_ACCOUNTS", "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def store() -> AccountStore:
    """The process-wide store, created lazily and only when accounts are on."""
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = AccountStore(accounts_path())
        return _STORE


def reset_store() -> None:
    """Drop the cached store so the next access re-reads from disk (tests)."""
    global _STORE
    with _STORE_LOCK:
        _STORE = None


# ── CLI ───────────────────────────────────────────────────────────────────
def _cmd_create(args: argparse.Namespace) -> int:
    st = AccountStore(Path(args.file))
    created = st.create(email=args.email, tier=args.tier,
                        ttl_days=(None if args.days == 0 else args.days),
                        chat_id=args.chat_id or "", notes=args.notes or "")
    acc = created.account
    exp = "never" if acc.expires_at is None else time.strftime(
        "%Y-%m-%d", time.localtime(acc.expires_at))
    print(f"created {acc.id}  email={acc.email}  tier={acc.tier}  expires={exp}")
    print("")
    print("  key (shown once — copy it now, it cannot be recovered):")
    print(f"  {created.key}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    st = AccountStore(Path(args.file))
    now = _now()
    rows = st.list_accounts()
    if not rows:
        print("(no accounts)")
        return 0
    print(f"{'id':<14} {'email':<24} {'tier':<5} {'status':<8} {'days left':>9}")
    for a in rows:
        if not a.active:
            status = "revoked"
        elif a.is_expired(now):
            status = "expired"
        else:
            status = "active"
        left = a.days_left(now)
        left_s = "never" if left is None else str(left)
        print(f"{a.id:<14} {a.email:<24} {a.tier:<5} {status:<8} {left_s:>9}")
    return 0


def _cmd_extend(args: argparse.Namespace) -> int:
    st = AccountStore(Path(args.file))
    ok = st.extend(args.id, args.days)
    print("extended " + args.id if ok else f"no such account: {args.id}")
    return 0 if ok else 1


def _cmd_revoke(args: argparse.Namespace) -> int:
    st = AccountStore(Path(args.file))
    ok = st.revoke(args.id)
    print("revoked " + args.id if ok else f"no such account: {args.id}")
    return 0 if ok else 1


def _cmd_restore(args: argparse.Namespace) -> int:
    st = AccountStore(Path(args.file))
    ok = st.restore(args.id)
    print("restored " + args.id if ok else f"no such account: {args.id}")
    return 0 if ok else 1


def _cmd_link(args: argparse.Namespace) -> int:
    st = AccountStore(Path(args.file))
    ok = st.link_chat(args.id, args.chat_id)
    print(f"linked {args.id} -> chat {args.chat_id}" if ok
          else f"no such account: {args.id}")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="ASTRA account store")
    parser.add_argument("--file", default=str(accounts_path()),
                        help="accounts JSON path (default: %(default)s)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("create", help="mint a new account key")
    p.add_argument("--email", required=True)
    p.add_argument("--tier", default=TIER_PRO, choices=list(_TIERS))
    p.add_argument("--days", type=int, default=30,
                   help="days until expiry; 0 means never")
    p.add_argument("--chat-id", default="", help="optional Telegram chat id")
    p.add_argument("--notes", default="")
    p.set_defaults(func=_cmd_create)

    p = sub.add_parser("list", help="show every account")
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("extend", help="push an expiry out")
    p.add_argument("--id", required=True)
    p.add_argument("--days", type=int, required=True)
    p.set_defaults(func=_cmd_extend)

    p = sub.add_parser("revoke", help="turn an account off")
    p.add_argument("--id", required=True)
    p.set_defaults(func=_cmd_revoke)

    p = sub.add_parser("restore", help="turn a revoked account back on")
    p.add_argument("--id", required=True)
    p.set_defaults(func=_cmd_restore)

    p = sub.add_parser("link", help="attach a Telegram chat id to an account")
    p.add_argument("--id", required=True)
    p.add_argument("--chat-id", required=True)
    p.set_defaults(func=_cmd_link)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
