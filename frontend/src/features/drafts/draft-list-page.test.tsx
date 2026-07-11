import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { HttpResponse, http } from 'msw'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { server } from '../../mocks/server'
import { DraftListPage } from './draft-list-page'

const rawDraft = {
  id: 'draft-1',
  user_id: 'user-1',
  conversation_id: null,
  draft_type: 'email',
  title: '差旅申请',
  content: '申请正文',
  source_question: '帮我写申请',
  related_sources: [],
  status: 'draft',
  created_at: '2026-07-10T08:00:00Z',
  updated_at: '2026-07-10T08:00:00Z',
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/drafts']}>
        <Routes>
          <Route path="/drafts" element={<DraftListPage />} />
          <Route path="/drafts/:draftId" element={<p>草稿详情已打开</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('DraftListPage', () => {
  it('renders loading, filters, and drafts from the server', async () => {
    const requests: string[] = []
    server.use(
      http.get('*/api/drafts', ({ request }) => {
        requests.push(request.url)
        return HttpResponse.json({
          items: [rawDraft],
          total: 1,
          page: 1,
          page_size: 20,
        })
      }),
    )
    const user = userEvent.setup()
    renderPage()
    expect(screen.getByRole('status')).toHaveTextContent('正在加载草稿')
    expect(await screen.findByText('差旅申请')).toBeVisible()
    await user.selectOptions(screen.getByLabelText('状态'), 'draft')
    await user.selectOptions(screen.getByLabelText('类型'), 'email')
    await vi.waitFor(() => {
      const latest = new URL(requests.at(-1) ?? 'http://localhost')
      expect(latest.searchParams.get('status')).toBe('draft')
      expect(latest.searchParams.get('draft_type')).toBe('email')
    })
  })

  it('creates a draft and opens its detail route', async () => {
    let createBody: unknown
    server.use(
      http.get('*/api/drafts', () =>
        HttpResponse.json({ items: [], total: 0, page: 1, page_size: 20 }),
      ),
      http.post('*/api/drafts', async ({ request }) => {
        createBody = await request.json()
        return HttpResponse.json(rawDraft, { status: 201 })
      }),
    )
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('还没有符合条件的草稿')
    await user.click(screen.getByRole('button', { name: '创建草稿' }))
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByLabelText('标题'), '差旅申请')
    await user.type(within(dialog).getByLabelText('正文'), '申请正文')
    await user.type(within(dialog).getByLabelText('来源问题'), '帮我写申请')
    await user.click(within(dialog).getByRole('button', { name: '创建草稿' }))
    expect(await screen.findByText('草稿详情已打开')).toBeVisible()
    expect(createBody).toMatchObject({
      draft_type: 'email',
      title: '差旅申请',
      source_question: '帮我写申请',
    })
  })
})
