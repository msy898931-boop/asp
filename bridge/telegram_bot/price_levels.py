"""LVC-0101 - Per-chat price levels and crossing detection.

The whole point of the paid tool: the user names a gold price, and the bot
tells them when it gets crossed. No prediction, no signal - just "4180 was hit
at 14:03". Honest, and useful on its own.

A level is stored per chat with a direction the user cares about:

  * "above" - alert when price rises through it (resistance break / target hit)
  * "below" - alert when price falls through it (support break / stop area)
  * "cross" - alert on either side (default)

Crossing is edge-triggered and one-shot: once a level fires it is marked and
will not fire again until the user re-arms it, so a level sitting exactly on
the price cannot spam.

Standard library only.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

DIR_ABOVE = "above"
DIR_BELOW = "below"
DIR_CROSS = "cross"
_DIRECTIONS = (DIR_ABOVE, DIR_BELOW, DIR_CROSS)


@dataclass
class Level:
    price: float
    direction: str = DIR_CROSS
    created_at: float = field(default_factory=time.time)
    last_side: Optional[str] = None   # "above" | "below" at last observation
    fired: bool = False

    def to_json(self) -> Dict[str, object]:
        return {"price": self.price, "direction": self.direction,
                "created_at": self.created_at, "last_side": self.last_side,
                "fired": self.fired}

    @classmethod
    def from_json(cls, raw: Dict[str, object]) -> "Level":
        return cls(price=float(raw["price"]),
                   direction=str(raw.get("direction", DIR_CROSS)),
                   created_at=float(raw.get("created_at", time.time())),
                   last_side=raw.get("last_side"),  # type: ignore[arg-type]
                   fired=bool(raw.get("fired", False)))


def side_of(price: float, level: float) -> str:
    return "above" if price >= level else "below"


def crossed(level: Level, price: float) -> Optional[str]:
    """Return the crossing direction, or None.

    Edge-triggered: needs a known previous side that differs from the current
    side. A brand-new level learns its side without firing.
    """
    now = side_of(price, level.price)
    prev = level.last_side
    if prev is None or prev == now:
        return None
    if level.direction == DIR_ABOVE and now != "above":
        return None
    if level.direction == DIR_BELOW and now != "below":
        return None
    return now


class LevelStore:
    """Persisted per-chat price levels, keyed by chat id."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._levels: Dict[str, List[Level]] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            self._levels = {}
            return
        out: Dict[str, List[Level]] = {}
        if isinstance(raw, dict):
            for chat, items in raw.items():
                if isinstance(items, list):
                    out[str(chat)] = [Level.from_json(i) for i in items
                                      if isinstance(i, dict) and "price" in i]
        self._levels = out

    def _save_locked(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {c: [l.to_json() for l in ls] for c, ls in self._levels.items()}
            self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), "utf-8")
        except OSError as exc:
            print(f"[warn] could not persist levels: {exc}", file=sys.stderr)

    def list(self, chat_id: str) -> List[Level]:
        with self._lock:
            return list(self._levels.get(str(chat_id), []))

    def count(self, chat_id: str) -> int:
        with self._lock:
            return len(self._levels.get(str(chat_id), []))

    def add(self, chat_id: str, price: float,
            direction: str = DIR_CROSS) -> Level:
        if direction not in _DIRECTIONS:
            direction = DIR_CROSS
        level = Level(price=float(price), direction=direction)
        with self._lock:
            self._levels.setdefault(str(chat_id), []).append(level)
            self._save_locked()
        return level

    def remove(self, chat_id: str, price: float, tol: float = 1e-6) -> bool:
        with self._lock:
            items = self._levels.get(str(chat_id), [])
            for i, lv in enumerate(items):
                if abs(lv.price - price) <= tol:
                    del items[i]
                    self._save_locked()
                    return True
        return False

    def clear(self, chat_id: str) -> int:
        with self._lock:
            n = len(self._levels.get(str(chat_id), []))
            self._levels[str(chat_id)] = []
            self._save_locked()
        return n

    def chats(self) -> List[str]:
        with self._lock:
            return [c for c, ls in self._levels.items() if ls]

    def observe(self, chat_id: str, price: float) -> List[Level]:
        """Update sides for one chat; return the levels that just fired."""
        fired: List[Level] = []
        with self._lock:
            for lv in self._levels.get(str(chat_id), []):
                if lv.fired:
                    continue
                which = crossed(lv, price)
                if which:
                    lv.fired = True
                    lv.last_side = which
                    fired.append(lv)
                else:
                    lv.last_side = side_of(price, lv.price)
            if fired:
                self._save_locked()
        return fired
