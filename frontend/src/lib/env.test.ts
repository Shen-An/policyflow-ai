import { describe, expect, it } from 'vitest'
import { parseEnv } from './env'

describe('environment contract', () => {
  it('uses safe same-origin defaults', () => {
    expect(parseEnv({})).toEqual({ apiBaseUrl: '', enableMsw: false, requestTimeoutMs: 10_000 })
  })
  it('rejects production mock activation', () => {
    expect(() => parseEnv({ VITE_ENABLE_MSW: 'true' }, true)).toThrow('MSW cannot be enabled in a production build.')
  })
  it('rejects unsupported base URL protocols', () => {
    expect(() => parseEnv({ VITE_API_BASE_URL: 'file:///secret' })).toThrow()
  })
})
