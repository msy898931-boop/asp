#!/usr/bin/env python3
"""Guard the offline shell: every asset sw.js precaches must exist on disk.

A name in the service worker's ASSETS list that has no file makes the install
event reject, so the app silently stops working offline — a failure that is
invisible until someone is offline. This checks it in CI instead.

Run: python3 mobile/check_shell.py   (exit 1 on a missing asset)
"""

from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SW = os.path.join(HERE, "sw.js")


def shell_assets(source: str) -> list[str]:
    """The string entries of the ASSETS array, in order."""
    match = re.search(r"const\s+ASSETS\s*=\s*\[(.*?)\]", source, re.S)
    if not match:
        return []
    return re.findall(r'"([^"]+)"', match.group(1))


def main() -> int:
    with open(SW, encoding="utf-8") as fh:
        assets = shell_assets(fh.read())
    if not assets:
        print("check_shell: no ASSETS array found in sw.js", file=sys.stderr)
        return 1

    missing = []
    for asset in assets:
        rel = asset.lstrip("./") or "index.html"
        target = os.path.normpath(os.path.join(HERE, rel))
        if not os.path.isfile(target):
            missing.append(asset)

    print("check_shell: %d assets, %d missing" % (len(assets), len(missing)))
    for asset in missing:
        print("  MISSING %s" % asset, file=sys.stderr)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
