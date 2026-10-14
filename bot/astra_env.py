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

# Read once at import, matching how the callers already bind their constants.
URL = os.environ.get("ASTRA_URL", DEFAULT_URL).rstrip("/")
REPO = os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO)
