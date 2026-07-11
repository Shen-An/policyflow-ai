import { describe, expect, it } from 'vitest'
import { hasAnyRole } from './permissions'

describe('role permissions', () => {
  it('allows any matching role', () => {
    expect(hasAnyRole(['employee', 'kb_admin'], ['sys_admin', 'kb_admin'])).toBe(true)
  })
  it('denies missing roles and accepts an empty requirement', () => {
    expect(hasAnyRole(['employee'], ['sys_admin'])).toBe(false)
    expect(hasAnyRole(['employee'], [])).toBe(true)
  })
})
