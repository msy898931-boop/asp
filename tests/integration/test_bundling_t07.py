#!/usr/bin/env python3
"""T07 - Bundle layout parity + staging verification (no broker required).

Checks that the packaging manifest agrees with the C++ runtime's declared
layout and with the API runtime manifest, then stages a bundle and verifies it.

This test reads (never modifies) production src/ so a drift between the
packaging source and the runtime constants is caught early.

Exit code 0 = all checks passed.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import bundle  # noqa: E402

_results = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))


def read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def main() -> int:
    manifest = bundle.load_manifest()
    rel = manifest["app_root_relative"]

    print("Parity: packaging manifest vs C++ runtime constants")
    pkg_h = read(os.path.join(REPO, "src/platform/windows/PackagingConfig.h"))
    pathres = read(os.path.join(REPO, "src/platform/windows/PathResolver.cpp"))
    resolver_h = read(os.path.join(REPO, "src/platform/windows/PathResolver.h"))

    check("bridgeRelativePath matches manifest",
          f'"{rel["bridge_dir"].split("resources/", 1)[1]}/bridge_service.py"' in pkg_h,
          rel["bridge_dir"] + "/bridge_service.py")
    check("PackagingConfig pythonRelativePath resolves under resources/python",
          'std::string pythonRelativePath = "python/python.exe";' in pkg_h)
    check("runtime-authoritative python dir is resources/python",
          '"python"' in pathres and 'resourceDir, "python"' in pathres)
    check("resource dir named resources",
          'resourceDir = join(out.appRootDir, "resources")' in pathres and
          "appRoot/resources" in resolver_h)
    check("bundlePython default true", "bundlePython = true" in pkg_h)
    check("allowSystemPython default false", "allowSystemPython = false" in pkg_h)

    print("Parity: packaging manifest vs RuntimeManifest.json")
    rm = json.loads(read(os.path.join(REPO, "src/api/RuntimeManifest.json")))
    check("bridge port matches", rm["components"]["python_bridge"]["port"] == manifest["ports"]["bridge"])
    check("frontend port matches", rm["components"]["frontend_api"]["port"] == manifest["ports"]["frontend_api"])
    check("bridge requires_manual_cmd false",
          rm["components"]["python_bridge"]["requires_manual_cmd"] is False)
    check("startup requires_manual_cmd false",
          rm["startup"]["depends_on_working_directory"] is False)

    print("Parity: bridge_files cover the real bridge directory")
    bridge_dir = os.path.join(REPO, "bridge", "mt5_python")
    # Tests are not shipped, so they are not part of the bundle contract.
    real = sorted(f for f in os.listdir(bridge_dir)
                  if os.path.isfile(os.path.join(bridge_dir, f))
                  and not f.endswith(".pyc") and not f.startswith("test_"))
    declared = sorted(manifest["bridge_files"])
    check("every declared bridge file exists", all(f in real for f in declared),
          f"missing: {[f for f in declared if f not in real]}")
    check("no bridge source file omitted from manifest",
          all(f in declared for f in real),
          f"undeclared: {[f for f in real if f not in declared]}")

    print("Staging: build and verify a bundle")
    out = tempfile.mkdtemp(prefix="astra-bundle-")
    try:
        bundle.stage(out)
        check("bundle report written", os.path.isfile(os.path.join(out, "bundle_report.json")))
        check("bridge_service.py staged",
              os.path.isfile(os.path.join(out, rel["bridge_dir"], "bridge_service.py")))
        check("resources/python dir staged", os.path.isdir(os.path.join(out, rel["python_runtime_dir"])))
        ok, problems = bundle.verify(out)
        check("bundle verifies clean", ok, "; ".join(problems))

        print("Launcher: the bundle must say which executable goes first")
        # Two exes ship side by side and the UI is useless alone: it polls
        # 127.0.0.1:8790 and shows an empty window until the host binds it.
        launcher = os.path.join(out, "ASTRA.bat")
        readme = os.path.join(out, "README.txt")
        check("launcher staged", os.path.isfile(launcher))
        check("readme staged", os.path.isfile(readme))
        with open(launcher, "rb") as fh:
            raw = fh.read()
        # A .bat carrying non-ASCII bytes is decoded with the console code page
        # and the line is mis-executed; the launcher must stay ASCII.
        check("launcher is pure ASCII",
              all(b < 128 for b in raw), f"non-ascii bytes: {[b for b in raw if b > 127][:5]}")
        # Every newline must be part of a CRLF pair; a bare LF is what a Windows
        # .bat mis-handles.
        lf = raw.count(b"\n")
        crlf = raw.count(b"\r\n")
        check("launcher uses CRLF", lf == crlf, f"lf={lf} crlf={crlf}")
        text = raw.decode("ascii")
        # Compare the two start commands, not the file offsets of the names:
        # the header comment mentions astra_desktop.exe before either runs.
        host_cmd = text.find('start "" /min "aura_backend_host.exe"')
        ui_cmd = text.find('start "" "astra_desktop.exe"')
        check("launcher starts the host before the UI",
              host_cmd != -1 and ui_cmd != -1 and host_cmd < ui_cmd,
              f"host-cmd@{host_cmd} ui-cmd@{ui_cmd}")
        with open(readme, encoding="utf-8") as fh:
            doc = fh.read()
        check("readme names both executables",
              "aura_backend_host.exe" in doc and "astra_desktop.exe" in doc)
        check("readme warns about the 409 single-poller rule",
              "409" in doc and "توكن" in doc)

        # Scope limit (F1), asserted rather than only documented: the stager
        # arranges the layout but places NO interpreter binary, so the bundle is
        # not runtime-complete until a real distribution is added.
        runtime_dir = os.path.join(out, rel["python_runtime_dir"])
        runtime_entries = os.listdir(runtime_dir)
        check("stager places no interpreter payload (layout-only scope)",
              runtime_entries == [], f"unexpected: {runtime_entries}")

        # A staged bundle must not carry forbidden artifacts.
        os.makedirs(os.path.join(out, rel["bridge_dir"], "__pycache__"), exist_ok=True)
        with open(os.path.join(out, rel["bridge_dir"], "__pycache__", "x.pyc"), "w") as fh:
            fh.write("x")
        ok2, problems2 = bundle.verify(out)
        check("verify catches forbidden artifacts", not ok2, "; ".join(problems2))

        # verify must also catch a bundle shipped without the launcher.
        os.remove(launcher)
        ok3, problems3 = bundle.verify(out)
        check("verify catches a missing launcher",
              not ok3 and any("ASTRA.bat" in p for p in problems3), "; ".join(problems3))
    finally:
        shutil.rmtree(out, ignore_errors=True)

    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
