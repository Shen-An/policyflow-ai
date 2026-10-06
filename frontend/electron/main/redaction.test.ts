import { describe, expect, it } from 'vitest'

import { ProxyError, redactString, toDesktopError } from './redaction'

describe('redactString', () => {
  it('masks bearer headers and JWT-like tokens', () => {
    const out = redactString('Authorization: Bearer abc.def.ghijklmnop and token eyJhbGciOi.JIUzI1NiIs.aGVsbG8xMjM')
    expect(out.toLowerCase()).not.toContain('bearer abc')
    expect(out).not.toContain('eyJhbGciOi.JIUzI1NiIs.aGVsbG8xMjM')
    expect(out).toContain('[redacted]')
  })

  it('masks windows paths, file urls and the backend origin', () => {
    const out = redactString(
      'failed at E:\\secret\\host\\creds.bin loading file:///C:/app/x and calling http://127.0.0.1:8000/api',
      { backendOrigin: 'http://127.0.0.1:8000' },
    )
    expect(out).not.toContain('E:\\secret')
    expect(out).not.toContain('file:///C:/app/x')
    expect(out).not.toContain('127.0.0.1:8000')
    expect(out).toContain('[backend]')
  })

  it('masks explicit literal secrets', () => {
    const out = redactString('the token is super-secret-123', { literals: ['super-secret-123'] })
    expect(out).not.toContain('super-secret-123')
  })
})

describe('toDesktopError', () => {
  it('preserves a ProxyError code/retryable and redacts its message', () => {
    const error = new ProxyError('UPSTREAM_ERROR', 'boom at http://127.0.0.1:8000', true)
    const result = toDesktopError(error, { backendOrigin: 'http://127.0.0.1:8000' })
    expect(result).toEqual({ code: 'UPSTREAM_ERROR', message: 'boom at [backend]', retryable: true })
    expect(Object.keys(result).sort()).toEqual(['code', 'message', 'retryable'])
  })

  it('collapses unknown errors to a generic internal error (no leakage)', () => {
    const result = toDesktopError(new Error('stack trace with E:\\host\\path and secret token'))
    expect(result.code).toBe('INTERNAL_ERROR')
    expect(result.retryable).toBe(false)
    expect(result.message).not.toContain('E:\\host')
    expect(result.message).not.toContain('secret token')
  })
})
