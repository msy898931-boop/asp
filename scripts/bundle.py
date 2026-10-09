#!/usr/bin/env python3
"""T07 - Stage the ASTRA application bundle (Python runtime + MT5 bridge).

Reads packaging/bundle_manifest.json (the single source of truth for the
installed layout) and stages a directory tree that the C++ runtime resolves
from the executable location:

    <out>/
      aura_backend_host        (optional: copied when --backend-host is given)
      resources/
        bridge/mt5_python/     (bridge files copied verbatim)
      runtime/python/          (bundled interpreter goes here)
      config/ data/ logs/

The stager never copies __pycache__, virtualenvs, or test scaffolding, and it
never falls back to a system interpreter: a release bundle is self-contained.
It does not need a Windows host or MetaTrader5 to run - it only arranges files.

Usage:
    python3 scripts/bundle.py --out dist/ASTRA [--backend-host path/to/bin]
    python3 scripts/bundle.py --out dist/ASTRA --verify
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
from typing import Dict, List

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(REPO_ROOT, "packaging", "bundle_manifest.json")


def load_manifest(path: str = MANIFEST) -> Dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _copy_bridge(manifest: Dict, out_dir: str) -> List[str]:
    src_dir = os.path.join(REPO_ROOT, "bridge", "mt5_python")
    rel_dir = manifest["app_root_relative"]["bridge_dir"]
    dst_dir = os.path.join(out_dir, rel_dir)
    os.makedirs(dst_dir, exist_ok=True)
    copied: List[str] = []
    for name in manifest["bridge_files"]:
        src = os.path.join(src_dir, name)
        if not os.path.isfile(src):
            raise FileNotFoundError(f"bridge file missing in source tree: {src}")
        shutil.copy2(src, os.path.join(dst_dir, name))
        copied.append(name)
    return copied


def _copy_telegram_bot(manifest: Dict, out_dir: str) -> List[str]:
    src_dir = os.path.join(REPO_ROOT, "bridge", "telegram_bot")
    rel_dir = manifest["app_root_relative"]["telegram_bot_dir"]
    dst_dir = os.path.join(out_dir, rel_dir)
    os.makedirs(dst_dir, exist_ok=True)
    copied: List[str] = []
    for name in manifest.get("telegram_bot_files", []):
        src = os.path.join(src_dir, name)
        if not os.path.isfile(src):
            raise FileNotFoundError(f"telegram bot file missing in source tree: {src}")
        shutil.copy2(src, os.path.join(dst_dir, name))
        copied.append(name)
    return copied


def _make_dirs(manifest: Dict, out_dir: str) -> List[str]:
    rel = manifest["app_root_relative"]
    made = []
    for key in ("python_runtime_dir", "config_dir", "data_dir", "log_dir"):
        d = os.path.join(out_dir, rel[key])
        os.makedirs(d, exist_ok=True)
        made.append(rel[key])
    return made


def stage(out_dir: str, backend_host: str | None = None) -> Dict:
    manifest = load_manifest()
    os.makedirs(out_dir, exist_ok=True)
    copied = _copy_bridge(manifest, out_dir)
    bot_copied = _copy_telegram_bot(manifest, out_dir)
    made = _make_dirs(manifest, out_dir)

    host_name = manifest.get("product", "ASTRA")
    backend_copied = None
    if backend_host:
        if not os.path.isfile(backend_host):
            raise FileNotFoundError(f"--backend-host not found: {backend_host}")
        dst = os.path.join(out_dir, os.path.basename(backend_host))
        shutil.copy2(backend_host, dst)
        os.chmod(dst, os.stat(dst).st_mode | stat.S_IEXEC)
        backend_copied = os.path.basename(dst)

    report = {
        "out_dir": os.path.abspath(out_dir),
        "product": host_name,
        "bridge_dir": manifest["app_root_relative"]["bridge_dir"],
        "bridge_files": copied,
        "telegram_bot_dir": manifest["app_root_relative"]["telegram_bot_dir"],
        "telegram_bot_files": bot_copied,
        "dirs_created": made,
        "backend_host": backend_copied,
    }
    with open(os.path.join(out_dir, "bundle_report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    _prune_forbidden(out_dir)
    return report


def _prune_forbidden(out_dir: str) -> None:
    """Remove __pycache__/venv/.pyc artifacts that a prior run may have left."""
    for root, dirs, files in os.walk(out_dir, topdown=False):
        for d in list(dirs):
            if d in ("__pycache__", ".venv", "venv"):
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)
        for f in files:
            if f.endswith(".pyc"):
                try:
                    os.remove(os.path.join(root, f))
                except OSError:
                    pass


def verify(out_dir: str) -> tuple[bool, List[str]]:
    """Check the staged tree against the manifest. Returns (ok, problems)."""
    manifest = load_manifest()
    rel = manifest["app_root_relative"]
    problems: List[str] = []

    bridge_dir = os.path.join(out_dir, rel["bridge_dir"])
    for name in manifest["bridge_files"]:
        if not os.path.isfile(os.path.join(bridge_dir, name)):
            problems.append(f"missing bridge file: {rel['bridge_dir']}/{name}")

    bot_dir = os.path.join(out_dir, rel["telegram_bot_dir"])
    for name in manifest.get("telegram_bot_files", []):
        if not os.path.isfile(os.path.join(bot_dir, name)):
            problems.append(f"missing telegram bot file: {rel['telegram_bot_dir']}/{name}")

    for key in ("python_runtime_dir", "config_dir", "data_dir", "log_dir"):
        if not os.path.isdir(os.path.join(out_dir, rel[key])):
            problems.append(f"missing directory: {rel[key]}")

    # No forbidden artifacts may leak into the bundle.
    for root, dirs, files in os.walk(out_dir):
        for d in list(dirs):
            if d in ("__pycache__", ".venv", "venv", ".git"):
                problems.append(f"forbidden directory in bundle: {os.path.join(root, d)}")
        for f in files:
            if f.endswith(".pyc"):
                problems.append(f"forbidden file in bundle: {os.path.join(root, f)}")

    return (not problems), problems


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage the ASTRA application bundle")
    parser.add_argument("--out", required=True, help="output bundle directory")
    parser.add_argument("--backend-host", default=None,
                        help="path to the aura_backend_host binary to include")
    parser.add_argument("--verify", action="store_true",
                        help="verify an already-staged bundle instead of staging")
    args = parser.parse_args(argv)

    if args.verify:
        ok, problems = verify(args.out)
        for p in problems:
            print(f"[FAIL] {p}", file=sys.stderr)
        print("bundle OK" if ok else f"bundle INVALID ({len(problems)} problems)")
        return 0 if ok else 1

    report = stage(args.out, args.backend_host)
    ok, problems = verify(args.out)
    print(json.dumps(report, indent=2))
    for p in problems:
        print(f"[FAIL] {p}", file=sys.stderr)
    if not ok:
        return 1
    print("bundle staged and verified OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
