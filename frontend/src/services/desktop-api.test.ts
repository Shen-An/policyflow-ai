import { afterEach, describe, expect, it, vi } from 'vitest'

import type { RawPolicyflowBridge } from '../../electron/shared/types'
import { DesktopApiError, desktopApi, isDesktopRuntime } from './desktop-api'

function installBridge(partial: Partial<RawPolicyflowBridge>): void {
  ;(window as unknown as { policyflow?: Partial<RawPolicyflowBridge> }).policyflow = partial
}

afterEach(() => {
  delete (window as unknown as { policyflow?: unknown }).policyflow
  vi.restoreAllMocks()
})

describe('desktop-api', () => {
  it('reports no desktop runtime when the bridge is absent', () => {
    expect(isDesktopRuntime()).toBe(false)
  })

  it('throws a typed bridge-unavailable error when called outside the shell', async () => {
    await expect(desktopApi.auth.currentUser()).rejects.toBeInstanceOf(DesktopApiError)
    await expect(desktopApi.auth.currentUser()).rejects.toMatchObject({ code: 'DESKTOP_BRIDGE_UNAVAILABLE' })
  })

  it('unwraps a success envelope into the value', async () => {
    const login = vi.fn().mockResolvedValue({ ok: true, value: { user: { id: 'u1', username: 'e', displayName: 'E', roles: [] }, expiresAt: 10 } })
    installBridge({ auth: { login, logout: vi.fn(), currentUser: vi.fn() } })
    const session = await desktopApi.auth.login({ username: 'e', password: 'p' })
    expect(session.user.username).toBe('e')
    expect(login).toHaveBeenCalledWith({ username: 'e', password: 'p' })
  })

  it('throws a typed DesktopApiError on a failure envelope', async () => {
    const get = vi.fn().mockResolvedValue({ ok: false, error: { code: 'UPSTREAM_ERROR', message: 'boom', retryable: true } })
    installBridge({ runs: { start: vi.fn(), get, cancel: vi.fn(), subscribeEvents: vi.fn() } })
    await expect(desktopApi.runs.get('run-1')).rejects.toMatchObject({
      code: 'UPSTREAM_ERROR',
      message: 'boom',
      retryable: true,
    })
  })

  it('passes run-event subscription straight through to the bridge', async () => {
    const unsubscribe = vi.fn()
    const subscribeEvents = vi.fn().mockResolvedValue(unsubscribe)
    installBridge({ runs: { start: vi.fn(), get: vi.fn(), cancel: vi.fn(), subscribeEvents } })
    const handler = vi.fn()
    const result = await desktopApi.runs.subscribeEvents('run-1', handler)
    expect(subscribeEvents).toHaveBeenCalledWith('run-1', handler)
    expect(result).toBe(unsubscribe)
  })
})
