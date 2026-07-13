import { HttpResponse, http } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../mocks/server'
import {
  deleteConversation,
  getConversation,
  listConversations,
  renameConversation,
  sendChat,
  submitFeedback,
} from './chat'

const citation = {
  knowledge_base_id: 'kb-1',
  knowledge_base_name: 'HR',
  document_id: 'document-1',
  document_title: 'Travel Policy',
  chunk_id: 'chunk-1',
  snippet: 'Travel requires manager approval.',
  score: 0.91,
}

const metadata = {
  citations: [citation],
  query_log_id: 'query-1',
  confidence_score: 0.91,
  query_mode: 'hybrid',
  router_result: {
    domain: 'hr',
    task_type: 'knowledge_qa',
    risk_level: 'low',
  },
  suggested_skills: [{ name: 'process_checklist', description: '生成流程清单' }],
  compliance: { passed: true, warnings: [] },
}

describe('chat API adapters', () => {
  it('serializes chat input and maps the complete answer contract', async () => {
    let requestBody: unknown
    server.use(
      http.post('*/api/chat', async ({ request }) => {
        requestBody = await request.json()
        return HttpResponse.json({
          conversation_id: 'conversation-1',
          message_id: 'message-1',
          answer: '需要经理审批。',
          ...metadata,
          draft: null,
        })
      }),
    )

    await expect(sendChat({
      question: '差旅流程？',
      knowledgeBaseIds: ['kb-1'],
      queryMode: 'hybrid',
    })).resolves.toMatchObject({
      conversationId: 'conversation-1',
      queryLogId: 'query-1',
      citations: [{
        knowledgeBaseId: 'kb-1',
        documentTitle: 'Travel Policy',
      }],
      routerResult: { taskType: 'knowledge_qa' },
    })
    expect(requestBody).toEqual({
      conversation_id: null,
      question: '差旅流程？',
      knowledge_base_ids: ['kb-1'],
      enable_skill: true,
      retrieval_strategy: 'lightrag_only',
      query_mode: 'hybrid',
      top_k: 5,
    })
  })

  it('maps conversation list with owner-scoped history fields', async () => {
    let listUrl = ''
    let renameBody: unknown
    let deletedId: string | null = null
    server.use(
      http.get('*/api/conversations', ({ request }) => {
        listUrl = request.url
        return HttpResponse.json({
          items: [
            {
              id: 'conversation-1',
              title: '差旅流程',
              status: 'active',
              message_count: 2,
              last_message_preview: '需要经理审批。',
              last_message_role: 'assistant',
              created_at: '2026-07-10T08:00:00Z',
              updated_at: '2026-07-10T08:01:00Z',
            },
          ],
          total: 1,
          page: 1,
          page_size: 20,
        })
      }),
      http.patch('*/api/conversations/:conversationId', async ({ request, params }) => {
        renameBody = await request.json()
        return HttpResponse.json({
          id: params.conversationId,
          title: '我的差旅咨询',
          status: 'active',
          message_count: 2,
          last_message_preview: '需要经理审批。',
          last_message_role: 'assistant',
          created_at: '2026-07-10T08:00:00Z',
          updated_at: '2026-07-10T08:02:00Z',
        })
      }),
      http.delete('*/api/conversations/:conversationId', ({ params }) => {
        deletedId = String(params.conversationId)
        return new HttpResponse(null, { status: 204 })
      }),
    )

    await expect(listConversations(1, 20, '差旅')).resolves.toEqual({
      items: [
        {
          id: 'conversation-1',
          title: '差旅流程',
          status: 'active',
          messageCount: 2,
          lastMessagePreview: '需要经理审批。',
          lastMessageRole: 'assistant',
          createdAt: '2026-07-10T08:00:00Z',
          updatedAt: '2026-07-10T08:01:00Z',
        },
      ],
      total: 1,
      page: 1,
      pageSize: 20,
    })
    const parsedListUrl = new URL(listUrl)
    expect(parsedListUrl.searchParams.get('page')).toBe('1')
    expect(parsedListUrl.searchParams.get('page_size')).toBe('20')
    expect(parsedListUrl.searchParams.get('keyword')).toBe('差旅')
    await expect(renameConversation('conversation-1', ' 我的差旅咨询 ')).resolves.toMatchObject({
      id: 'conversation-1',
      title: '我的差旅咨询',
    })
    expect(renameBody).toEqual({ title: '我的差旅咨询' })
    await expect(deleteConversation('conversation-1')).resolves.toBeUndefined()
    expect(deletedId).toBe('conversation-1')
  })

  it('maps historical assistant metadata and feedback upserts', async () => {
    server.use(
      http.get('*/api/conversations/conversation-1', () =>
        HttpResponse.json({
          id: 'conversation-1',
          title: '差旅流程',
          status: 'active',
          summary: {},
          messages: [{
            id: 'message-1',
            role: 'assistant',
            content: '需要经理审批。',
            meta_json: metadata,
            created_at: '2026-07-10T08:00:00Z',
          }],
        }),
      ),
      http.post('*/api/query-logs/query-1/feedback', async ({ request }) => {
        expect(await request.json()).toEqual({
          rating: 'incomplete',
          comment: '需要更多步骤',
        })
        return HttpResponse.json({
          id: 'feedback-1',
          query_log_id: 'query-1',
          user_id: 'user-1',
          rating: 'incomplete',
          comment: '需要更多步骤',
          created_at: '2026-07-10T08:00:00Z',
          updated_at: '2026-07-10T08:01:00Z',
        })
      }),
    )

    await expect(getConversation('conversation-1')).resolves.toMatchObject({
      messages: [{
        metadata: {
          queryLogId: 'query-1',
          confidenceScore: 0.91,
          suggestedSkills: [{ name: 'process_checklist' }],
        },
      }],
    })
    await expect(
      submitFeedback('query-1', 'incomplete', ' 需要更多步骤 '),
    ).resolves.toMatchObject({
      queryLogId: 'query-1',
      rating: 'incomplete',
    })
  })
})
