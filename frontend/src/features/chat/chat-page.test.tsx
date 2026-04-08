import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { HttpResponse, http } from 'msw'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { server } from '../../mocks/server'
import { ChatPage } from './chat-page'

const rawKnowledgeBase = {
  id: 'kb-1',
  name: '人力资源制度库',
  code: 'hr',
  department_id: 'department-1',
  description: 'HR',
  rag_workspace: 'rag/hr',
  default_query_mode: 'hybrid',
  status: 'active',
  permission: 'read',
  document_count: 1,
}

const answer = {
  conversation_id: 'conversation-1',
  message_id: 'message-2',
  query_log_id: 'query-1',
  answer: '差旅申请需要经理审批。',
  citations: [{
    knowledge_base_id: 'kb-1',
    knowledge_base_name: 'HR',
    document_id: 'document-1',
    document_title: '差旅制度',
    chunk_id: 'chunk-1',
    snippet: '申请人应先获得直属经理审批。',
    score: 0.9,
  }],
  confidence_score: 0.9,
  query_mode: 'hybrid',
  router_result: { domain: 'hr', task_type: 'knowledge_qa', risk_level: 'low' },
  suggested_skills: [{ name: 'process_checklist', description: '生成流程清单' }],
  compliance: { passed: true, warnings: [] as string[] },
  draft: null,
}

function conversationResponse(response = answer) {
  return {
    id: response.conversation_id,
    title: '差旅流程',
    status: 'active',
    summary: {},
    messages: [
      {
        id: 'message-1',
        role: 'user',
        content: '差旅申请流程是什么？',
        meta_json: {
          citations: [],
          query_log_id: null,
          confidence_score: null,
          query_mode: null,
          router_result: null,
          suggested_skills: [],
          compliance: null,
        },
        created_at: '2026-07-10T08:00:00Z',
      },
      {
        id: response.message_id,
        role: 'assistant',
        content: response.answer,
        meta_json: {
          citations: response.citations,
          query_log_id: response.query_log_id,
          confidence_score: response.confidence_score,
          query_mode: response.query_mode,
          router_result: response.router_result,
          suggested_skills: response.suggested_skills,
          compliance: response.compliance,
        },
        created_at: '2026-07-10T08:00:01Z',
      },
    ],
  }
}

function renderPage(entry = '/chat') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/chat/:conversationId" element={<ChatPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('ChatPage', () => {
  it('sends a question, renders evidence, and submits feedback', async () => {
    let feedbackBody: unknown
    server.use(
      http.get('*/api/knowledge-bases', () =>
        HttpResponse.json({ items: [rawKnowledgeBase], total: 1 }),
      ),
      http.post('*/api/chat', () => HttpResponse.json(answer)),
      http.get('*/api/conversations/conversation-1', () =>
        HttpResponse.json(conversationResponse()),
      ),
      http.post('*/api/query-logs/query-1/feedback', async ({ request }) => {
        feedbackBody = await request.json()
        return HttpResponse.json({
          id: 'feedback-1',
          query_log_id: 'query-1',
          user_id: 'user-1',
          rating: 'useful',
          comment: '引用准确',
          created_at: '2026-07-10T08:00:00Z',
          updated_at: '2026-07-10T08:00:00Z',
        })
      }),
    )

    const user = userEvent.setup()
    renderPage()
    await screen.findByText('人力资源制度库')
    await user.type(screen.getByLabelText('问题'), '差旅申请流程是什么？')
    await user.click(screen.getByRole('button', { name: '发送问题' }))
    expect(await screen.findByText('差旅申请需要经理审批。')).toBeVisible()
    await user.click(screen.getByText('查看引用（1）'))
    expect(screen.getByText('申请人应先获得直属经理审批。')).toBeVisible()
    expect(screen.getByText('可信度 90%')).toBeVisible()

    const answerCard = screen.getByRole('article', { name: /PolicyFlow 回答/u })
    await user.type(within(answerCard).getByLabelText('反馈备注'), '引用准确')
    await user.click(within(answerCard).getByRole('button', { name: '提交反馈' }))
    expect(await within(answerCard).findByRole('status')).toHaveTextContent('已记录')
    expect(feedbackBody).toEqual({ rating: 'useful', comment: '引用准确' })
  })

  it('renders a distinct no-evidence state without fake citations', async () => {
    const noEvidence = {
      ...answer,
      message_id: 'message-no-evidence',
      query_log_id: 'query-no-evidence',
      answer: '当前知识库未找到可靠依据。',
      citations: [],
      confidence_score: 0,
      compliance: { passed: true, warnings: ['NO_RELIABLE_EVIDENCE'] },
      suggested_skills: [],
    }
    server.use(
      http.get('*/api/knowledge-bases', () =>
        HttpResponse.json({ items: [rawKnowledgeBase], total: 1 }),
      ),
      http.post('*/api/chat', () => HttpResponse.json(noEvidence)),
      http.get('*/api/conversations/conversation-1', () =>
        HttpResponse.json(conversationResponse(noEvidence)),
      ),
    )
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('人力资源制度库')
    await user.type(screen.getByLabelText('问题'), 'unknown policy')
    await user.click(screen.getByRole('button', { name: '发送问题' }))
    expect(await screen.findByText('未找到可靠依据')).toBeVisible()
    expect(screen.queryByText(/查看引用/u)).not.toBeInTheDocument()
  })

  it('restores a conversation with feedback metadata', async () => {
    server.use(
      http.get('*/api/knowledge-bases', () =>
        HttpResponse.json({ items: [rawKnowledgeBase], total: 1 }),
      ),
      http.get('*/api/conversations/conversation-1', () =>
        HttpResponse.json(conversationResponse()),
      ),
    )
    renderPage('/chat/conversation-1')
    expect(await screen.findByText('差旅申请需要经理审批。')).toBeVisible()
    expect(screen.getByLabelText('回答评价')).toBeVisible()
    expect(screen.getByText('process_checklist：生成流程清单')).toBeVisible()
  })

  it('keeps a failed question available for retry', async () => {
    let calls = 0
    server.use(
      http.get('*/api/knowledge-bases', () =>
        HttpResponse.json({ items: [rawKnowledgeBase], total: 1 }),
      ),
      http.post('*/api/chat', () => {
        calls += 1
        return calls === 1
          ? HttpResponse.json(
              { error: { code: 'INTERNAL_ERROR', message: '服务暂不可用', details: null } },
              { status: 500 },
            )
          : HttpResponse.json(answer)
      }),
      http.get('*/api/conversations/conversation-1', () =>
        HttpResponse.json(conversationResponse()),
      ),
    )
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('人力资源制度库')
    await user.type(screen.getByLabelText('问题'), '差旅申请流程是什么？')
    await user.click(screen.getByRole('button', { name: '发送问题' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('服务暂不可用')
    await user.click(screen.getByRole('button', { name: '重试发送' }))
    expect(await screen.findByText('差旅申请需要经理审批。')).toBeVisible()
    expect(calls).toBe(2)
  })
})
