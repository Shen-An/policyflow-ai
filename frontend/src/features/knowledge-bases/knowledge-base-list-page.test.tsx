import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { HttpResponse, http } from 'msw'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import type { RoleCode } from '../../api/auth'
import { authStore } from '../../auth/auth-store'
import { server } from '../../mocks/server'
import { KnowledgeBaseListPage } from './knowledge-base-list-page'

const rawKnowledgeBase = {
  id: 'kb-1',
  name: '人力资源制度库',
  code: 'hr-policy',
  department_id: 'department-1',
  description: '员工手册和人事制度',
  rag_workspace: 'rag/hr-policy',
  default_query_mode: 'mix',
  status: 'active',
  permission: 'admin',
  document_count: 2,
}

function renderPage(initialEntry = '/knowledge-bases') {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <KnowledgeBaseListPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function authenticate(roles: RoleCode[]) {
  authStore.authenticateForDuration('token', 1800, {
    id: 'user-1',
    username: 'tester',
    displayName: '测试用户',
    roles,
  })
}

describe('KnowledgeBaseListPage', () => {
  beforeEach(() => {
    authenticate(['sys_admin'])
  })

  afterEach(() => {
    authStore.clearSession()
    vi.restoreAllMocks()
  })

  it('renders loading, ACL fields, URL-backed filtering, and pagination', async () => {
    const items = Array.from({ length: 13 }, (_, index) => ({
      ...rawKnowledgeBase,
      id: `kb-${index + 1}`,
      name: index === 12 ? '财务制度库' : `人力资源制度库 ${index + 1}`,
      code: index === 12 ? 'finance-policy' : `hr-policy-${index + 1}`,
      permission: index === 12 ? 'read' : 'admin',
    }))
    server.use(
      http.get('*/api/knowledge-bases', () =>
        HttpResponse.json({ items, total: items.length }),
      ),
    )

    const user = userEvent.setup()
    renderPage()
    expect(screen.getByRole('status')).toHaveTextContent('正在加载知识库')
    expect(await screen.findByText('人力资源制度库 1')).toBeVisible()
    expect(screen.queryByText('财务制度库')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '下一页' }))
    expect(await screen.findByText('财务制度库')).toBeVisible()
    expect(screen.getByText('共 13 个，第 2 / 2 页')).toBeVisible()

    await user.type(screen.getByPlaceholderText('搜索名称、编码或描述'), 'finance')
    expect(await screen.findByText('财务制度库')).toBeVisible()
    expect(screen.getByText('共 1 个，第 1 / 1 页')).toBeVisible()
  })

  it('separates retriable error, empty, and no-match states', async () => {
    let calls = 0
    server.use(
      http.get('*/api/knowledge-bases', () => {
        calls += 1
        return calls === 1
          ? HttpResponse.json(
              { error: { code: 'INTERNAL_ERROR', message: '服务暂不可用', details: null } },
              { status: 500 },
            )
          : HttpResponse.json({ items: [], total: 0 })
      }),
    )

    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent('知识库加载失败')
    await user.click(screen.getByRole('button', { name: '重新加载' }))
    expect(await screen.findByText('没有可访问的知识库')).toBeVisible()
  })

  it('creates a knowledge base with backend options and refreshes the list', async () => {
    let listCalls = 0
    let createBody: unknown
    server.use(
      http.get('*/api/knowledge-bases', () => {
        listCalls += 1
        return HttpResponse.json({ items: [rawKnowledgeBase], total: 1 })
      }),
      http.get('*/api/knowledge-bases/create-options', () =>
        HttpResponse.json({
          departments: [{ id: 'department-1', code: 'hr', name: '人力资源部' }],
        }),
      ),
      http.post('*/api/knowledge-bases', async ({ request }) => {
        createBody = await request.json()
        return HttpResponse.json({
          ...rawKnowledgeBase,
          id: 'kb-created',
          name: '新制度库',
          code: 'new-policy',
        }, { status: 201 })
      }),
    )

    const user = userEvent.setup()
    renderPage()
    await screen.findByText('人力资源制度库')
    await user.click(screen.getByRole('button', { name: '创建知识库' }))
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByLabelText('名称'), '新制度库')
    await user.type(within(dialog).getByLabelText('编码'), 'new-policy')
    await user.selectOptions(
      within(dialog).getByLabelText('部门'),
      'department-1',
    )
    await user.selectOptions(within(dialog).getByLabelText('默认检索模式'), 'hybrid')
    await user.type(within(dialog).getByLabelText('描述'), '新制度')
    await user.click(within(dialog).getByRole('button', { name: '创建知识库' }))

    await vi.waitFor(() => expect(listCalls).toBeGreaterThan(1))
    expect(createBody).toMatchObject({
      code: 'new-policy',
      department_id: 'department-1',
      default_query_mode: 'hybrid',
    })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows a code conflict and hides creation from ordinary employees', async () => {
    server.use(
      http.get('*/api/knowledge-bases', () =>
        HttpResponse.json({ items: [rawKnowledgeBase], total: 1 }),
      ),
      http.get('*/api/knowledge-bases/create-options', () =>
        HttpResponse.json({
          departments: [{ id: 'department-1', code: 'hr', name: '人力资源部' }],
        }),
      ),
      http.post('*/api/knowledge-bases', () =>
        HttpResponse.json(
          { error: { code: 'KB_CODE_EXISTS', message: 'Code exists', details: null } },
          { status: 409 },
        ),
      ),
    )

    const user = userEvent.setup()
    const first = renderPage()
    await screen.findByText('人力资源制度库')
    await user.click(screen.getByRole('button', { name: '创建知识库' }))
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByLabelText('名称'), '重复制度库')
    await user.type(within(dialog).getByLabelText('编码'), 'hr-policy')
    await user.selectOptions(within(dialog).getByLabelText('部门'), 'department-1')
    await user.click(within(dialog).getByRole('button', { name: '创建知识库' }))
    expect(await within(dialog).findByText('该知识库编码已存在')).toBeVisible()

    first.unmount()
    authStore.clearSession()
    authenticate(['employee'])
    renderPage()
    await screen.findByText('人力资源制度库')
    expect(screen.queryByRole('button', { name: '创建知识库' })).not.toBeInTheDocument()
  })
})
