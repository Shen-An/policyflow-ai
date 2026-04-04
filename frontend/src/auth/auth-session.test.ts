import { HttpResponse, http } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'
import { apiClient } from '../api/client'
import { server } from '../mocks/server'
import { authStore } from './auth-store'
import { bindAuthSession } from './auth-session'

bindAuthSession()

describe('global auth session handling', () => {
  beforeEach(() => { authStore.clearSession(); sessionStorage.clear() })

  it('clears an authenticated session once a protected request returns 401', async () => {
    authStore.authenticate('expired-token', Date.now() + 60_000, { id: 'u1', username: 'admin', displayName: 'Admin', roles: ['sys_admin'] })
    server.use(http.get('*/api/private', ({ request }) => {
      expect(request.headers.get('authorization')).toBe('Bearer expired-token')
      return HttpResponse.json({ error: { code: 'AUTH_TOKEN_EXPIRED', message: 'Expired', details: null } }, { status: 401 })
    }))
    await expect(apiClient.request('/api/private')).rejects.toMatchObject({ kind: 'auth' })
    expect(authStore.getState().status).toBe('anonymous')
  })
})
