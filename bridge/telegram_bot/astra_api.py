"""TEL-0100 - Thin client for the ASTRA backend loopback API.

The bot never reaches runtime internals directly; it reads the same
frontend-facing API the desktop UI reads (127.0.0.1:8790/api/v1) and maps the
JSON envelopes to short operator messages. Standard library only.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

DEFAULT_API_BASE = "http://127.0.0.1:8790/api/v1"
BRIDGE_PORT = 8791   # the host's Python bridge port (see StartupCoordinator)
ADVISOR_PORT = 8792  # the isolated AI advisor's loopback port
_READ_TIMEOUT_SECONDS = 5.0


class ApiError(RuntimeError):
    """Raised when the backend API cannot be reached or returns an error."""


class AstraApi:
    """Read-only view over the ASTRA backend loopback API."""

    def __init__(self, base_url: str = DEFAULT_API_BASE,
                 timeout: float = _READ_TIMEOUT_SECONDS,
                 bridge_base: Optional[str] = None,
                 advisor_base: Optional[str] = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # The bridge and the advisor answer on their own loopback ports, outside
        # the /api/v1 prefix. Keep the API host but swap in the right port unless
        # an explicit base is given.
        if bridge_base is None or advisor_base is None:
            from urllib.parse import urlsplit, urlunsplit
            parts = urlsplit(self.base_url)
            if bridge_base is None:
                bridge_base = urlunsplit(
                    (parts.scheme, f"{parts.hostname}:{BRIDGE_PORT}", "/v1", "", ""))
            if advisor_base is None:
                advisor_base = urlunsplit(
                    (parts.scheme, f"{parts.hostname}:{ADVISOR_PORT}", "/v1", "", ""))
        self.bridge_base = bridge_base.rstrip("/")
        self.advisor_base = advisor_base.rstrip("/")

    # -- transport ----------------------------------------------------------

    def _get_abs(self, url: str) -> Dict[str, Any]:
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.URLError as exc:
            raise ApiError(f"backend unreachable at {url}: {exc.reason}") from exc
        except OSError as exc:  # socket timeouts, connection resets
            raise ApiError(f"backend unreachable at {url}: {exc}") from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ApiError(f"malformed response from {url}: {exc}") from exc

    def _get(self, path: str) -> Dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        return self._get_abs(url)

    @staticmethod
    def _data(envelope: Dict[str, Any]) -> Dict[str, Any]:
        data = envelope.get("data")
        return data if isinstance(data, dict) else {}

    # -- read routes --------------------------------------------------------

    def health(self) -> Dict[str, Any]:
        return self._data(self._get("health"))

    def system_state(self) -> Dict[str, Any]:
        return self._data(self._get("system/state"))

    def bridge_status(self) -> Dict[str, Any]:
        return self._data(self._get("bridge/status"))

    def latest_analysis(self) -> Dict[str, Any]:
        return self._data(self._get("analysis/latest"))

    def latest_signal(self) -> Dict[str, Any]:
        return self._data(self._get("signals/latest"))

    def latest_probability(self) -> Dict[str, Any]:
        return self._data(self._get("probability/latest"))

    def governance_status(self) -> Dict[str, Any]:
        return self._data(self._get("governance/status"))

    def shadow_positions(self) -> Dict[str, Any]:
        return self._data(self._get("shadow/positions"))

    # -- bridge (live candles) ---------------------------------------------

    def candles(self, timeframe: str = "M15", limit: int = 2) -> Dict[str, Any]:
        """Live candles straight from the MT5/python bridge."""
        return self._get_abs(f"{self.bridge_base}/candles?tf={timeframe}&limit={limit}")

    def last_price(self) -> Optional[float]:
        """Most recent close, or None when the bridge has nothing usable."""
        try:
            env = self.candles()
        except ApiError:
            return None
        payload = env.get("payload") if isinstance(env, dict) else None
        bars = payload.get("bars") if isinstance(payload, dict) else None
        if not isinstance(bars, list) or not bars:
            return None
        last = bars[-1]
        try:
            return float(last.get("close"))
        except (TypeError, ValueError, AttributeError):
            return None

    # -- advisor (the multi-timeframe trade card) --------------------------

    def trade_card(self) -> Dict[str, Any]:
        """The advisor's multi-timeframe read, with its exact levels.

        Read from the advisor's model-free ``/v1/card`` route, so polling it
        never spends the model's token quota and the card stays available when
        the opinion is paused. Read-only and best-effort: an unreachable advisor
        raises ``ApiError`` so the caller can stay silent rather than invent a
        card.
        """
        env = self._get_abs(f"{self.advisor_base}/card")
        card = env.get("trade_card") if isinstance(env, dict) else None
        return card if isinstance(card, dict) else {}

    # -- composite health snapshot -----------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """One bounded snapshot for /status, tolerating partial failures."""
        result: Dict[str, Any] = {}
        for name, fn in (
            ("health", self.health),
            ("system", self.system_state),
            ("bridge", self.bridge_status),
        ):
            try:
                result[name] = fn()
            except ApiError as exc:
                result[name] = {"_error": str(exc)}
        return result
