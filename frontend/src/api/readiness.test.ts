import { describe, expect, it } from 'vitest'
import { apiReadiness, canCallApi } from './readiness'

describe('API readiness', () => {
  it('allows only implemented capabilities to send real requests', () => {
    expect(canCallApi('health', true)).toBe(true)
    expect(canCallApi('auth', true)).toBe(true)
    expect(canCallApi('users', true)).toBe(true)
    expect(canCallApi('chat', true)).toBe(false)
  })

  it('keeps future business capabilities contract-only', () => {
    expect(apiReadiness.knowledgeBases).toBe('contract-only')
    expect(apiReadiness.documents).toBe('contract-only')
    expect(apiReadiness.drafts).toBe('contract-only')
    expect(apiReadiness.eval).toBe('contract-only')
  })
})
