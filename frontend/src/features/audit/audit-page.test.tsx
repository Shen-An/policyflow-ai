import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConfigProvider } from 'antd'
import { HttpResponse, http } from 'msw'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { server } from '../../mocks/server'
import { AuditPage } from './audit-page'

const audit = {
  id: 'audit-1', actor_id: 'user-1',
  actor: { id: 'user-1', username: 'admin', display_name: '管理员' },
  action: 'faq.approve', target_type: 'faq_draft', target_id: 'faq-1',
  detail: { password: '[REDACTED]', safe: 'visible' },
  ip_address: '127.0.0.1', request_id: 'request-1',
  created_at: '2026-07-10T08:00:00Z',
}

describe('AuditPage', () => {
  it('renders paginated records and redacted details', async () => {
    server.use(
      http.get('*/api/audit-logs', () => HttpResponse.json({ items: [audit], total: 1, page: 1, page_size: 20 })),
      http.get('*/api/audit-logs/audit-1', () => HttpResponse.json(audit)),
    )
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const user = userEvent.setup()
    render(
      <ConfigProvider theme={{ token: { motion: false } }} button={{ autoInsertSpace: false }}>
        <QueryClientProvider client={client}>
          <MemoryRouter>
            <AuditPage />
          </MemoryRouter>
        </QueryClientProvider>
      </ConfigProvider>,
    )
    expect(await screen.findByText('审核通过 FAQ')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /详情/ }))
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toHaveTextContent('request-1')
      expect(screen.getByRole('dialog')).toHaveTextContent('[REDACTED]')
      expect(screen.getByRole('dialog')).toHaveTextContent('visible')
    })
  })
})
