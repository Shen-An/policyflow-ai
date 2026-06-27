import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConfigProvider, Modal } from 'antd'
import { HttpResponse, http } from 'msw'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { server } from '../../mocks/server'
import { UsersPage } from './users-page'

const rawUser = {
  id: 'u1', username: 'zhangsan', email: 'zhangsan@example.com', display_name: '张三',
  department: { id: 'd1', name: '人力资源部' }, roles: ['employee'], status: 'active',
  created_at: '2026-07-10T08:00:00Z', updated_at: '2026-07-10T08:00:00Z',
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <ConfigProvider theme={{ token: { motion: false } }} button={{ autoInsertSpace: false }}>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/admin/users']}>
          <UsersPage />
        </MemoryRouter>
      </QueryClientProvider>
    </ConfigProvider>,
  )
}

function listResponse(items = [rawUser]) {
  return HttpResponse.json({ items, total: items.length, page: 1, page_size: 20 })
}

async function openCreateForm(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: /创建用户/ }))
  await user.type(screen.getByLabelText('用户名'), 'new_user')
  await user.type(screen.getByLabelText('邮箱'), 'new_user@example.com')
  await user.type(screen.getByLabelText('显示名'), '新用户')
  await user.type(screen.getByLabelText('初始密码'), 'new-password')
}

describe('UsersPage', () => {
  beforeEach(() => vi.restoreAllMocks())
  afterEach(() => {
    Modal.destroyAll()
  })

  it('renders loading then the real list fields', async () => {
    server.use(http.get('*/api/users', () => listResponse()))
    renderPage()
    const row = await screen.findByRole('row', { name: /张三/ })
    expect(row).toHaveTextContent('zhangsan@example.com')
    expect(row).toHaveTextContent('人力资源部')
    expect(row).toHaveTextContent('普通员工')
    expect(row).toHaveTextContent('启用')
  })

  it('separates empty and retriable error states', async () => {
    let calls = 0
    server.use(http.get('*/api/users', () => {
      calls += 1
      return calls === 1
        ? HttpResponse.json({ error: { code: 'INTERNAL_ERROR', message: 'Internal server error', details: null } }, { status: 500 })
        : listResponse([])
    }))
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent('用户列表加载失败')
    await user.click(screen.getByRole('button', { name: '重新加载' }))
    expect(await screen.findByText('还没有用户')).toBeVisible()
  })

  it('debounces keyword search into the URL-backed request', async () => {
    const keywords: Array<string | null> = []
    server.use(http.get('*/api/users', ({ request }) => {
      keywords.push(new URL(request.url).searchParams.get('keyword'))
      return listResponse()
    }))
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('row', { name: /张三/ })
    await user.type(screen.getByPlaceholderText('搜索用户名、邮箱或显示名'), 'zhang')
    await vi.waitFor(() => expect(keywords).toContain('zhang'), { timeout: 1500 })
  })

  it('creates a user and invalidates the list', async () => {
    let listCalls = 0
    let createBody: unknown
    server.use(
      http.get('*/api/users', () => { listCalls += 1; return listResponse() }),
      http.post('*/api/users', async ({ request }) => { createBody = await request.json(); return HttpResponse.json({ ...rawUser, id: 'u2', username: 'new_user', email: 'new_user@example.com', display_name: '新用户' }, { status: 201 }) }),
    )
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('row', { name: /张三/ })
    await openCreateForm(user)
    await user.click(screen.getByRole('button', { name: '创建用户' }))
    await vi.waitFor(() => expect(listCalls).toBeGreaterThan(1))
    expect(createBody).toMatchObject({ username: 'new_user', display_name: '新用户', role_codes: ['employee'] })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('maps 409 and 422 errors to form fields', async () => {
    let mode: 'conflict' | 'validation' = 'conflict'
    server.use(
      http.get('*/api/users', () => listResponse()),
      http.post('*/api/users', () => mode === 'conflict'
        ? HttpResponse.json({ error: { code: 'USER_USERNAME_EXISTS', message: 'Username already exists', details: null } }, { status: 409 })
        : HttpResponse.json({ error: { code: 'VALIDATION_ERROR', message: 'Request validation failed', details: [{ loc: ['body', 'email'], msg: 'Invalid email address' }] } }, { status: 422 }),
      ),
    )
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('row', { name: /张三/ })
    await openCreateForm(user)
    await user.click(screen.getByRole('button', { name: '创建用户' }))
    expect(await screen.findByText('该用户名已存在')).toBeVisible()
    mode = 'validation'
    await user.clear(screen.getByLabelText('用户名'))
    await user.type(screen.getByLabelText('用户名'), 'another_user')
    await user.click(screen.getByRole('button', { name: '创建用户' }))
    expect(await screen.findByText('Invalid email address')).toBeVisible()
  })

  it('updates roles with at least one selected role', async () => {
    let updateBody: unknown
    server.use(
      http.get('*/api/users', () => listResponse()),
      http.put('*/api/users/u1/roles', async ({ request }) => { updateBody = await request.json(); return HttpResponse.json({ ...rawUser, roles: ['employee', 'kb_admin'] }) }),
    )
    const user = userEvent.setup()
    renderPage()
    const row = await screen.findByRole('row', { name: /张三/ })
    await user.click(within(row).getByRole('button', { name: '修改角色' }))
    await user.click(screen.getByLabelText('知识库管理员'))
    await user.click(screen.getByRole('button', { name: '保存角色' }))
    await vi.waitFor(() => expect(updateBody).toEqual({ role_codes: ['employee', 'kb_admin'] }))
  })
})
