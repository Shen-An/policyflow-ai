import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App as AntdApp } from 'antd'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../../mocks/server'
import { ReconciliationPage } from './reconciliation-page'

const rawIssue = {
  issue_id: 'issue-1',
  store_pair: 'postgres->milvus',
  issue_kind: 'missing_chunk',
  resource_kind: 'chunk',
  resource_id: 'chunk-1',
  version_id: 'v1',
  observed_fingerprint: 'sha256:aaa',
  expected_fingerprint: 'sha256:bbb',
  severity: 'warning',
  state: 'open',
  attempts: 3,
  max_attempts: 5,
  next_attempt_at: null,
  last_error_code: 'MISSING_CHUNK',
  version: 4,
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <AntdApp>
        <ReconciliationPage />
      </AntdApp>
    </QueryClientProvider>,
  )
}

describe('ReconciliationPage', () => {
  it('renders fingerprints and retries an actionable issue', async () => {
    let retried = false
    server.use(
      http.get('*/api/v2/admin/reconciliation', () =>
        HttpResponse.json({
          items: retried ? [{ ...rawIssue, attempts: 0 }] : [rawIssue],
          total: 1,
          limit: 20,
          offset: 0,
        }),
      ),
      http.get('*/api/v2/admin/capacity', () =>
        HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 }),
      ),
      http.post('*/api/v2/admin/reconciliation/issue-1/retry', () => {
        retried = true
        return HttpResponse.json({
          issue_id: 'issue-1',
          state: 'open',
          previous_state: 'open',
        })
      }),
    )

    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByText('missing_chunk')).toBeVisible()
    expect(screen.getByText(/观察到 sha256:aaa/)).toBeVisible()
    expect(screen.getByText('3/5 次 · MISSING_CHUNK')).toBeVisible()

    await user.click(screen.getByRole('button', { name: '重试' }))
    // The antd notice wrapper exists before its title, so assert presence rather
    // than visibility; the jsdom animation never advances.
    expect(await screen.findByText('已重置重试预算')).toBeInTheDocument()
    expect(await screen.findByText('0/5 次 · MISSING_CHUNK')).toBeVisible()
  })

  it('explains the cross-tenant permission instead of faking an empty table', async () => {
    server.use(
      http.get('*/api/v2/admin/reconciliation', () =>
        HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 }),
      ),
      http.get('*/api/v2/admin/capacity', () =>
        HttpResponse.json(
          {
            success: false,
            error: {
              code: 'AUTH_FORBIDDEN',
              message: 'cross_tenant_admin is required',
              details: null,
            },
            request_id: 'req-1',
          },
          { status: 403 },
        ),
      ),
    )

    renderPage()

    expect(await screen.findByText('容量证据仅平台管理员可见')).toBeVisible()
    expect(
      screen.getByText(/cross_tenant_admin/),
    ).toBeVisible()
  })
})
