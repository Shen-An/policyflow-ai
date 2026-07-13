import { describe, expect, it } from 'vitest'
import { apiReadiness, canCallApi } from './readiness'

describe('API readiness', () => {
  it('allows only implemented capabilities to send real requests', () => {
    expect(canCallApi('health', true)).toBe(true)
    expect(canCallApi('auth', true)).toBe(true)
    expect(canCallApi('users', true)).toBe(true)
    expect(canCallApi('chat', true)).toBe(true)
  })

  it('opens the implemented F3 through F6 capabilities', () => {
    expect(apiReadiness.knowledgeBases).toBe('implemented')
    expect(apiReadiness.documents).toBe('implemented')
    expect(apiReadiness.feedback).toBe('implemented')
    expect(apiReadiness.drafts).toBe('implemented')
    expect(apiReadiness.memory).toBe('implemented')
    expect(apiReadiness.faq).toBe('implemented')
    expect(apiReadiness.audit).toBe('implemented')
    expect(apiReadiness.eval).toBe('implemented')
    expect(apiReadiness.skills).toBe('implemented')
    expect(apiReadiness.tools).toBe('implemented')
    expect(apiReadiness.mcp).toBe('implemented')
  })
})
