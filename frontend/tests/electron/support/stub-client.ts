import type { StubState } from './stub-backend'

// Helpers for specs (run in the WDIO worker, a Node context) to read the stub backend
// state started in the launcher's onPrepare. The stub listens on loopback, so the
// worker reaches it over TCP even though it lives in a different process.

export const stubBaseUrl = process.env.POLICYFLOW_API_BASE_URL ?? 'http://127.0.0.1:59117'

export async function readStubState(): Promise<StubState> {
  const response = await fetch(`${stubBaseUrl}/__test/state`)
  return (await response.json()) as StubState
}
