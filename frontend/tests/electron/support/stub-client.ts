import type { StubState } from './stub-backend'

// Helpers for specs (run in the WDIO worker, a Node context) to read the stub backend
// state started in the launcher's onPrepare. The stub listens on loopback, so the
// worker reaches it over TCP even though it lives in a different process.

export const stubBaseUrl = process.env.POLICYFLOW_API_BASE_URL ?? 'http://127.0.0.1:59117'

export async function readStubState(): Promise<StubState> {
  const response = await fetch(`${stubBaseUrl}/__test/state`)
  return (await response.json()) as StubState
}

/**
 * Clear the Stage 8 stub bookkeeping (approval decisions, per-run stream counts,
 * pending emitters) WITHOUT touching the Phase 7 globals the crash/contract specs
 * assert on. Call this in a Stage 8 spec's `before` so repeated runs stay deterministic.
 */
export async function resetStubState(): Promise<void> {
  await fetch(`${stubBaseUrl}/__test/reset`, { method: 'POST' })
}
