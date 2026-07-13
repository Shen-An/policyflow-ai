import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App, ConfigProvider } from 'antd'
import { HttpResponse, http, passthrough } from 'msw'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
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

const historyItem = {
  id: 'conversation-1',
  title: '差旅流程',
  status: 'active',
  message_count: 2,
  last_message_preview: '差旅申请需要经理审批。',
  last_message_role: 'assistant',
  created_at: '2026-07-10T08:00:00Z',
  updated_at: '2026-07-10T08:00:01Z',
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

function emptyHistory() {
  return HttpResponse.json({
    items: [],
    total: 0,
    page: 1,
    page_size: 50,
  })
}

function historyList(items = [historyItem]) {
  return HttpResponse.json({
    items,
    total: items.length,
    page: 1,
    page_size: 50,
  })
}

function isConversationList(request: Request): boolean {
  const url = new URL(request.url)
  return (
    (url.pathname === '/api/conversations' || url.pathname.endsWith('/api/conversations')) &&
    !url.pathname.includes('/api/conversations/')
  )
}

function renderPage(entry = '/chat') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <ConfigProvider theme={{ token: { motion: false } }} autoInsertSpaceInButton={false}>
      <App>
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[entry]}>
            <Routes>
              <Route path="/chat" element={<ChatPage />} />
              <Route path="/chat/:conversationId" element={<ChatPage />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      </App>
    </ConfigProvider>,
  )
}

describe('ChatPage', () => {
  it('sends a question, renders evidence, and submits feedback', async () => {
    server.use(
      http.get('*/api/knowledge-bases', () =>
        HttpResponse.json({ items: [rawKnowledgeBase], total: 1 }),
      ),
      http.get('*/api/conversations', ({ request }) => {
        if (!isConversationList(request)) return passthrough()
        return emptyHistory()
      }),
      http.post('*/api/chat', () => HttpResponse.json(answer)),
      http.get('*/api/conversations/conversation-1', () =>
        HttpResponse.json(conversationResponse()),
      ),
    )

    const user = userEvent.setup()
    renderPage()
    await screen.findByText('人力资源制度库')
    await user.type(screen.getByLabelText('问题'), '差旅申请流程是什么？')
    await user.click(screen.getByRole('button', { name: '发送问题' }))
    expect(await screen.findByRole('article', { name: /PolicyFlow 回答/u })).toHaveTextContent(
      '差旅申请需要经理审批。',
    )
    expect(screen.getByText('查看引用（1）')).toBeVisible()
    expect(screen.getByText('申请人应先获得直属经理审批。')).toBeVisible()
    expect(screen.getByText('可信度 90%')).toBeVisible()
    expect(screen.getByLabelText('回答评价')).toBeVisible()
    expect(screen.getByLabelText('反馈备注')).toBeVisible()
    expect(screen.getByRole('button', { name: '提交反馈' })).toBeVisible()
  })

  it('renders a distinct no-evidence state without fake citations', async () => {
    const noEvidence = {
      ...answer,
      message_id: 'message-no-evidence',
      query_log_id: 'query-no-evidence',
      answer:
        '【未检索到知识库依据 · 模型参考回答 · 不可信，需你自行判断】\n当前知识库未检索到依据。一般企业差旅需先申请审批，请与行政部门确认。',
      citations: [],
      confidence_score: 0,
      compliance: { passed: true, warnings: ['NO_RELIABLE_EVIDENCE'] },
      suggested_skills: [],
    }
    server.use(
      http.get('*/api/knowledge-bases', () =>
        HttpResponse.json({ items: [rawKnowledgeBase], total: 1 }),
      ),
      http.get('*/api/conversations', ({ request }) => {
        if (!isConversationList(request)) return passthrough()
        return emptyHistory()
      }),
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
    expect(screen.getByText(/模型参考回答/u)).toBeVisible()
    expect(screen.getByText(/可信度 0%/u)).toBeVisible()
    expect(screen.queryByText(/查看引用/u)).not.toBeInTheDocument()
  })

  it('restores a conversation with feedback metadata', async () => {
    server.use(
      http.get('*/api/knowledge-bases', () =>
        HttpResponse.json({ items: [rawKnowledgeBase], total: 1 }),
      ),
      http.get('*/api/conversations', ({ request }) => {
        if (!isConversationList(request)) return passthrough()
        return historyList()
      }),
      http.get('*/api/conversations/conversation-1', () =>
        HttpResponse.json(conversationResponse()),
      ),
    )
    renderPage('/chat/conversation-1')
    const answerCard = await screen.findByRole('article', { name: /PolicyFlow 回答/u })
    expect(answerCard).toHaveTextContent('差旅申请需要经理审批。')
    expect(screen.getByLabelText('回答评价')).toBeVisible()
    expect(screen.getByText('process_checklist：生成流程清单')).toBeVisible()
    expect(screen.getByRole('button', { name: '打开会话：差旅流程' })).toHaveAttribute(
      'aria-current',
      'page',
    )
  })

  it('opens, renames, searches, and deletes historical conversations', async () => {
    let listKeyword = ''
    let renamedTitle: string | null = null
    let deletedId: string | null = null
    server.use(
      http.get('*/api/knowledge-bases', () =>
        HttpResponse.json({ items: [rawKnowledgeBase], total: 1 }),
      ),
      http.get('*/api/conversations', ({ request }) => {
        if (!isConversationList(request)) return passthrough()
        const url = new URL(request.url)
        listKeyword = url.searchParams.get('keyword') ?? ''
        if (
          listKeyword &&
          !historyItem.title.includes(listKeyword) &&
          !historyItem.last_message_preview.includes(listKeyword)
        ) {
          return emptyHistory()
        }
        if (deletedId === historyItem.id) return emptyHistory()
        return historyList([
          {
            ...historyItem,
            title: renamedTitle ?? historyItem.title,
          },
        ])
      }),
      http.get('*/api/conversations/conversation-1', () =>
        HttpResponse.json({
          ...conversationResponse(),
          title: renamedTitle ?? '差旅流程',
        }),
      ),
      http.patch('*/api/conversations/conversation-1', async ({ request }) => {
        const body = await request.json() as { title: string }
        renamedTitle = body.title
        return HttpResponse.json({
          ...historyItem,
          title: renamedTitle,
          updated_at: '2026-07-10T08:05:00Z',
        })
      }),
      http.delete('*/api/conversations/conversation-1', () => {
        deletedId = 'conversation-1'
        return new HttpResponse(null, { status: 204 })
      }),
    )

    const user = userEvent.setup()
    renderPage('/chat')
    expect(await screen.findByLabelText('历史会话')).toBeVisible()
    expect(screen.getByText('仅本人可见')).toBeVisible()
    expect(await screen.findByRole('button', { name: '打开会话：差旅流程' })).toBeVisible()

    await user.click(screen.getByRole('button', { name: '打开会话：差旅流程' }))
    expect(await screen.findByRole('article', { name: /PolicyFlow 回答/u })).toHaveTextContent(
      '差旅申请需要经理审批。',
    )

    await user.click(screen.getByRole('button', { name: '重命名会话：差旅流程' }))
    const dialog = await screen.findByRole('dialog')
    const titleInput = within(dialog).getByLabelText('会话标题')
    await user.clear(titleInput)
    await user.type(titleInput, '我的差旅咨询')
    await user.click(within(dialog).getByRole('button', { name: '保存' }))
    expect(await screen.findByRole('button', { name: '打开会话：我的差旅咨询' })).toBeVisible()

    await user.type(screen.getByLabelText('搜索历史会话'), '差旅')
    await vi.waitFor(() => {
      expect(listKeyword).toBe('差旅')
    })

    await user.click(screen.getByRole('button', { name: '删除会话：我的差旅咨询' }))
    const confirm = await screen.findByRole('dialog')
    await user.click(within(confirm).getByRole('button', { name: '删除' }))
    await vi.waitFor(() => {
      expect(deletedId).toBe('conversation-1')
    })
    expect(
      await screen.findByText((content) => content.includes('没有历史会话') || content.includes('没有匹配')),
    ).toBeVisible()
  })

  it('keeps a failed question available for retry', async () => {
    let calls = 0
    server.use(
      http.get('*/api/knowledge-bases', () =>
        HttpResponse.json({ items: [rawKnowledgeBase], total: 1 }),
      ),
      http.get('*/api/conversations', ({ request }) => {
        if (!isConversationList(request)) return passthrough()
        return emptyHistory()
      }),
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
    expect(await screen.findByRole('article', { name: /PolicyFlow 回答/u })).toHaveTextContent(
      '差旅申请需要经理审批。',
    )
    expect(calls).toBe(2)
  })
})
