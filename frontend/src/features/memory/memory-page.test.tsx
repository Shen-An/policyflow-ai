import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App as AntdApp } from 'antd'
import { HttpResponse, http } from 'msw'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { server } from '../../mocks/server'
import { MemoryPage } from './memory-page'

const rawMemory = {
  id: 'mem-1',
  owner_type: 'user',
  owner_id: 'user-1',
  memory_type: 'user_preference',
  content: 'Prefers bullet points',
  source: 'summary',
  confidence: 0.8,
  meta_json: {},
  has_embedding: true,
  expires_at: null,
  created_at: '2026-07-13T08:00:00Z',
  updated_at: '2026-07-13T08:00:00Z',
}

function renderPage(initial = '/memory') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <AntdApp>
        <MemoryRouter initialEntries={[initial]}>
          <Routes>
            <Route path="/memory" element={<MemoryPage />} />
          </Routes>
        </MemoryRouter>
      </AntdApp>
    </QueryClientProvider>,
  )
}

describe('MemoryPage', () => {
  it('renders memories and supports type filter', async () => {
    const requests: string[] = []
    server.use(
      http.get('*/api/memory', ({ request }) => {
        requests.push(request.url)
        const url = new URL(request.url)
        const memoryType = url.searchParams.get('memory_type')
        const items = !memoryType || memoryType === 'user_preference' ? [rawMemory] : []
        return HttpResponse.json({
          items,
          total: items.length,
          page: 1,
          page_size: 20,
        })
      }),
    )
    const user = userEvent.setup()
    renderPage()
    expect(screen.getByRole('status')).toHaveTextContent('正在加载记忆')
    expect(await screen.findByText('Prefers bullet points')).toBeVisible()
    expect(screen.getByText('用户偏好')).toBeVisible()

    await user.click(screen.getByLabelText('记忆类型'))
    await user.click(await screen.findByText('长期事件'))
    await vi.waitFor(() => {
      const latest = new URL(requests.at(-1) ?? 'http://localhost')
      expect(latest.searchParams.get('memory_type')).toBe('long_term_event')
    })
  })

  it('deletes a memory after confirmation', async () => {
    let deletedId: string | null = null
    server.use(
      http.get('*/api/memory', () =>
        HttpResponse.json({
          items: deletedId ? [] : [rawMemory],
          total: deletedId ? 0 : 1,
          page: 1,
          page_size: 20,
        }),
      ),
      http.delete('*/api/memory/:id', ({ params }) => {
        deletedId = String(params.id)
        return new HttpResponse(null, { status: 204 })
      }),
    )
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByText('Prefers bullet points')).toBeVisible()
    await user.click(screen.getByRole('button', { name: /删\s*除/ }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: /删\s*除/ }))
    await vi.waitFor(() => {
      expect(deletedId).toBe('mem-1')
    })
  })
})
