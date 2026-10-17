"""Web Push for the phone app — optional, and degrades to "not configured".

Real Web Push needs ECDH/AES-GCM crypto that is not in the Python standard
library, so this is the one place the shipped service leans on a third-party
package (``pywebpush``). The import is lazy and every entry point is guarded:
with no package, or no VAPID keys, the routes answer ``available: false`` and
the phone keeps using its in-app alerts — the same graceful degradation the
advisor uses when ``GROQ_API_KEY`` is unset.

The VAPID key pair is generated locally, for free, with no account:

    python3 -m py_vapid --applicationServerKey   # prints the public key
    python3 -m py_vapid --gen                    # writes private_key.pem

or with ``webpush.generate_keys()`` below. Set ``VAPID_PUBLIC_KEY`` and
``VAPID_PRIVATE_KEY`` (base64url, no padding) on the host; the private key must
stay constant across deploys or every subscription is invalidated.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# A VAPID claim must be a contact the push service can use (mailto: or https:).
# Apple is strict about it; the value is not a secret and never authenticates.
_DEFAULT_SUBJECT = "mailto:admin@example.com"

# The store is one JSON file on the host's persistent disk, next to the bot
# heartbeat, so subscriptions survive a redeploy.
_MAX_SUBS = 500
_MAX_BODY = 8192
_ENDPOINT_MAX = 2048

_store_lock = threading.Lock()
_pywebpush = None
_pywebpush_tried = False


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def public_key() -> str:
    return _env("VAPID_PUBLIC_KEY")


def _private_key() -> str:
    return _env("VAPID_PRIVATE_KEY")


def _subject() -> str:
    return _env("VAPID_SUBJECT") or _DEFAULT_SUBJECT


def _load_pywebpush():
    """Import pywebpush once. Returns the module, or None when unavailable."""
    global _pywebpush, _pywebpush_tried
    if _pywebpush_tried:
        return _pywebpush
    _pywebpush_tried = True
    try:
        import pywebpush
        _pywebpush = pywebpush
    except Exception:  # noqa: BLE001 - any import failure means "not configured"
        _pywebpush = None
    return _pywebpush


def configured() -> bool:
    """True only when both the keys and the crypto package are present."""
    return bool(public_key() and _private_key()) and _load_pywebpush() is not None


def _store_path() -> str:
    env = _env("ASTRA_PUSH_STORE")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), ".astra", "push_subscriptions.json")


def load_subscriptions() -> list[dict]:
    try:
        with open(_store_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    subs = data.get("subscriptions") if isinstance(data, dict) else None
    if not isinstance(subs, list):
        return []
    return [s for s in subs if isinstance(s, dict) and s.get("endpoint")]


def _write_subscriptions(subs: list[dict]) -> None:
    path = _store_path()
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".push-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"subscriptions": subs}, fh, ensure_ascii=False)
        os.replace(tmp, path)  # atomic: a reader never sees a half-written file
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def valid_subscription(sub: dict) -> bool:
    """A subscription is an https endpoint plus the two client keys."""
    if not isinstance(sub, dict):
        return False
    endpoint = sub.get("endpoint")
    keys = sub.get("keys")
    if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
        return False
    if len(endpoint) > _ENDPOINT_MAX:
        return False
    if not isinstance(keys, dict):
        return False
    return bool(keys.get("p256dh")) and bool(keys.get("auth"))


def subscribe(sub: dict, now: float | None = None) -> bool:
    """Store (or refresh) a subscription, keyed by endpoint. True if stored."""
    if not valid_subscription(sub):
        return False
    now = time.time() if now is None else now
    clean = {
        "endpoint": sub["endpoint"],
        "keys": {"p256dh": sub["keys"]["p256dh"], "auth": sub["keys"]["auth"]},
        "added_at": now,
    }
    with _store_lock:
        subs = [s for s in load_subscriptions() if s["endpoint"] != clean["endpoint"]]
        subs.append(clean)
        if len(subs) > _MAX_SUBS:
            subs = subs[-_MAX_SUBS:]
        _write_subscriptions(subs)
    return True


def unsubscribe(endpoint: str) -> bool:
    with _store_lock:
        subs = load_subscriptions()
        kept = [s for s in subs if s["endpoint"] != endpoint]
        if len(kept) == len(subs):
            return False
        _write_subscriptions(kept)
    return True


def _send_one(sub: dict, payload: dict) -> tuple[bool, int]:
    """Send one push. Returns (delivered, status). status 0 = transport error.

    A 404/410 means the push service has forgotten the subscription (the app
    was uninstalled, or the subscription expired), which is the signal to drop
    it rather than retry forever.
    """
    webpush = _load_pywebpush()
    if webpush is None:
        return False, 0
    try:
        webpush.webpush(
            subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]},
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=_private_key(),
            vapid_claims={"sub": _subject()},
            timeout=10,
        )
        return True, 201
    except webpush.WebPushException as exc:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", 0) or 0
        return False, status
    except Exception:  # noqa: BLE001 - never let one bad endpoint stop the rest
        return False, 0


def broadcast(payload: dict) -> dict:
    """Push to every subscriber, dropping the ones the service reports gone."""
    if not configured():
        return {"configured": False, "sent": 0, "removed": 0}
    sent = 0
    dead: list[str] = []
    for sub in load_subscriptions():
        ok, status = _send_one(sub, payload)
        if ok:
            sent += 1
        elif status in (404, 410):
            dead.append(sub["endpoint"])
    if dead:
        with _store_lock:
            subs = load_subscriptions()
            kept = [s for s in subs if s["endpoint"] not in set(dead)]
            _write_subscriptions(kept)
    return {"configured": True, "sent": sent, "removed": len(dead)}


def body() -> bytes:
    """The /push.json body: whether push works, and the key the phone needs."""
    return json.dumps({
        "available": configured(),
        "public_key": public_key() if configured() else "",
        "subscribers": len(load_subscriptions()),
    }, ensure_ascii=False).encode("utf-8")


def test_broadcast() -> dict:
    """Send one self-describing notification to every subscriber.

    Turning alerts on is silent, so the operator has no way to tell a working
    background path from one that never registered. This is the deliberate
    "prove it" button: the same send path the bot uses, with a payload that
    says it is a test.
    """
    return broadcast({
        "title": "ASTRA",
        "body": "الإشعارات بالخلفية تعمل ✅",
        "tag": "astra-test",
    })


def generate_keys() -> tuple[str, str]:
    """A fresh VAPID pair as (public, private), base64url without padding.

    Local and free: no account, no service, no network. Keep the private key
    constant across deploys.
    """
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
    )
    from py_vapid import Vapid

    vapid = Vapid()
    vapid.generate_keys()
    raw_public = vapid.public_key.public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)
    raw_private = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
    return b64(raw_public), b64(raw_private)
