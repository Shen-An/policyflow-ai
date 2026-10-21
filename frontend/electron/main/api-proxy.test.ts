import { describe, expect, it } from 'vitest'

import { ApiProxy, resolveIdempotencyKey, toRunRequestBody } from './api-proxy'
import type { CredentialVault } from './credentials'

// The proxy is the only component that talks to the backend, so these tests pin
// the *wire* contract: the desktop request shape must match what
// `backend/app/api/routes_runs.py` actually accepts, not what the renderer
// happens to send.

type Recorded = { url: string; init: RequestInit }

function fakeVault(token: string | null = 'access-token-1'): CredentialVault {
  let current = token
  return {
    getAccessToken: () => current,
    setAccessToken: (value: string) => {
      current = value
    },
    clearAccessToken: () => {
      current = null
    },
    saveRefreshToken: () => undefined,
    clear: () => {
      current = null
    },
  } as unknown as CredentialVault
}

function proxyWith(payload: unknown, token: string | null = 'access-token-1') {
  const calls: Recorded[] = []
  const fetchImpl = (async (url: string, init: RequestInit) => {
    calls.push({ url: String(url), init })
    return {
      ok: true,
      status: 201,
      text: async () => JSON.stringify(payload),
    } as unknown as Response
  }) as unknown as typeof fetch
  return { proxy: new ApiProxy({ backendBaseUrl: 'http://127.0.0.1:8000/', vault: fakeVault(token), fetchImpl }), calls }
}

function bodyOf(call: Recorded): Record<string, unknown> {
  return JSON.parse(String(call.init.body)) as Record<string, unknown>
}

function headersOf(call: Recorded): Record<string, string> {
  return (call.init.headers ?? {}) as Record<string, string>
}

describe('ApiProxy.startRun', () => {
  it('sends the durable run contract: kind + payload + lane, key in a header', async () => {
    const { proxy, calls } = proxyWith({
      run_id: 'run-1',
      job_id: 'job-1',
      state: 'queued',
      kind: 'file_workflow',
      created_at: '2026-10-21T00:00:00Z',
    })

    const summary = await proxy.startRun({
      kind: 'file_workflow',
      input: { question: '差旅报销需要哪些材料？' },
      knowledgeBaseIds: ['kb-1'],
    })

    expect(calls).toHaveLength(1)
    expect(calls[0].url).toBe('http://127.0.0.1:8000/api/v2/runs')
    expect(calls[0].init.method).toBe('POST')

    const body = bodyOf(calls[0])
    // Backend RunRequest: no top-level `input` / `knowledge_base_ids` / `idempotency_key`.
    expect(body.kind).toBe('file_workflow')
    expect(body.payload).toEqual({
      question: '差旅报销需要哪些材料？',
      knowledge_base_ids: ['kb-1'],
    })
    expect(body.priority_lane).toBe('default')
    expect(body.max_attempts).toBe(5)
    expect(body).not.toHaveProperty('input')
    expect(body).not.toHaveProperty('knowledge_base_ids')
    expect(body).not.toHaveProperty('idempotency_key')

    const headers = headersOf(calls[0])
    const key = headers['Idempotency-Key']
    expect(key).toBeTypeOf('string')
    expect(key.length).toBeGreaterThanOrEqual(16)
    expect(key.length).toBeLessThanOrEqual(128)
    expect(headers.Authorization).toBe('Bearer access-token-1')

    // The run view reports `state`; the typed summary must surface it.
    expect(summary).toEqual({
      runId: 'run-1',
      status: 'queued',
      kind: 'file_workflow',
      createdAt: '2026-10-21T00:00:00Z',
    })
  })

  it('forwards a caller idempotency key and lane overrides verbatim', async () => {
    const { proxy, calls } = proxyWith({ run_id: 'run-2', state: 'queued' })

    await proxy.startRun({
      kind: 'chat',
      input: {},
      idempotencyKey: 'desktop-retry-0001',
      priorityLane: 'interactive',
      maxAttempts: 3,
    })

    expect(headersOf(calls[0])['Idempotency-Key']).toBe('desktop-retry-0001')
    const body = bodyOf(calls[0])
    expect(body.priority_lane).toBe('interactive')
    expect(body.max_attempts).toBe(3)
    expect(body.payload).toEqual({})
  })
})

describe('toRunRequestBody', () => {
  it('omits knowledge_base_ids when no knowledge base is selected', () => {
    const body = toRunRequestBody({ kind: 'chat', input: { question: 'hello' } })
    expect(body.payload).toEqual({ question: 'hello' })
  })
})

describe('resolveIdempotencyKey', () => {
  it('keeps a key inside the backend window', () => {
    expect(resolveIdempotencyKey('  0123456789abcdef  ')).toBe('0123456789abcdef')
  })

  it('mints a fresh backend-safe key for short or missing keys', () => {
    const fromShort = resolveIdempotencyKey('short')
    expect(fromShort.length).toBeGreaterThanOrEqual(16)
    expect(fromShort.length).toBeLessThanOrEqual(128)
    expect(fromShort).not.toBe('short')

    const generated = resolveIdempotencyKey(undefined)
    expect(generated).toMatch(/^run-[0-9a-f-]{36}$/u)
  })
})

describe('ApiProxy.login', () => {
  it('forwards tenant_code for multi-tenant deployments', async () => {
    const { proxy, calls } = proxyWith({
      access_token: 'tok',
      expires_in: 60,
      user: { id: 'u1', username: 'alice', display_name: 'Alice', roles: ['employee'] },
    })

    const session = await proxy.login({ username: 'alice', password: 'secret', tenantCode: 'acme' })

    expect(calls[0].url).toBe('http://127.0.0.1:8000/api/auth/login')
    expect(bodyOf(calls[0])).toEqual({
      username: 'alice',
      password: 'secret',
      tenant_code: 'acme',
    })
    expect(session.user.username).toBe('alice')
  })

  it('omits tenant_code when the caller does not select a tenant', async () => {
    const { proxy, calls } = proxyWith({
      access_token: 'tok',
      expires_in: 60,
      user: { id: 'u1', username: 'alice' },
    })

    await proxy.login({ username: 'alice', password: 'secret' })

    expect(bodyOf(calls[0])).toEqual({ username: 'alice', password: 'secret' })
  })
})
