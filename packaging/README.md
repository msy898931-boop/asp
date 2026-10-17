# ASTRA / AURA — Packaging & Python Bundling (T07)

This directory owns how the **bundled Python runtime** and the **MT5 bridge**
are assembled into the installed desktop layout that the C++ runtime expects.

It is the source-side counterpart to the runtime path logic in
`src/platform/windows/`:

| Runtime consumer | Contract |
|---|---|
| `PathResolver.{h,cpp}` | all paths derive from the executable location, never the CWD |
| `BundleLocator.{h,cpp}` | finds `runtime/python/…` (bundled) and `resources/bridge/mt5_python/bridge_service.py`; system interpreter is development-only |
| `PackagingConfig.{h,cpp}` | declares `bridgeRelativePath`, `pythonRelativePath`, `bundlePython=true`, `allowSystemPython=false` |
| `src/api/RuntimeManifest.json` | declares `requires_manual_cmd: false`, `managed_by_application: true` |

## Installed layout

```text
ASTRA/
  ASTRA.bat                    # one-click launcher: host first, then the UI
  README.txt                   # how to run it (Arabic, for the end user)
  astra_desktop.exe            # Qt UI (talks to 127.0.0.1:8790)
  aura_backend_host.exe        # backend host entry point
  resources/
    bridge/mt5_python/         # bundled bridge (copied verbatim)
      bridge_service.py
      mt5_client.py
      read_candles.py
      schemas.py
      requirements.txt
      README.md
    python/                    # bundled interpreter (self-contained)
  config/
  data/
  logs/
```

`ASTRA.bat` exists because the bundle ships **two** executables and the UI is
useless alone: it polls `127.0.0.1:8790` and shows an empty window until the
host binds that port. The launcher starts the host, waits four seconds, then
opens the UI. It is ASCII-only with CRLF endings, since a `.bat` carrying
non-ASCII bytes is decoded with the console code page and mis-executed.

The layout is declared once in `bundle_manifest.json`. `scripts/bundle.py`
stages it, writes the launcher and the README, and verifies it; nothing
resolves a path from the CWD.

## Bundling

```text
python3 scripts/bundle.py --out dist/ASTRA
python3 scripts/bundle.py --out dist/ASTRA --backend-host build/aura_backend_host
python3 scripts/bundle.py --out dist/ASTRA --verify
```

The stager copies the bridge files, creates `resources/python`, `config`,
`data`, and `logs`, and refuses to let `__pycache__`, virtualenvs, `.git`, or
`*.pyc` leak into the bundle.

## The bundled interpreter

A release bundle is **self-contained**: it ships its own interpreter so the end
user never installs Python and never opens a CMD window.

- Windows: the official embeddable CPython distribution (or an equivalent
  self-contained build) unpacked under `resources/python/` as `python.exe`.
- POSIX (development/CI): `resources/python/python3`, or a system interpreter
  passed explicitly for local smoke tests only.

`BundleLocator` treats a system interpreter as `SYSTEM_FALLBACK` and only when
`allowSystemFallback` is set; `PackagingConfig::validate` rejects a config that
neither bundles nor permits a fallback.

## Runtime dependencies

`requirements-runtime.txt` mirrors `bridge/mt5_python/requirements.txt` and is
installed into the bundled interpreter at build time. The bridge imports only
the standard library plus `MetaTrader5` (which pulls in `numpy`).

## Known discrepancies (resolved 2026-10-08)

All three T07-reported discrepancies were reconciled under T08 (C-1/C-2/C-3),
authorized by the human in the Phase 6.1 closing directive. Minimum change only;
no strategy/decision/risk/bridge logic touched.

1. **Python runtime path (C-1) — FIXED.** `PackagingConfig.h` now declares
   `pythonRelativePath = "python/python.exe"`, so `interpreterPath()` resolves to
   `resources/python/python.exe` — matching `PathResolver::resolve`
   (`resources/python`), `BundleLocator.cpp`, `BundleLocator.h`, and `HANDOFF.md`.
   Previously it produced `resources/runtime/python/python.exe`, a path the
   locator never searches.

2. **pandas dependency (C-2) — REMOVED.** `defaultPackagingConfig()` no longer
   declares `pandas>=2.0`. Neither the bridge nor the runtime requirements import
   pandas, so the C++ dependency list now matches reality
   (`MetaTrader5` + `numpy` only).

3. **numpy pin drift (C-3) — RECONCILED.** All three files now declare
   `numpy>=1.23`: `src/platform/windows/PackagingConfig.cpp`,
   `bridge/mt5_python/requirements.txt`, and `packaging/requirements-runtime.txt`.
   `>=1.23` is the authoritative constraint (MetaTrader5's requirement, and the
   version actually validated, 2.4.6, satisfies it). The looseness is intentional:
   nothing here imports numpy directly.
