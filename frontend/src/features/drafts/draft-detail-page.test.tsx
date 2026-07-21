import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConfigProvider, Modal } from 'antd'
import { HttpResponse, http } from 'msw'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { server } from '../../mocks/server'
import { DraftDetailPage } from './draft-detail-page'

const rawDraft = {
  id: 'draft-1',
  user_id: 'user-1',
  conversation_id: 'conversation-1',
  draft_type: 'email',
  title: '差旅申请',
  content: '初始正文',
  source_question: '帮我写申请',
  related_sources: [{ document_title: '差旅制度' }],
  status: 'draft',
  created_at: '2026-07-10T08:00:00Z',
  updated_at: '2026-07-10T08:00:00Z',
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const router = createMemoryRouter([
    { path: '/drafts', element: <p>草稿列表</p> },
    { path: '/drafts/:draftId', element: <DraftDetailPage /> },
  ], { initialEntries: ['/drafts/draft-1'] })
  return render(
    <ConfigProvider theme={{ token: { motion: false } }} button={{ autoInsertSpace: false }}>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ConfigProvider>,
  )
}

describe('DraftDetailPage', () => {
  beforeEach(() => {
    Modal.destroyAll()
    document.body.innerHTML = ''
  })
  afterEach(() => {
    Modal.destroyAll()
    vi.restoreAllMocks()
  })

  it('saves, confirms, becomes read-only, and exports markdown', async () => {
    let updatedBody: unknown
    const createObjectUrl = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:test')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    server.use(
      http.get('*/api/drafts/draft-1', () => HttpResponse.json(rawDraft)),
      http.put('*/api/drafts/draft-1', async ({ request }) => {
        updatedBody = await request.json()
        return HttpResponse.json({
          ...rawDraft,
          title: '更新后的申请',
          content: '更新后的正文',
          updated_at: '2026-07-10T08:01:00Z',
        })
      }),
      http.post('*/api/drafts/draft-1/confirm', () =>
        HttpResponse.json({
          ...rawDraft,
          title: '更新后的申请',
          content: '更新后的正文',
          status: 'confirmed',
          updated_at: '2026-07-10T08:02:00Z',
        }),
      ),
      http.post('*/api/drafts/draft-1/export', () =>
        HttpResponse.json({
          export_type: 'markdown',
          content: '# 更新后的申请\n\n更新后的正文',
        }),
      ),
    )

    const user = userEvent.setup()
    renderPage()
    const title = await screen.findByLabelText('标题')
    await user.clear(title)
    await user.type(title, '更新后的申请')
    const content = screen.getByLabelText('正文')
    await user.clear(content)
    await user.type(content, '更新后的正文')
    expect(screen.getByRole('button', { name: '确认草稿' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '保存草稿' }))
    await vi.waitFor(() => expect(updatedBody).toEqual({
      title: '更新后的申请',
      content: '更新后的正文',
    }))
    await user.click(screen.getByRole('button', { name: '确认草稿' }))
    expect(await screen.findByText('当前状态为已确认，正文已只读。')).toBeVisible()
    expect(screen.getByText('已确认')).toBeVisible()
    expect(screen.getByLabelText('正文')).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '导出 Markdown' }))
    await vi.waitFor(() => expect(createObjectUrl).toHaveBeenCalledOnce())
  })

  it('requires confirmation before discarding', async () => {
    let discarded = false
    server.use(
      http.get('*/api/drafts/draft-1', () => HttpResponse.json(rawDraft)),
      http.post('*/api/drafts/draft-1/discard', () => {
        discarded = true
        return HttpResponse.json({ ...rawDraft, status: 'discarded' })
      }),
    )
    const user = userEvent.setup()
    renderPage()
    await screen.findByLabelText('正文')
    await user.click(screen.getByRole('button', { name: '丢弃草稿' }))
    const dialog = (await screen.findAllByRole('dialog')).at(-1)!
    expect(dialog).toHaveTextContent('确定丢弃这份草稿吗')
    await user.click(within(dialog).getByRole('button', { name: /丢弃/ }))
    await vi.waitFor(() => expect(discarded).toBe(true))
  })

  it('blocks navigation while edits are unsaved', async () => {
    server.use(
      http.get('*/api/drafts/draft-1', () => HttpResponse.json(rawDraft)),
    )
    const user = userEvent.setup()
    renderPage()
    await screen.findByLabelText('正文')
    await user.type(screen.getByLabelText('正文'), ' 未保存')
    await user.click(screen.getByRole('link', { name: /返回草稿/ }))
    const dialog = (await screen.findAllByRole('dialog')).at(-1)!
    expect(dialog).toHaveTextContent('草稿有未保存修改')
    await user.click(within(dialog).getByRole('button', { name: /继续编辑/ }))
    await vi.waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
    expect(screen.getByLabelText('正文')).toBeVisible()

    await user.click(screen.getByRole('link', { name: /返回草稿/ }))
    const leaveDialog = (await screen.findAllByRole('dialog')).at(-1)!
    await user.click(within(leaveDialog).getByRole('button', { name: /离开/ }))
    expect(await screen.findByText('草稿列表')).toBeVisible()
  })
})
