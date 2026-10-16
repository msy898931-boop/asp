#!/usr/bin/env python3
"""Where ASTRA lives — one place to change the host and the repository.

The deployed host and the GitHub repo were literals repeated across the bot
scripts, the watchdog, the workflows and the phone server, so renaming a
deployment meant a sweep through all of them. They now come from the
environment, with the current values as defaults:

  ASTRA_URL           the deployed service root (trailing slash optional)
  GITHUB_REPOSITORY   owner/repo — GitHub sets this in Actions automatically,
                      so the watchdog needs no literal there

A fork only sets these two variables; nothing else changes.
"""

from __future__ import annotations

import os

DEFAULT_URL = "https://astra-xlp9.onrender.com"
DEFAULT_REPO = "msy898931-boop/asp"


def _env(name: str, default: str) -> str:
    """Return ``name`` if it is set and non-blank, else ``default``.

    A workflow that writes ``ASTRA_URL: ${{ vars.ASTRA_URL }}`` sets the
    variable to the empty string when the repo variable is absent; plain
    ``os.environ.get(name, default)`` would then hand an empty host to the
    callers. Blank means "not set".
    """
    return (os.environ.get(name) or "").strip() or default


# Read once at import, matching how the callers already bind their constants.
URL = _env("ASTRA_URL", DEFAULT_URL).rstrip("/")
REPO = _env("GITHUB_REPOSITORY", DEFAULT_REPO)

# The phone server's optional access gate. Blank means the gate is off and the
# server is open, so monitoring needs no cookie; set, it must send one or every
# route but /api/v1/health answers 401. The name matches the cookie the server
# sets on a first `?k=` visit (see mobile/serve_mobile.py).
TOKEN = (os.environ.get("ASTRA_ACCESS_TOKEN") or "").strip()
COOKIE_NAME = "astra_key"


def auth_headers() -> dict[str, str]:
    """Headers that pass the phone server's access gate.

    Monitoring must keep working whether the gate is on or off, so this returns
    an empty mapping when no token is configured and a cookie when one is.
    """
    if TOKEN:
        return {"Cookie": "%s=%s" % (COOKIE_NAME, TOKEN)}
    return {}
