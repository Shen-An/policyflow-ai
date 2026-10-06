# Stage 7 — Electron security boundary: infrastructure probe

Date: 2026-10-06 · Host: win32 · Node v22.17.0 · npm 10.9.2

## Can Electron really run on this machine? YES.

Unlike the Stage 6 gVisor runtime (which had no K8s cluster and was marked `[~]`),
the Stage 7 Electron security boundary **runs for real** here. Every Phase 7 security
E2E test (T110–T113) launched the actual hardened Electron app and passed — no
false-green, no simulated runtime.

| Probe | Result |
|---|---|
| `electron` binary | `node_modules/electron/dist/electron.exe` present (Electron **44.3.0**), downloaded during `npm install`. |
| Launch smoke test | A hardened `BrowserWindow` (contextIsolation + sandbox) loads and renders. Chromium **152.0.7977.78**. |
| WDIO driver | `@wdio/electron-service` **10.3.0** drives Electron via the CDP bridge (`@wdio/native-cdp-bridge` + `puppeteer-core`). No separate ChromeDriver download was required — Electron's bundled driver is used. |
| `test:electron` result | **4 spec files / 14 tests — all passing** (see `test-electron-output.txt`). |

## Two issues found and fixed during bring-up (both honest infra fixes, not shortcuts)

1. **Broken `@wdio/electron-service@10.1.0` release.** Its ESM imported
   `installMockSyncOverride` from `@wdio/native-utils@2.4.0`, which that pinned version
   does not export → launcher failed to initialise. Fixed by upgrading the service to
   **10.3.0** (pins the consistent `@wdio/native-utils@2.7.0`).
2. **Windows `appEntryPoint` ChromeDriver crash.** In `appEntryPoint` mode the service
   launches `node_modules/.bin/electron.CMD`, a batch shim whose process exits
   immediately, so ChromeDriver reports "Chrome failed to start: crashed." Fixed on
   win32 by pointing `appBinaryPath` at the real `electron.exe` and passing the bundled
   main via `appArgs: ['--app=<dist-electron/main/index.cjs>']` (see
   `wdio.electron.conf.ts`). Other platforms keep the normal `appEntryPoint` path.

## What is real vs. a test fixture

- **Real production code under test:** the entire capability boundary — hardened
  `BrowserWindow` webPreferences, the `app://` privileged-scheme renderer, the strict
  CSP (meta + protocol header + session header), navigation / new-window / webview
  denial, the external-link allowlist, operation-specific schema-validated IPC with
  sender-origin checks, the `safeStorage` credential vault, the authenticated API/SSE
  proxy with cancellation + error redaction, and the renderer-crash abort of in-flight
  privileged requests.
- **Deterministic fixture:** the backend the main-side proxy talks to during the E2E is
  an in-process stub (`tests/electron/support/stub-backend.ts`) so that token
  injection, SSE cancellation, error redaction, and crash-time request abort can be
  observed deterministically. The stub being a fixture does **not** weaken any
  assertion — every assertion is about `main`/`preload`/`renderer` behaviour, with the
  renderer never seeing a token, raw IPC, Node, the filesystem, or a foreign origin.

## The one honest `[~]` for Phase 7: signed production packaging

Producing a **signed** desktop installer via `electron-builder` is **not run here** —
this machine has no enterprise code-signing certificate (Windows Authenticode / Apple
Developer ID). This mirrors the Stage 6 gVisor boundary.

What IS implemented and verified without certs:
- `electron-builder.yml` sets `forceCodeSigning: true` and hardening fuses
  (`runAsNode: false`, ASAR integrity, `onlyLoadAppFromAsar`).
- `scripts/verify-electron-signing.mjs` runs before packaging and **refuses** an
  unsigned or placeholder configuration, and **accepts** real credentials
  (demonstrated three ways in `signing-gate.txt`).

Producing an actual signed artifact requires real `CSC_LINK` / `APPLE_ID` credentials,
so it is correctly refused on this machine. The guardrail itself is fully exercised.
