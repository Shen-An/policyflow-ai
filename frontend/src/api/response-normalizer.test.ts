import { describe, expect, it } from 'vitest'
import { normalizeError, normalizeSuccess } from './response-normalizer'

describe('response normalizer', () => {
  it('accepts raw and envelope success payloads in one place', () => {
    expect(normalizeSuccess({ status: 'ok' })).toEqual({ status: 'ok' })
    expect(normalizeSuccess({ success: true, data: { status: 'ok' }, request_id: 'req_1' })).toEqual({ status: 'ok' })
  })

  it.each([[401, 'auth'], [403, 'permission'], [404, 'not-found'], [409, 'conflict'], [422, 'validation'], [500, 'server']] as const)(
    'maps HTTP %s to %s',
    (status, kind) => expect(normalizeError(status, {}).kind).toBe(kind),
  )

  it('preserves backend details and request id', () => {
    const error = normalizeError(409, { error: { code: 'DUPLICATE', message: 'Already exists', details: { field: 'email' } } }, 'req_2')
    expect(error).toMatchObject({ kind: 'conflict', code: 'DUPLICATE', details: { field: 'email' }, requestId: 'req_2', retryable: false })
  })
})
