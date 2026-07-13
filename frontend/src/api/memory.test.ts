import { describe, expect, it, vi } from 'vitest'
import { apiClient } from './client'
import { deleteMemory, listMemories } from './memory'

describe('memory api client', () => {
  it('maps list payload and delete request', async () => {
    const request = vi.spyOn(apiClient, 'request')
    request.mockResolvedValueOnce({
      items: [
        {
          id: 'm1',
          owner_type: 'user',
          owner_id: 'u1',
          memory_type: 'user_preference',
          content: 'Prefer tables',
          source: 'summary',
          confidence: 0.7,
          meta_json: { event_type: 'preference' },
          has_embedding: false,
          expires_at: null,
          created_at: '2026-07-13T00:00:00Z',
          updated_at: '2026-07-13T00:00:00Z',
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    })
    const result = await listMemories(1, 20, 'user_preference', 'table')
    expect(result.items[0]?.content).toBe('Prefer tables')
    expect(result.items[0]?.hasEmbedding).toBe(false)
    expect(request).toHaveBeenCalledWith(
      expect.stringContaining('/api/memory?'),
      expect.objectContaining({ method: 'GET' }),
    )

    request.mockResolvedValueOnce(undefined)
    await deleteMemory('m1')
    expect(request).toHaveBeenCalledWith(
      '/api/memory/m1',
      expect.objectContaining({ method: 'DELETE' }),
    )
  })
})
