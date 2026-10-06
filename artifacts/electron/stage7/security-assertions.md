# Stage 7 — Electron capability boundary: assertion map

Independent Test (spec): *"In a real Electron app, verify that illegal IPC
schema/origin, navigation, new windows, and Node/file/token access are all denied;
a renderer crash neither approves nor submits an action, and the server-side run stays
authoritative."* — **verified for real** (14 E2E tests + 37 unit/contract tests).

## E2E tests (real Electron, `npm run test:electron`) — 14 passing

### T110 `security.e2e.ts` — renderer capability sandbox (4)
- `webPreferences` on the live window: `contextIsolation=true`, `sandbox=true`,
  `nodeIntegration=false`, `webviewTag=false`, `webSecurity≠false`.
- Only the minimal typed bridge is exposed; `require`/`process`/`module`/`global`/
  `Buffer`/`electron`/`ipcRenderer` are all absent on `window`.
- No raw IPC (`invoke`/`send`/`on`), no filesystem accessor, no token getter on the bridge.
- `system.info()` reports `hasNodeAccess: false`.

### T111 `ipc-contract.e2e.ts` — IPC contract (4)
- Trusted renderer origin accepted; `login` → identity + expiry only, **no token** in
  the renderer; main injects the bearer when proxying `/me` (`authHeaderWasBearer`).
- Per-operation schema rejects bad payloads (`login`, `materials.declare`,
  `approvals.decide`, `workspace.select`) with `INVALID_PAYLOAD`.
- In-flight run-event stream is cancelled when the renderer unsubscribes (backend SSE
  connection closes).
- Errors are redacted: envelope is exactly `{code, message, retryable}`, with no stack,
  host path, backend origin, or token.
- *(Origin-rejection for an untrusted frame is unit-tested in `origin.test.ts`; CSP +
  navigation locking make a foreign calling frame impossible to construct in-app.)*

### T112 `navigation.e2e.ts` — navigation & content security (5)
- Strict CSP blocks dynamically injected inline scripts (`securitypolicyviolation` on
  `script-src`); the renderer document carries the CSP header.
- Full-page navigation to a remote origin is blocked (URL stays `app://local`).
- `window.open` is denied (returns null; window count stays 1).
- Only allowlisted https hosts open externally (`opened:true`); others are refused
  (`opened:false`).

### T113 `renderer-crash.e2e.ts` — crash containment (1)
- With an approval decision in flight, forcefully crashing the renderer aborts the
  request in main (`approvalAborted`), **never commits** an approval
  (`approvalCommitted=false`), and the durable server run remains `running`.

## Unit / contract tests (`npm run test`, no app launch) — 37 passing

- `hardening.test.ts` — frozen hardened `webPreferences`; strict CSP string (no
  `unsafe-inline`/`unsafe-eval` on scripts; `object-src/frame-src/base-uri/form-action`
  locked; `connect-src 'self'`); external allowlist; same-renderer navigation rule.
- `origin.test.ts` — trusted `app://local` vs. foreign/data/blank/empty senders; dev
  server trusted only when configured.
- `schemas.test.ts` — every operation has a dedicated schema; channels are namespaced;
  SHA-256 digests, http(s)-only external URLs, non-empty version lists enforced; extra
  props on parameterless ops rejected.
- `redaction.test.ts` — bearer/JWT/host-path/backend-origin/literal masking; unknown
  errors collapse to a generic internal error.
- `run-mapper.test.ts` — typed RunEvent + summary mappings, defensive against missing fields.
- `credentials.test.ts` — refresh token encrypted at rest (never plaintext), refuses
  without OS encryption, in-memory access-token expiry, full clear.
- `src/services/desktop-api.test.ts` — envelope unwrap, typed `DesktopApiError`,
  bridge-unavailable guard, event-subscription pass-through.

## Signing gate (`signing-gate.txt`)
`verify-electron-signing.mjs` rejects missing (exit 1) and placeholder (exit 1) signing
config, and accepts real credentials (exit 0). `electron-builder.yml` additionally sets
`forceCodeSigning: true` and hardening fuses.

## Honest boundary
Signed production **packaging** is not run here (no code-signing certificate on this
machine) — see `infra-probe.md`. The signing gate and builder hardening are implemented
and the gate is fully exercised; only the final signed-artifact step requires real certs.
