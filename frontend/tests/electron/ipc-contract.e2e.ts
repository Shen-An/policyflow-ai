import { browser, expect } from '@wdio/globals'

import { readStubState } from './support/stub-client'

// T111 — the IPC contract: operation-specific schema validation, sender-origin trust,
// in-flight request cancellation, and redacted errors. Driven against the real app and
// the deterministic stub backend. (The origin-REJECTION path for an untrusted frame is
// additionally unit-tested in electron/main/origin.test.ts, since CSP + navigation
// locking make it impossible to even create a foreign frame to call from here.)

type Envelope<T> =
  | { ok: true; value: T }
  | { ok: false; error: { code: string; message: string; retryable: boolean } }

describe('T111 IPC contract', () => {
  before(async () => {
    await browser.waitUntil(
      async () =>
        (await browser.execute(
          () => typeof (window as unknown as { policyflow?: unknown }).policyflow,
        )) === 'object',
      { timeout: 15_000, timeoutMsg: 'window.policyflow was never exposed' },
    )
  })

  it('accepts calls from the trusted renderer origin and injects auth in main only', async () => {
    const login = (await browser.execute(() =>
      (window as unknown as {
        policyflow: { auth: { login: (r: unknown) => Promise<unknown> } }
      }).policyflow.auth.login({ username: 'employee', password: 'secret-pw' }),
    )) as Envelope<{ user: { username: string }; expiresAt: number }>

    expect(login.ok).toBe(true)
    if (!login.ok) return
    expect(login.value.user.username).toBe('employee')
    expect(typeof login.value.expiresAt).toBe('number')
    // The access/refresh token must never cross the boundary into the renderer.
    const serialized = JSON.stringify(login.value)
    expect(serialized.includes('token')).toBe(false)
    expect(serialized.includes('stub-access-token')).toBe(false)

    const me = (await browser.execute(() =>
      (window as unknown as {
        policyflow: { auth: { currentUser: () => Promise<unknown> } }
      }).policyflow.auth.currentUser(),
    )) as Envelope<{ username: string }>
    expect(me.ok).toBe(true)
    if (me.ok) expect(me.value.username).toBe('employee')

    // Main attached the bearer when proxying /me; the renderer never saw it.
    const state = await readStubState()
    expect(state.authHeaderWasBearer).toBe(true)
  })

  it('rejects payloads that violate each operation-specific schema', async () => {
    const results = await browser.execute(async () => {
      const p = (window as unknown as {
        policyflow: Record<string, Record<string, (arg: unknown) => Promise<unknown>>>
      }).policyflow
      return {
        login: await p.auth.login({ username: '', password: '' }),
        material: await p.materials.declare({ name: 'x', purpose: 'y', contentHash: 'not-a-hash' }),
        approval: await p.approvals.decide({
          runId: 'r',
          approvalId: 'a',
          decision: 'maybe',
          actionDigest: 'short',
        }),
        workspace: await p.workspace.select({ runId: 'r', materialVersionIds: [] }),
      }
    })
    for (const key of ['login', 'material', 'approval', 'workspace'] as const) {
      const envelope = results[key] as Envelope<unknown>
      expect(envelope.ok).toBe(false)
      if (!envelope.ok) expect(envelope.error.code).toBe('INVALID_PAYLOAD')
    }
  })

  it('cancels an in-flight run-event stream when the renderer unsubscribes', async () => {
    const received = await browser.execute(async () => {
      const p = (window as unknown as {
        policyflow: { runs: { subscribeEvents: (id: string, cb: (e: unknown) => void) => Promise<() => void> } }
      }).policyflow
      const events: unknown[] = []
      const unsubscribe = await p.runs.subscribeEvents('run-stream', (event) => events.push(event))
      ;(window as unknown as { __unsub: () => void }).__unsub = unsubscribe
      const start = Date.now()
      while (events.length < 1 && Date.now() - start < 10_000) {
        await new Promise((resolve) => setTimeout(resolve, 50))
      }
      return events.length
    })
    expect(received >= 1).toBe(true)

    await browser.waitUntil(async () => (await readStubState()).sseOpened >= 1, {
      timeout: 10_000,
      timeoutMsg: 'backend never saw the event stream open',
    })

    // Unsubscribe → main aborts the SSE request → the backend connection closes.
    await browser.execute(() => (window as unknown as { __unsub: () => void }).__unsub())
    await browser.waitUntil(async () => (await readStubState()).sseOpen === 0, {
      timeout: 10_000,
      timeoutMsg: 'event stream was not cancelled after unsubscribe',
    })
    expect((await readStubState()).sseOpen).toBe(0)
  })

  it('returns redacted errors with no stack, host path, backend origin or token', async () => {
    const probe = await browser.execute(async () => {
      const p = (window as unknown as {
        policyflow: { runs: { get: (id: string) => Promise<{ ok: boolean; error?: Record<string, unknown> }> } }
      }).policyflow
      const result = await p.runs.get('run-does-not-exist')
      return {
        ok: result.ok,
        keys: result.error ? Object.keys(result.error).sort() : [],
        serialized: JSON.stringify(result.error ?? {}),
      }
    })
    expect(probe.ok).toBe(false)
    expect(probe.keys).toEqual(['code', 'message', 'retryable'])
    expect(probe.serialized.includes('127.0.0.1')).toBe(false)
    expect(probe.serialized.toLowerCase().includes('stub-access-token')).toBe(false)
    expect(/[A-Za-z]:\\/u.test(probe.serialized)).toBe(false)
    expect(probe.serialized.includes(' at ')).toBe(false)
  })
})
