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
  diagnostics: {
    memories: [
      {
        id: 'mem-1',
        memory_type: 'user_preference',
        content: '偏好分点回答',
        source_slot: 'fixed',
        confidence: 0.8,
      },
    ],
    tools: [
      {
        tool_name: 'skill.suggest:process_checklist',
        status: 'suggested',
        agent_name: 'SkillAgent',
        input_summary: {},
        output_summary: { name: 'process_checklist', description: '生成流程清单' },
        error_message: null,
        latency_ms: 0,
      },
    ],
    commands: [
      {
        name: 'RetrievalAgent',
        status: 'success',
        summary: '检索 1 个知识库，命中 1 条证据',
        output: { evidence_count: 1 },
      },
      {
        name: 'AnswerAgent',
        status: 'success',
        summary: '差旅申请需要经理审批。',
        output: { confidence_score: 0.9 },
      },
    ],
  },
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

function toSse(events: Array<{ event: string; data: unknown }>): string {
  return events
    .map((item) => `event: ${item.event}\ndata: ${JSON.stringify(item.data)}\n\n`)
    .join('')
}

function streamAnswer(response = answer) {
  return new HttpResponse(
    toSse([
      {
        event: 'stage',
        data: { stage: 'MemoryLoad', status: 'running', message: '正在加载记忆…' },
      },
      {
        event: 'diagnostics_partial',
        data: {
          memories: response.diagnostics.memories,
          tools: [],
          commands: [
            {
              name: 'MemoryLoad',
              status: 'success',
              summary: '已加载记忆',
              output: {},
            },
          ],
        },
      },
      {
        event: 'stage',
        data: {
          stage: 'RetrievalAgent',
          status: 'success',
          message: '检索 1 个知识库，命中 1 条证据',
        },
      },
      {
        event: 'diagnostics',
        data: response.diagnostics,
      },
      {
        event: 'final',
        data: response,
      },
    ]),
    {
      status: 200,
      headers: {
        'Content-Type': 'text/event-stream',
      },
    },
  )
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
          diagnostics: { memories: [], tools: [], commands: [] },
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
          diagnostics: response.diagnostics,
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
    <ConfigProvider theme={{ token: { motion: false } }} button={{ autoInsertSpace: false }}>
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
      http.post('*/api/chat/stream', () => streamAnswer()),
      http.get('*/api/conversations/conversation-1', () =>
        HttpResponse.json(conversationResponse()),
      ),
    )

    const user = userEvent.setup()
    renderPage()
    await screen.findByText('人力资源制度库')
    await user.type(screen.getByLabelText('输入问题'), '差旅申请流程是什么？')
    await user.click(screen.getByRole('button', { name: '发送' }))
    expect(await screen.findByRole('article', { name: /PolicyFlow 回答/u })).toHaveTextContent(
      '差旅申请需要经理审批。',
    )
    expect(screen.getByText('查看引用（1）')).toBeVisible()
    expect(screen.getByText('申请人应先获得直属经理审批。')).toBeVisible()
    expect(screen.getByText('本轮使用')).toBeVisible()
    expect(screen.getByText('记忆 1')).toBeVisible()
    expect(screen.getByText('工具 1')).toBeVisible()
    expect(screen.getByText('命令 2')).toBeVisible()
    const memorySection = screen.getByText((_, element) =>
      element?.tagName.toLowerCase() === 'summary' &&
      (element.textContent ?? '').includes('记忆 · 1'),
    ).closest('details')
    expect(memorySection).not.toBeNull()
    memorySection?.setAttribute('open', '')
    expect(within(memorySection as HTMLElement).getByText('偏好分点回答')).toBeVisible()
    const commandSection = screen.getByText((_, element) =>
      element?.tagName.toLowerCase() === 'summary' &&
      (element.textContent ?? '').includes('命令 · 2'),
    ).closest('details')
    commandSection?.setAttribute('open', '')
    expect(
      within(commandSection as HTMLElement).getByText('检索 1 个知识库，命中 1 条证据'),
    ).toBeVisible()
    expect(screen.getByText('可信度 90%')).toBeVisible()
    expect(screen.getByLabelText('回答评价')).toBeVisible()
    expect(screen.getByLabelText('反馈备注')).toBeVisible()
    expect(screen.getByRole('button', { name: '提交反馈' })).toBeVisible()
  })

  it('supports copying answers and editing previous questions', async () => {
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
    const user = userEvent.setup()
    let copied = ''
    const writeText = vi.fn(async (value: string) => {
      copied = value
    })
    // userEvent.setup() may install its own clipboard; override after setup.
    Object.defineProperty(globalThis.navigator, 'clipboard', {
      configurable: true,
      writable: true,
      value: { writeText },
    })
    renderPage('/chat/conversation-1')
    expect(await screen.findByText('差旅申请流程是什么？')).toBeVisible()
    const answer = await screen.findByRole('article', { name: /PolicyFlow 回答/u })
    expect(answer).toBeVisible()

    await user.click(within(answer).getByRole('button', { name: '复制回答' }))
    await vi.waitFor(() => {
      expect(copied).toContain('差旅申请需要经理审批。')
    })

    await user.click(screen.getByRole('button', { name: '编辑问题' }))
    await vi.waitFor(() => {
      expect(screen.getByLabelText('输入问题')).toHaveValue('差旅申请流程是什么？')
    })
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
      http.post('*/api/chat/stream', () => streamAnswer(noEvidence)),
      http.get('*/api/conversations/conversation-1', () =>
        HttpResponse.json(conversationResponse(noEvidence)),
      ),
    )
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('人力资源制度库')
    await user.type(screen.getByLabelText('输入问题'), 'unknown policy')
    await user.click(screen.getByRole('button', { name: '发送' }))
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

    const deleteButton = await screen.findByRole('button', {
      name: '删除会话：我的差旅咨询',
    })
    // Ensure hover/focus actions are interactable in jsdom.
    deleteButton.style.opacity = '1'
    deleteButton.style.pointerEvents = 'auto'
    await user.click(deleteButton)
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
      http.post('*/api/chat/stream', () => {
        calls += 1
        if (calls === 1) {
          return HttpResponse.json(
            { error: { code: 'INTERNAL_ERROR', message: '服务暂不可用', details: null } },
            { status: 500 },
          )
        }
        return streamAnswer(answer)
      }),
      http.get('*/api/conversations/conversation-1', () =>
        HttpResponse.json(conversationResponse()),
      ),
    )
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('人力资源制度库')
    await user.type(screen.getByLabelText('输入问题'), '差旅申请流程是什么？')
    await user.click(screen.getByRole('button', { name: '发送' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('服务暂不可用')
    await user.click(screen.getByRole('button', { name: '重试发送' }))
    expect(await screen.findByRole('article', { name: /PolicyFlow 回答/u })).toHaveTextContent(
      '差旅申请需要经理审批。',
    )
    expect(calls).toBe(2)
  })
})
