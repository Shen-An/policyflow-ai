import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConfigProvider } from 'antd'
import { HttpResponse, http } from 'msw'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { server } from '../../mocks/server'
import { FAQReviewPage } from './faq-review-page'

const kb = {
  id: 'kb-1', name: 'HR', code: 'hr', department_id: 'd1', description: '',
  rag_workspace: 'rag/hr', default_query_mode: 'hybrid', status: 'active',
  permission: 'admin', document_count: 1,
}
const faq = {
  id: 'faq-1', knowledge_base_id: 'kb-1', knowledge_base_name: 'HR',
  source_document_id: 'doc-1', source_document_title: 'Leave Policy',
  source_conversation_id: null, question: '如何请假？', answer: '需要经理审批。',
  status: 'draft', generated_by: 'ai', reviewer_id: null, review_note: null,
  created_at: '2026-07-10T08:00:00Z', updated_at: '2026-07-10T08:00:00Z',
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <ConfigProvider theme={{ token: { motion: false } }} autoInsertSpaceInButton={false}>
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <FAQReviewPage />
        </MemoryRouter>
      </QueryClientProvider>
    </ConfigProvider>,
  )
}

describe('FAQReviewPage', () => {
  it('shows sources and requires explicit approval confirmation', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    server.use(
      http.get('*/api/knowledge-bases', () => HttpResponse.json({ items: [kb], total: 1 })),
      http.get('*/api/faq-drafts', () => HttpResponse.json({ items: [faq] })),
      http.post('*/api/faq-drafts/faq-1/approve', () => HttpResponse.json({
        faq_draft: { ...faq, status: 'approved' }, document_id: 'doc-faq', index_job_id: 'job-1',
      })),
      http.get('*/api/documents/doc-faq/status', () => HttpResponse.json({
        document_id: 'doc-faq', index_status: 'indexed', index_error: null, latest_job: null,
      })),
    )
    const user = userEvent.setup()
    renderPage()
    const card = await screen.findByRole('article')
    expect(card).toHaveTextContent('Leave Policy')
    await user.click(within(card).getByRole('button', { name: '审核通过' }))
    expect(window.confirm).toHaveBeenCalled()
    expect(await screen.findByRole('status')).toHaveTextContent('indexed')
  })

  it('requires a rejection reason and maps the reviewed state', async () => {
    server.use(
      http.get('*/api/knowledge-bases', () => HttpResponse.json({ items: [kb], total: 1 })),
      http.get('*/api/faq-drafts', () => HttpResponse.json({ items: [faq] })),
      http.post('*/api/faq-drafts/faq-1/reject', async ({ request }) => {
        expect(await request.json()).toEqual({ reason: '重复主题' })
        return HttpResponse.json({ ...faq, status: 'rejected', review_note: '重复主题' })
      }),
    )
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: '驳回' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('button', { name: '确认驳回' })).toBeDisabled()
    await user.type(within(dialog).getByLabelText('驳回原因'), '重复主题')
    await user.click(within(dialog).getByRole('button', { name: '确认驳回' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
