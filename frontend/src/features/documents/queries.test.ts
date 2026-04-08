import { describe, expect, it } from 'vitest'
import type { DocumentStatus } from '../../api/knowledge-bases'
import { documentStatusPollingInterval } from './queries'

function status(indexStatus: string): DocumentStatus {
  return {
    documentId: 'document-1',
    indexStatus,
    indexError: null,
    latestJob: null,
  }
}

describe('documentStatusPollingInterval', () => {
  it('polls quickly at first and backs off after 30 seconds', () => {
    expect(documentStatusPollingInterval(undefined, 0, 100_000)).toBe(2_000)
    expect(documentStatusPollingInterval(status('indexing'), 80_001, 100_000)).toBe(2_000)
    expect(documentStatusPollingInterval(status('pending'), 70_000, 100_000)).toBe(5_000)
  })

  it.each(['indexed', 'failed'])('stops polling for terminal status %s', (indexStatus) => {
    expect(documentStatusPollingInterval(status(indexStatus), 70_000, 100_000)).toBe(false)
  })
})
