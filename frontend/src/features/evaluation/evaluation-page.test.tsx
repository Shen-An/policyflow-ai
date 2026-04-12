import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { HttpResponse, http } from 'msw'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { server } from '../../mocks/server'
import { EvaluationPage } from './evaluation-page'

describe('EvaluationPage', () => {
  it('renders terminal skipped semantics without treating them as zero scores', async () => {
    server.use(
      http.get('*/api/knowledge-bases', () => HttpResponse.json({ items: [], total: 0 })),
      http.get('*/api/eval/cases', () => HttpResponse.json([])),
      http.get('*/api/eval/retrieval-items', () => HttpResponse.json([])),
      http.get('*/api/eval/runs', () => HttpResponse.json({
        items: [{ id: 'run-1', name: 'Skipped Run', status: 'skipped', total_cases: 1,
          created_by: 'u1', created_at: '2026-07-10T08:00:00Z', started_at: null,
          finished_at: null, metrics: { skipped_cases: 1 }, error_summary: null, request_id: 'req-1' }],
        total: 1, page: 1, page_size: 20,
      })),
      http.get('*/api/eval/runs/run-1', () => HttpResponse.json({
        id: 'run-1', name: 'Skipped Run', status: 'skipped', total_cases: 1,
        metrics: { skipped_cases: 1 }, config_snapshot: {}, created_by: 'u1',
        created_at: '2026-07-10T08:00:00Z', started_at: null, finished_at: null,
        error_summary: null, request_id: 'req-1',
        results: [{ id: 'result-1', question: 'Q', answer: null, retrieved_sources: [],
          retrieval_metrics: { status: 'skipped', reason: 'empty_ground_truth' },
          answer_metrics: null, ragas_metrics: { status: 'skipped', reason: 'disabled' },
          type_statuses: { retrieval: 'skipped' }, score: 0, passed: false,
          error_message: null, latency_ms: 0 }],
      })),
    )
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/evaluation?run_id=run-1']}><EvaluationPage /></MemoryRouter></QueryClientProvider>)
    expect(await screen.findByText('retrieval:skipped')).toBeVisible()
    expect(screen.getByText('skipped（empty_ground_truth）')).toBeVisible()
    expect(screen.getByText('skipped（disabled）')).toBeVisible()
  })
})
