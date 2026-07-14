import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConfigProvider } from 'antd'
import { HttpResponse, http } from 'msw'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { server } from '../../mocks/server'
import { EvaluationPage } from './evaluation-page'

describe('EvaluationPage', () => {
  it('shows Hit@K and MRR as primary resume metrics', async () => {
    server.use(
      http.get('*/api/knowledge-bases', () => HttpResponse.json({ items: [], total: 0 })),
      http.get('*/api/eval/cases', () => HttpResponse.json([])),
      http.get('*/api/eval/retrieval-items', () => HttpResponse.json([])),
      http.get('*/api/eval/runs', () =>
        HttpResponse.json({
          items: [
            {
              id: 'run-1',
              name: 'Hybrid Demo',
              status: 'success',
              total_cases: 20,
              created_by: 'u1',
              created_at: '2026-07-10T08:00:00Z',
              started_at: null,
              finished_at: null,
              metrics: {
                mrr: 0.82,
                hit_at_1: 0.7,
                hit_at_3: 0.85,
                hit_at_5: 0.9,
                completed_cases: 20,
              },
              error_summary: null,
              request_id: 'req-1',
            },
          ],
          total: 1,
          page: 1,
          page_size: 20,
        }),
      ),
      http.get('*/api/eval/runs/run-1', () =>
        HttpResponse.json({
          id: 'run-1',
          name: 'Hybrid Demo',
          status: 'success',
          total_cases: 20,
          metrics: {
            mrr: 0.82,
            hit_at_1: 0.7,
            hit_at_3: 0.85,
            hit_at_5: 0.9,
            completed_cases: 20,
          },
          config_snapshot: {},
          created_by: 'u1',
          created_at: '2026-07-10T08:00:00Z',
          started_at: null,
          finished_at: null,
          error_summary: null,
          request_id: 'req-1',
          results: [
            {
              id: 'result-1',
              question: '差旅住宿标准？',
              answer: null,
              retrieved_sources: [],
              retrieval_metrics: {
                status: 'completed',
                mrr: 1,
                hit_at_1: 1,
                hit_at_3: 1,
                hit_at_5: 1,
                first_relevant_rank: 1,
              },
              answer_metrics: null,
              ragas_metrics: { status: 'skipped', reason: 'disabled' },
              type_statuses: { retrieval: 'completed' },
              score: 1,
              passed: true,
              error_message: null,
              latency_ms: 12,
            },
          ],
        }),
      ),
    )
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <ConfigProvider theme={{ token: { motion: false } }} autoInsertSpaceInButton={false}>
        <QueryClientProvider client={client}>
          <MemoryRouter initialEntries={['/evaluation?run_id=run-1']}>
            <EvaluationPage />
          </MemoryRouter>
        </QueryClientProvider>
      </ConfigProvider>,
    )

    expect(await screen.findByText('简历可直接写的主指标')).toBeVisible()
    expect(screen.getByText(/Hit@1=70\.0%/)).toBeVisible()
    expect(screen.getByText(/Hit@5=90\.0%/)).toBeVisible()
    expect(screen.getByText(/MRR=0\.8200/)).toBeVisible()
    // Per-case details are collapsed by default to keep the page scannable.
    expect(screen.getByText(/展开逐条检索结果/)).toBeVisible()
    expect(screen.queryByText('差旅住宿标准？')).not.toBeInTheDocument()
  })
})
