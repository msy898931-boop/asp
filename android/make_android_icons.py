#!/usr/bin/env python3
"""Render the ASTRA launcher icons for Android from the same gauge artwork.

Reuses mobile/icons/make_icons.py (standard library only) so the launcher icon
and the PWA icon can never drift apart. Writes the density buckets Android
expects, plus the round variant referenced by the manifest.

Run: python3 android/make_android_icons.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "mobile", "icons"))

import make_icons  # noqa: E402  (path set up above)

# bucket -> launcher icon edge length in pixels
DENSITIES = {
    "mdpi": 48,
    "hdpi": 72,
    "xhdpi": 96,
    "xxhdpi": 144,
    "xxxhdpi": 192,
}


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    res = os.path.join(here, "app", "src", "main", "res")
    for bucket, size in DENSITIES.items():
        out_dir = os.path.join(res, f"mipmap-{bucket}")
        os.makedirs(out_dir, exist_ok=True)
        # Slightly inset so the outer ring survives a round mask.
        png = make_icons.draw(size, inset=0.88)
        for name in ("ic_launcher.png", "ic_launcher_round.png"):
            path = os.path.join(out_dir, name)
            with open(path, "wb") as fh:
                fh.write(png)
            print(f"wrote {path}")


if __name__ == "__main__":
    main()
