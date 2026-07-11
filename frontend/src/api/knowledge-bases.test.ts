import { HttpResponse, http } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../mocks/server'
import {
  createKnowledgeBase,
  getCreateOptions,
  getDocumentStatus,
  getKnowledgeBase,
  listDocuments,
  listKnowledgeBases,
  reindexDocument,
  uploadDocument,
} from './knowledge-bases'

const rawKnowledgeBase = {
  id: 'kb-1',
  name: '人力资源制度库',
  code: 'hr-policy',
  department_id: 'department-1',
  description: '人力资源制度',
  rag_workspace: 'rag/hr-policy',
  default_query_mode: 'hybrid',
  status: 'active',
  permission: 'admin',
  document_count: 3,
}

describe('knowledge-base API adapters', () => {
  it('maps list, detail, and create-options responses into frontend models', async () => {
    server.use(
      http.get('*/api/knowledge-bases', () =>
        HttpResponse.json({ items: [rawKnowledgeBase], total: 1 }),
      ),
      http.get('*/api/knowledge-bases/create-options', () =>
        HttpResponse.json({
          departments: [{ id: 'department-1', code: 'hr', name: '人力资源部' }],
        }),
      ),
      http.get('*/api/knowledge-bases/kb-1', () =>
        HttpResponse.json(rawKnowledgeBase),
      ),
    )

    await expect(listKnowledgeBases()).resolves.toEqual([
      {
        id: 'kb-1',
        name: '人力资源制度库',
        code: 'hr-policy',
        departmentId: 'department-1',
        description: '人力资源制度',
        ragWorkspace: 'rag/hr-policy',
        defaultQueryMode: 'hybrid',
        status: 'active',
        permission: 'admin',
        documentCount: 3,
      },
    ])
    await expect(getKnowledgeBase('kb-1')).resolves.toMatchObject({
      departmentId: 'department-1',
      defaultQueryMode: 'hybrid',
      documentCount: 3,
    })
    await expect(getCreateOptions()).resolves.toEqual([
      { id: 'department-1', code: 'hr', name: '人力资源部' },
    ])
  })

  it('serializes create input using the backend snake_case contract', async () => {
    let requestBody: unknown
    server.use(
      http.post('*/api/knowledge-bases', async ({ request }) => {
        requestBody = await request.json()
        return HttpResponse.json(rawKnowledgeBase, { status: 201 })
      }),
    )

    await expect(
      createKnowledgeBase({
        name: '人力资源制度库',
        code: 'hr-policy',
        departmentId: 'department-1',
        description: '人力资源制度',
        defaultQueryMode: 'hybrid',
      }),
    ).resolves.toMatchObject({ id: 'kb-1', departmentId: 'department-1' })
    expect(requestBody).toEqual({
      name: '人力资源制度库',
      code: 'hr-policy',
      department_id: 'department-1',
      description: '人力资源制度',
      default_query_mode: 'hybrid',
    })
  })

  it('maps document list, upload, status, and reindex responses', async () => {
    let uploadBody: FormData | undefined
    server.use(
      http.get('*/api/knowledge-bases/kb-1/documents', ({ request }) => {
        const url = new URL(request.url)
        expect(url.searchParams.get('page')).toBe('2')
        expect(url.searchParams.get('page_size')).toBe('10')
        return HttpResponse.json({
          items: [{
            id: 'document-1',
            title: '员工手册',
            file_type: 'txt',
            index_status: 'indexed',
            source_version: 2,
            created_at: '2026-07-10T08:00:00Z',
          }],
          total: 11,
          page: 2,
          page_size: 10,
        })
      }),
      http.post('*/api/knowledge-bases/kb-1/documents', async ({ request }) => {
        uploadBody = await request.formData()
        return HttpResponse.json({
          document_id: 'document-1',
          title: '员工手册',
          file_type: 'txt',
          index_status: 'pending',
          index_job_id: 'job-1',
        }, { status: 201 })
      }),
      http.get('*/api/documents/document-1/status', () =>
        HttpResponse.json({
          document_id: 'document-1',
          index_status: 'failed',
          index_error: '索引服务不可用',
          latest_job: {
            id: 'job-1',
            status: 'failed',
            started_at: '2026-07-10T08:00:01Z',
            finished_at: '2026-07-10T08:00:02Z',
          },
        }),
      ),
      http.post('*/api/documents/document-1/index', () =>
        HttpResponse.json({ job_id: 'job-2', status: 'pending' }),
      ),
    )

    await expect(listDocuments('kb-1', 2, 10)).resolves.toEqual({
      items: [{
        id: 'document-1',
        title: '员工手册',
        fileType: 'txt',
        indexStatus: 'indexed',
        sourceVersion: 2,
        createdAt: '2026-07-10T08:00:00Z',
      }],
      total: 11,
      page: 2,
      pageSize: 10,
    })

    const file = new File(['policy'], 'employee.txt', { type: 'text/plain' })
    await expect(uploadDocument('kb-1', file, ' 员工手册 ')).resolves.toEqual({
      documentId: 'document-1',
      title: '员工手册',
      fileType: 'txt',
      indexStatus: 'pending',
      indexJobId: 'job-1',
    })
    expect(uploadBody?.get('file')).toMatchObject({
      size: 9,
      type: 'text/plain',
    })
    expect(uploadBody?.get('title')).toBe('员工手册')

    await expect(getDocumentStatus('document-1')).resolves.toEqual({
      documentId: 'document-1',
      indexStatus: 'failed',
      indexError: '索引服务不可用',
      latestJob: {
        id: 'job-1',
        status: 'failed',
        startedAt: '2026-07-10T08:00:01Z',
        finishedAt: '2026-07-10T08:00:02Z',
      },
    })
    await expect(reindexDocument('document-1')).resolves.toEqual({
      jobId: 'job-2',
      status: 'pending',
    })
  })
})
