import { HttpResponse, http } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../mocks/server'
import { getAuditLog, listAuditLogs } from './audit'
import { createEvalRun, getEvalRun, listEvalRuns, retrievalDebug } from './eval'
import { approveFAQ, listFAQDrafts, rejectFAQ } from './faq'

const faq = {
  id: 'faq-1', knowledge_base_id: 'kb-1', knowledge_base_name: 'HR',
  source_document_id: 'doc-1', source_document_title: 'Leave Policy',
  source_conversation_id: null, question: '如何请假？', answer: '需要审批。',
  status: 'draft', generated_by: 'ai', reviewer_id: null, review_note: null,
  created_at: '2026-07-10T08:00:00Z', updated_at: '2026-07-10T08:00:00Z',
}

describe('F5 API adapters', () => {
  it('maps FAQ list, approve, and reject contracts', async () => {
    server.use(
      http.get('*/api/faq-drafts', ({ request }) => {
        const search = new URL(request.url).searchParams
        expect(search.get('status')).toBe('draft')
        return HttpResponse.json({ items: [faq] })
      }),
      http.post('*/api/faq-drafts/faq-1/approve', () => HttpResponse.json({
        faq_draft: { ...faq, status: 'approved' },
        document_id: 'doc-faq',
        index_job_id: 'job-1',
      })),
      http.post('*/api/faq-drafts/faq-1/reject', async ({ request }) => {
        expect(await request.json()).toEqual({ reason: '重复主题' })
        return HttpResponse.json({ ...faq, status: 'rejected', review_note: '重复主题' })
      }),
    )
    await expect(listFAQDrafts('kb-1', 'draft')).resolves.toMatchObject([
      { knowledgeBaseName: 'HR', sourceDocumentTitle: 'Leave Policy' },
    ])
    await expect(approveFAQ('faq-1')).resolves.toMatchObject({
      documentId: 'doc-faq', faq: { status: 'approved' },
    })
    await expect(rejectFAQ('faq-1', '重复主题')).resolves.toMatchObject({
      status: 'rejected', reviewNote: '重复主题',
    })
  })

  it('maps paginated audit and redacted detail contracts', async () => {
    const raw = {
      id: 'audit-1', actor_id: 'user-1',
      actor: { id: 'user-1', username: 'admin', display_name: '管理员' },
      action: 'faq.approve', target_type: 'faq_draft', target_id: 'faq-1',
      detail: { password: '[REDACTED]' }, ip_address: '127.0.0.1',
      request_id: 'request-1', created_at: '2026-07-10T08:00:00Z',
    }
    server.use(
      http.get('*/api/audit-logs', () =>
        HttpResponse.json({ items: [raw], total: 1, page: 1, page_size: 20 }),
      ),
      http.get('*/api/audit-logs/audit-1', () => HttpResponse.json(raw)),
    )
    await expect(listAuditLogs({ page: 1, pageSize: 20 })).resolves.toMatchObject({
      items: [{ actor: { displayName: '管理员' }, requestId: 'request-1' }],
      pageSize: 20,
    })
    await expect(getAuditLog('audit-1')).resolves.toMatchObject({
      detail: { password: '[REDACTED]' },
    })
  })

  it('maps eval pending/history/terminal result and retrieval debug', async () => {
    const run = {
      id: 'run-1', name: 'F5 Run', status: 'pending', total_cases: 1,
      metrics: {}, config_snapshot: {}, created_by: 'user-1',
      created_at: '2026-07-10T08:00:00Z', started_at: null, finished_at: null,
      error_summary: null, request_id: 'request-1', results: [],
    }
    server.use(
      http.post('*/api/eval/runs', () => HttpResponse.json(run, { status: 201 })),
      http.get('*/api/eval/runs', () => HttpResponse.json({
        items: [{ ...run, config_snapshot: undefined, results: undefined }],
        total: 1, page: 1, page_size: 20,
      })),
      http.get('*/api/eval/runs/run-1', () => HttpResponse.json({
        ...run, status: 'skipped', metrics: { skipped_cases: 1 },
        results: [{
          id: 'result-1', question: 'Q', answer: null, retrieved_sources: [],
          retrieval_metrics: { status: 'skipped', reason: 'empty_ground_truth' },
          answer_metrics: null, ragas_metrics: { status: 'skipped', reason: 'disabled' },
          type_statuses: { retrieval: 'skipped' }, score: 0, passed: false,
          error_message: null, latency_ms: 0,
        }],
      })),
      http.post('*/api/eval/retrieval-debug', () => HttpResponse.json({
        query: 'leave', items: [{ rank: 1, document_title: 'Leave Policy' }], warnings: [],
      })),
    )
    await expect(createEvalRun({
      name: 'F5 Run', caseIds: [], retrievalItemIds: ['item-1'],
      evalTypes: ['retrieval'], queryMode: 'hybrid',
    })).resolves.toMatchObject({ status: 'pending' })
    await expect(listEvalRuns(1, 20)).resolves.toMatchObject({ total: 1 })
    await expect(getEvalRun('run-1')).resolves.toMatchObject({
      status: 'skipped',
      results: [{ typeStatuses: { retrieval: 'skipped' } }],
    })
    await expect(retrievalDebug({
      query: 'leave', knowledgeBaseIds: ['kb-1'], queryMode: 'hybrid',
    })).resolves.toMatchObject({ items: [{ document_title: 'Leave Policy' }] })
  })
})
