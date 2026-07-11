import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AUTH_SESSION_KEY } from './auth-storage'
import { createAuthStore } from './auth-store'

const user = { id: 'u1', username: 'admin', displayName: 'Admin', roles: ['sys_admin'] as const }

describe('auth store', () => {
  beforeEach(() => sessionStorage.clear())

  it('restores a non-expired token in booting state without persisting user data', () => {
    sessionStorage.setItem(AUTH_SESSION_KEY, JSON.stringify({ accessToken: 'token', expiresAt: 2_000 }))
    const store = createAuthStore(sessionStorage, () => 1_000)
    expect(store.getState()).toMatchObject({ accessToken: 'token', expiresAt: 2_000, user: null, status: 'booting' })
  })

  it('rejects and removes an expired stored token', () => {
    sessionStorage.setItem(AUTH_SESSION_KEY, JSON.stringify({ accessToken: 'expired', expiresAt: 999 }))
    const store = createAuthStore(sessionStorage, () => 1_000)
    expect(store.getState().status).toBe('anonymous')
    expect(sessionStorage.getItem(AUTH_SESSION_KEY)).toBeNull()
  })

  it('stores only token metadata and clears it on logout', () => {
    const store = createAuthStore(sessionStorage, () => 1_000)
    store.authenticate('token', 2_000, { ...user, roles: [...user.roles] })
    expect(JSON.parse(sessionStorage.getItem(AUTH_SESSION_KEY) ?? '{}')).toEqual({ accessToken: 'token', expiresAt: 2_000 })
    expect(sessionStorage.getItem(AUTH_SESSION_KEY)).not.toContain('Admin')
    store.clearSession()
    expect(store.getState().status).toBe('anonymous')
    expect(sessionStorage.getItem(AUTH_SESSION_KEY)).toBeNull()
  })

  it('invalidates the token when it expires in memory', () => {
    const now = vi.fn(() => 1_000)
    const store = createAuthStore(sessionStorage, now)
    store.authenticate('token', 2_000, { ...user, roles: [...user.roles] })
    now.mockReturnValue(2_001)
    expect(store.getValidAccessToken()).toBeNull()
    expect(store.getState().status).toBe('anonymous')
  })
})
