import { HttpResponse, http } from 'msw'
import { describe, expect, it, vi } from 'vitest'
import { server } from '../mocks/server'
import { ApiClient } from './client'

describe('ApiClient', () => {
  it('normalizes a response through MSW', async () => {
    const client = new ApiClient({ baseUrl: 'http://localhost' })
    await expect(client.request<{ status: string }>('/health')).resolves.toEqual({ status: 'ok' })
  })

  it('injects bearer token and JSON headers centrally', async () => {
    server.use(http.post('http://localhost/api/example', async ({ request }) => {
      expect(request.headers.get('authorization')).toBe('Bearer token-value')
      expect(request.headers.get('content-type')).toContain('application/json')
      return HttpResponse.json({ success: true, data: { accepted: true } })
    }))
    const client = new ApiClient({ baseUrl: 'http://localhost', getAccessToken: () => 'token-value' })
    await expect(client.request<{ accepted: boolean }>('/api/example', { method: 'POST', body: JSON.stringify({ value: 1 }) })).resolves.toEqual({ accepted: true })
  })

  it('notifies session handling on 401 without hiding the error', async () => {
    server.use(http.get('http://localhost/api/private', () => HttpResponse.json(
      { error: { code: 'AUTH_REQUIRED', message: 'Authentication required', details: null } },
      { status: 401 },
    )))
    const onUnauthorized = vi.fn()
    const client = new ApiClient({ baseUrl: 'http://localhost', onUnauthorized })
    await expect(client.request('/api/private')).rejects.toMatchObject({ kind: 'auth', code: 'AUTH_REQUIRED' })
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it('maps transport failures to retryable network errors', async () => {
    const client = new ApiClient({ fetcher: vi.fn().mockRejectedValue(new TypeError('offline')) })
    await expect(client.request('/health')).rejects.toMatchObject({ kind: 'network', code: 'NETWORK_ERROR', retryable: true })
  })
})
