"""ENT-0101 - Access tiers for the paid alert tool.

Two tiers:

  * free - the tool works: live price, a couple of price levels, and the
    market-state view. Enough to be genuinely useful, not a crippled demo.
  * pro  - more price levels, faster checks, and the push-alert stream.

Design note: when no upgrade URL is configured (``ASTRA_UPGRADE_URL`` unset)
everyone is treated as pro. That keeps the layer dormant until the product is
actually on sale, so development, CI, and the "not selling yet" state behave
exactly as before. The moment a Stripe Payment Link is set, the gate turns on.

Standard library only, like the rest of the bridge.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional

TIER_FREE = "free"
TIER_PRO = "pro"

# Defaults; override with env when the plan changes, no code edit needed.
DEFAULT_FREE_LEVELS = 2
DEFAULT_PRO_LEVELS = 50


class Entitlements:
    """Per-chat tier store, plus the limits each tier grants."""

    def __init__(self, path: Path, upgrade_url: str = "",
                 free_levels: int = DEFAULT_FREE_LEVELS,
                 pro_levels: int = DEFAULT_PRO_LEVELS,
                 admin_chats: Optional[List[str]] = None,
                 accounts_store=None) -> None:
        self.path = path
        self.upgrade_url = upgrade_url.strip()
        self.free_levels = free_levels
        self.pro_levels = pro_levels
        self._admins = {str(c).strip() for c in (admin_chats or []) if str(c).strip()}
        # Optional link to the mobile account store. When present, a chat id
        # bound to a valid, unexpired account is Pro — so a key bought on the
        # web grants the same tier in the bot, and expiry is enforced in one
        # place. Absent, the store is not consulted and nothing changes.
        self._accounts = accounts_store
        self._lock = threading.Lock()
        self._tiers: Dict[str, str] = {}
        self._load()

    # ── persistence ───────────────────────────────────────────────────────
    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text("utf-8"))
            if isinstance(raw, dict):
                self._tiers = {str(k): str(v) for k, v in raw.items()}
        except (OSError, ValueError):
            self._tiers = {}

    def _save_locked(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._tiers, indent=2, sort_keys=True),
                                 "utf-8")
        except OSError as exc:
            print(f"[warn] could not persist entitlements: {exc}", file=sys.stderr)

    # ── tier resolution ───────────────────────────────────────────────────
    @property
    def selling(self) -> bool:
        """True once an upgrade URL exists, i.e. the gate is live."""
        return bool(self.upgrade_url)

    def tier(self, chat_id: str) -> str:
        chat_id = str(chat_id)
        if chat_id in self._admins:
            return TIER_PRO
        # A valid, unexpired account bound to this chat is Pro regardless of
        # the manual store below — the account is the source of truth for a
        # purchased key, and its expiry is what turns the tier off.
        if self._accounts is not None:
            account = self._accounts.find_by_chat(chat_id)
            if account is not None:
                return TIER_PRO
        if not self.selling:
            # Not on sale yet: do not withhold anything from anyone.
            return TIER_PRO
        with self._lock:
            return self._tiers.get(chat_id, TIER_FREE)

    def is_pro(self, chat_id: str) -> bool:
        return self.tier(chat_id) == TIER_PRO

    def is_admin(self, chat_id: str) -> bool:
        return str(chat_id) in self._admins

    def max_levels(self, chat_id: str) -> int:
        return self.pro_levels if self.is_pro(chat_id) else self.free_levels

    # ── granting (manual, after a purchase) ───────────────────────────────
    def set_tier(self, chat_id: str, tier: str) -> None:
        if tier not in (TIER_FREE, TIER_PRO):
            raise ValueError(f"unknown tier: {tier}")
        with self._lock:
            self._tiers[str(chat_id)] = tier
            self._save_locked()

    def grant_pro(self, chat_id: str) -> None:
        self.set_tier(chat_id, TIER_PRO)

    def revoke(self, chat_id: str) -> None:
        self.set_tier(chat_id, TIER_FREE)

    def counts(self) -> Dict[str, int]:
        with self._lock:
            pro = sum(1 for t in self._tiers.values() if t == TIER_PRO)
        return {"pro": pro, "total": len(self._tiers)}

    # ── upgrade copy ──────────────────────────────────────────────────────
    def upgrade_line(self) -> str:
        """One-line upsell used when a free user hits a gate."""
        if not self.selling:
            return ""
        return (f"⬆️ ترقَّ إلى Pro لتحصل على {self.pro_levels} مستوى سعري "
                f"وفحص أسرع:\n{self.upgrade_url}")


def from_env(state_dir: Path) -> Entitlements:
    """Build from the process environment (the host passes these through)."""

    def _int(name: str, default: int) -> int:
        try:
            return int(os.environ.get(name, "") or default)
        except ValueError:
            return default

    admins = [c for c in os.environ.get("ASTRA_PRO_CHATS", "").split(",") if c.strip()]
    return Entitlements(
        path=state_dir / "entitlements.json",
        upgrade_url=os.environ.get("ASTRA_UPGRADE_URL", ""),
        free_levels=_int("ASTRA_FREE_LEVELS", DEFAULT_FREE_LEVELS),
        pro_levels=_int("ASTRA_PRO_LEVELS", DEFAULT_PRO_LEVELS),
        admin_chats=admins,
        accounts_store=_accounts_store_from_env(),
    )


def _accounts_store_from_env():
    """The mobile account store, when ASTRA_ACCOUNTS is on; else None.

    The store lives in `mobile/`, which is a sibling of this package. It is
    imported lazily and defensively: if the path is not importable (the bot
    running on its own), the feature simply stays off rather than breaking the
    bot at startup.
    """
    flag = os.environ.get("ASTRA_ACCOUNTS", "").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return None
    try:
        import accounts  # type: ignore
    except ImportError:
        here = Path(__file__).resolve()
        for parent in here.parents:
            candidate = parent / "mobile"
            if (candidate / "accounts.py").is_file():
                sys.path.insert(0, str(candidate))
                try:
                    import accounts  # type: ignore
                except ImportError:
                    return None
                break
        else:
            return None
    try:
        return accounts.store()
    except (OSError, ValueError) as exc:  # pragma: no cover - host-specific
        print(f"[warn] accounts enabled but store unavailable: {exc}",
              file=sys.stderr)
        return None
