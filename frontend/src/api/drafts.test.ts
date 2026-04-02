import { HttpResponse, http } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../mocks/server'
import {
  confirmDraft,
  createDraft,
  discardDraft,
  exportDraft,
  getDraft,
  listDrafts,
  updateDraft,
} from './drafts'

const rawDraft = {
  id: 'draft-1',
  user_id: 'user-1',
  conversation_id: 'conversation-1',
  draft_type: 'email',
  title: '差旅申请',
  content: '正文',
  source_question: '帮我写申请',
  related_sources: [{ document_id: 'document-1' }],
  status: 'draft',
  created_at: '2026-07-10T08:00:00Z',
  updated_at: '2026-07-10T08:00:00Z',
}

describe('draft API adapters', () => {
  it('maps list/detail and serializes create/update contracts', async () => {
    const bodies: unknown[] = []
    server.use(
      http.get('*/api/drafts', ({ request }) => {
        const search = new URL(request.url).searchParams
        expect(search.get('status')).toBe('draft')
        expect(search.get('draft_type')).toBe('email')
        return HttpResponse.json({
          items: [rawDraft],
          total: 1,
          page: 1,
          page_size: 20,
        })
      }),
      http.get('*/api/drafts/draft-1', () => HttpResponse.json(rawDraft)),
      http.post('*/api/drafts', async ({ request }) => {
        bodies.push(await request.json())
        return HttpResponse.json(rawDraft, { status: 201 })
      }),
      http.put('*/api/drafts/draft-1', async ({ request }) => {
        bodies.push(await request.json())
        return HttpResponse.json({ ...rawDraft, title: '更新标题' })
      }),
    )

    await expect(listDrafts(1, 20, 'draft', 'email')).resolves.toMatchObject({
      items: [{ userId: 'user-1', draftType: 'email', sourceQuestion: '帮我写申请' }],
      pageSize: 20,
    })
    await expect(getDraft('draft-1')).resolves.toMatchObject({ id: 'draft-1' })
    await createDraft({
      conversationId: 'conversation-1',
      draftType: 'email',
      title: '差旅申请',
      content: '正文',
      sourceQuestion: '帮我写申请',
    })
    await updateDraft('draft-1', { title: '更新标题', content: '更新正文' })
    expect(bodies).toEqual([
      {
        conversation_id: 'conversation-1',
        draft_type: 'email',
        title: '差旅申请',
        content: '正文',
        source_question: '帮我写申请',
        related_sources: [],
      },
      { title: '更新标题', content: '更新正文' },
    ])
  })

  it('maps confirm, discard, and export actions', async () => {
    server.use(
      http.post('*/api/drafts/draft-1/confirm', () =>
        HttpResponse.json({ ...rawDraft, status: 'confirmed' }),
      ),
      http.post('*/api/drafts/draft-1/discard', () =>
        HttpResponse.json({ ...rawDraft, status: 'discarded' }),
      ),
      http.post('*/api/drafts/draft-1/export', () =>
        HttpResponse.json({ export_type: 'markdown', content: '# 差旅申请' }),
      ),
    )
    await expect(confirmDraft('draft-1')).resolves.toMatchObject({ status: 'confirmed' })
    await expect(discardDraft('draft-1')).resolves.toMatchObject({ status: 'discarded' })
    await expect(exportDraft('draft-1')).resolves.toEqual({
      exportType: 'markdown',
      content: '# 差旅申请',
    })
  })
})
