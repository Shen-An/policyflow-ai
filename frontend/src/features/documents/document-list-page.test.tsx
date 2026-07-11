import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { HttpResponse, http } from 'msw'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import type { KnowledgeBase, ResourcePermission } from '../../api/knowledge-bases'
import { server } from '../../mocks/server'
import { DocumentListPage } from './document-list-page'

const knowledgeBase: KnowledgeBase = {
  id: 'kb-1',
  name: '人力资源制度库',
  code: 'hr-policy',
  departmentId: 'department-1',
  description: '员工制度',
  ragWorkspace: 'rag/hr-policy',
  defaultQueryMode: 'mix',
  status: 'active',
  permission: 'admin',
  documentCount: 1,
}

const failedDocument = {
  id: 'document-1',
  title: '员工手册',
  file_type: 'txt',
  index_status: 'failed',
  source_version: 1,
  created_at: '2026-07-10T08:00:00Z',
}

function renderPage(permission: ResourcePermission = 'admin') {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  const context = { knowledgeBase: { ...knowledgeBase, permission } }
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/knowledge-bases/kb-1/documents']}>
        <Routes>
          <Route
            path="/knowledge-bases/:kbId"
            element={<Outlet context={context} />}
          >
            <Route path="documents" element={<DocumentListPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function listResponse(items = [failedDocument]) {
  return HttpResponse.json({
    items,
    total: items.length,
    page: 1,
    page_size: 20,
  })
}

describe('DocumentListPage', () => {
  it('renders loading, empty, and retriable error states', async () => {
    let calls = 0
    server.use(
      http.get('*/api/knowledge-bases/kb-1/documents', () => {
        calls += 1
        return calls === 1
          ? HttpResponse.json(
              { error: { code: 'INTERNAL_ERROR', message: '服务暂不可用', details: null } },
              { status: 500 },
            )
          : listResponse([])
      }),
    )

    const user = userEvent.setup()
    renderPage()
    expect(screen.getByRole('status')).toHaveTextContent('正在加载文档')
    expect(await screen.findByRole('alert')).toHaveTextContent('文档列表加载失败')
    await user.click(screen.getByRole('button', { name: '重新加载' }))
    expect(await screen.findByText('还没有文档')).toBeVisible()
  })

  it('enforces read-only controls while preserving document visibility', async () => {
    server.use(
      http.get('*/api/knowledge-bases/kb-1/documents', () => listResponse()),
    )

    renderPage('read')
    const row = await screen.findByRole('row', { name: /员工手册/u })
    expect(row).toHaveTextContent('failed')
    expect(screen.getByText('当前为只读权限，不能上传或重新索引。')).toBeVisible()
    expect(screen.queryByRole('button', { name: '上传文档' })).not.toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: '重新索引' })).not.toBeInTheDocument()
  })

  it('validates upload files and maps duplicate backend errors', async () => {
    server.use(
      http.get('*/api/knowledge-bases/kb-1/documents', () => listResponse([])),
      http.post('*/api/knowledge-bases/kb-1/documents', () =>
        HttpResponse.json(
          {
            error: {
              code: 'DOCUMENT_DUPLICATE',
              message: 'Duplicate document',
              details: { document_id: 'document-existing' },
            },
          },
          { status: 409 },
        ),
      ),
    )

    const user = userEvent.setup({ applyAccept: false })
    renderPage('write')
    await screen.findByText('还没有文档')
    await user.click(screen.getByRole('button', { name: '上传文档' }))
    const dialog = screen.getByRole('dialog')
    const input = within(dialog).getByLabelText('文件')

    await user.upload(input, new File(['bad'], 'policy.exe'))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      '仅支持 TXT、Markdown、DOCX 和 PDF 文件。',
    )

    await user.upload(input, new File(['same policy'], 'policy.txt', { type: 'text/plain' }))
    await user.click(within(dialog).getByRole('button', { name: '上传文档' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      '同一知识库中已经存在内容相同的文档。',
    )
  })

  it('uploads a valid document and refreshes the list', async () => {
    let listCalls = 0
    let uploadedTitle: FormDataEntryValue | null = null
    server.use(
      http.get('*/api/knowledge-bases/kb-1/documents', () => {
        listCalls += 1
        return listResponse([])
      }),
      http.post('*/api/knowledge-bases/kb-1/documents', async ({ request }) => {
        const body = await request.formData()
        uploadedTitle = body.get('title')
        return HttpResponse.json({
          document_id: 'document-2',
          title: '员工福利',
          file_type: 'txt',
          index_status: 'pending',
          index_job_id: 'job-2',
        }, { status: 201 })
      }),
    )

    const user = userEvent.setup()
    renderPage()
    await screen.findByText('还没有文档')
    await user.click(screen.getByRole('button', { name: '上传文档' }))
    const dialog = screen.getByRole('dialog')
    await user.upload(
      within(dialog).getByLabelText('文件'),
      new File(['benefits'], 'benefits.txt', { type: 'text/plain' }),
    )
    await user.type(within(dialog).getByLabelText('标题（可选）'), '员工福利')
    await user.click(within(dialog).getByRole('button', { name: '上传文档' }))

    await vi.waitFor(() => expect(listCalls).toBeGreaterThan(1))
    expect(uploadedTitle).toBe('员工福利')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('retries failed indexing only for writable users', async () => {
    let reindexedDocument: string | null = null
    server.use(
      http.get('*/api/knowledge-bases/kb-1/documents', () => listResponse()),
      http.post('*/api/documents/:documentId/index', ({ params }) => {
        reindexedDocument = String(params.documentId)
        return HttpResponse.json({ job_id: 'job-retry', status: 'pending' })
      }),
    )

    const user = userEvent.setup()
    renderPage('admin')
    const row = await screen.findByRole('row', { name: /员工手册/u })
    await user.click(within(row).getByRole('button', { name: '重新索引' }))
    await vi.waitFor(() => expect(reindexedDocument).toBe('document-1'))
  })
})
