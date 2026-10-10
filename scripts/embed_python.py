#!/usr/bin/env python3
"""Embed a private Python runtime into a staged ASTRA bundle.

The desktop `.exe` is only the UI; the backend host launches
`resources/bridge/mt5_python/bridge_service.py` with an interpreter found under
`resources/python/`. This script downloads a standalone Windows build of
CPython and lays it down so that `resources/python/python.exe` exists — no
Python installation on the target machine, no MSI.

The bridge and the operator bot import only the standard library, so the stock
standalone build is enough; nothing needs `pip install`.

Usage:
    python scripts/embed_python.py --dest dist/ASTRA/resources/python
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request

# python-build-standalone (astral-sh) release. The install_only asset extracts
# to a single `python/` directory containing python.exe, Lib/, and DLLs/.
DEFAULT_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    "20241016/cpython-3.12.7+20241016-x86_64-pc-windows-msvc-install_only.tar.gz"
)


def _download(url: str, dest_path: str) -> None:
    print(f"[embed-python] downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as resp, open(dest_path, "wb") as out:
        shutil.copyfileobj(resp, out)


def _extract_into(tar_path: str, dest_dir: str) -> None:
    os.makedirs(dest_dir, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(tar_path, "r:gz") as tar:
            tar.extractall(tmp)
        # The archive has one top-level directory (e.g. "python/"); flatten it.
        entries = [os.path.join(tmp, e) for e in os.listdir(tmp)]
        roots = [e for e in entries if os.path.isdir(e)]
        src = roots[0] if len(roots) == 1 else tmp
        for name in os.listdir(src):
            shutil.move(os.path.join(src, name), os.path.join(dest_dir, name))


def embed(dest: str, url: str = DEFAULT_URL) -> str:
    exe = os.path.join(dest, "python.exe")
    if os.path.isfile(exe):
        print(f"[embed-python] already present: {exe}")
        return exe

    with tempfile.TemporaryDirectory() as tmp:
        tar_path = os.path.join(tmp, "python.tar.gz")
        _download(url, tar_path)
        _extract_into(tar_path, dest)

    if not os.path.isfile(exe):
        raise RuntimeError(f"python.exe not found after extraction: {exe}")
    print(f"[embed-python] runtime ready: {exe}")
    return exe


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Embed a private Python runtime")
    parser.add_argument("--dest", required=True,
                        help="target directory (becomes resources/python)")
    parser.add_argument("--url", default=DEFAULT_URL,
                        help="python-build-standalone install_only tarball URL")
    parser.add_argument("--python-exe", default=None,
                        help="unused; accepted so CI can pass a hint harmlessly")
    args = parser.parse_args(argv)

    try:
        embed(args.dest, args.url)
    except Exception as exc:  # noqa: BLE001 - surfaced to CI logs
        print(f"[embed-python] FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
