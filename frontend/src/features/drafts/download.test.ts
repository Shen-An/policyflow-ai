import { describe, expect, it } from 'vitest'
import { safeDraftFilename } from './download'

describe('safeDraftFilename', () => {
  it('removes path and Windows filename characters', () => {
    expect(safeDraftFilename(' 差旅:/申请*? ')).toBe('差旅--申请--.md')
  })

  it('uses a stable fallback for empty titles', () => {
    expect(safeDraftFilename('...')).toBe('policyflow-draft.md')
  })
})
