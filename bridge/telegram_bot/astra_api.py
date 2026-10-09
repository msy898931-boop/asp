"""TEL-0100 - Thin client for the ASTRA backend loopback API.

The bot never reaches runtime internals directly; it reads the same
frontend-facing API the desktop UI reads (127.0.0.1:8790/api/v1) and maps the
JSON envelopes to short operator messages. Standard library only.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

DEFAULT_API_BASE = "http://127.0.0.1:8790/api/v1"
_READ_TIMEOUT_SECONDS = 5.0


class ApiError(RuntimeError):
    """Raised when the backend API cannot be reached or returns an error."""


class AstraApi:
    """Read-only view over the ASTRA backend loopback API."""

    def __init__(self, base_url: str = DEFAULT_API_BASE,
                 timeout: float = _READ_TIMEOUT_SECONDS) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # -- transport ----------------------------------------------------------

    def _get(self, path: str) -> Dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
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
