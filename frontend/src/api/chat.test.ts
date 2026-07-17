import { HttpResponse, http } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../mocks/server'
import {
  deleteConversation,
  getConversation,
  listConversations,
  renameConversation,
  sendChat,
  sendChatStream,
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
  diagnostics: {
    memories: [
      {
        id: 'mem-1',
        memory_type: 'user_preference',
        content: 'Prefer tables',
        source_slot: 'fixed',
        confidence: 0.8,
      },
    ],
    tools: [
      {
        tool_name: 'memory.read',
        status: 'success',
        agent_name: 'ToolRegistry',
        input_summary: { owner_type: 'user' },
        output_summary: { items: 1 },
        error_message: null,
        latency_ms: 12,
      },
    ],
    commands: [
      {
        name: 'AnswerAgent',
        status: 'success',
        summary: '需要经理审批。',
        output: { confidence_score: 0.91 },
      },
    ],
  },
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
      diagnostics: {
        memories: [{ memoryType: 'user_preference', sourceSlot: 'fixed' }],
        tools: [{ toolName: 'memory.read', status: 'success' }],
        commands: [{ name: 'AnswerAgent', status: 'success' }],
      },
    })
    expect(requestBody).toEqual({
      conversation_id: null,
      question: '差旅流程？',
      knowledge_base_ids: ['kb-1'],
      enable_skill: true,
      retrieval_strategy: 'hybrid_lightrag_bm25',
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

  it('parses plan and plan_step SSE events during chat stream', async () => {
    const plans: Array<{ complexity: string; steps: Array<{ id: string }> }> = []
    const stepUpdates: Array<{ id: string; status: string }> = []

    server.use(
      http.post('*/api/chat/stream', () => {
        const body = [
          'event: plan',
          'data: {"complexity":"multi_step","plan_source":"user","steps":[{"id":"s1","title":"检索","kind":"retrieve","status":"pending"},{"id":"s2","title":"回答","kind":"answer","status":"pending"}]}',
          '',
          'event: plan_step',
          'data: {"id":"s1","status":"running","message":"检索中"}',
          '',
          'event: plan_step',
          'data: {"id":"s1","status":"success","message":"命中 2 条"}',
          '',
          'event: final',
          `data: ${JSON.stringify({
            conversation_id: 'conversation-1',
            message_id: 'message-1',
            query_log_id: 'query-1',
            answer: 'done',
            citations: [citation],
            confidence_score: 0.9,
            query_mode: 'hybrid',
            router_result: {
              domain: 'hr',
              task_type: 'knowledge_qa',
              risk_level: 'low',
              complexity: 'multi_step',
              plan_source: 'user',
              plan_steps: [
                { id: 's1', title: '检索', kind: 'retrieve', status: 'success' },
                { id: 's2', title: '回答', kind: 'answer', status: 'success' },
              ],
            },
            suggested_skills: [],
            compliance: { passed: true, warnings: [] },
            diagnostics: { memories: [], tools: [], commands: [] },
          })}`,
          '',
        ].join('\n')
        return new HttpResponse(body, {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        })
      }),
    )

    const result = await sendChatStream(
      {
        question: '1. 查制度\n2. 回答',
        knowledgeBaseIds: ['kb-1'],
        queryMode: 'hybrid',
      },
      {
        onPlan: (plan) => {
          plans.push({ complexity: plan.complexity, steps: plan.steps })
        },
        onPlanStep: (step) => {
          stepUpdates.push({ id: step.id, status: step.status })
        },
      },
    )

    expect(plans).toHaveLength(1)
    expect(plans[0]?.complexity).toBe('multi_step')
    expect(plans[0]?.steps).toHaveLength(2)
    expect(stepUpdates).toEqual([
      { id: 's1', status: 'running' },
      { id: 's1', status: 'success' },
    ])
    expect(result.routerResult.complexity).toBe('multi_step')
    expect(result.routerResult.planSteps?.[0]?.status).toBe('success')
  })
})
