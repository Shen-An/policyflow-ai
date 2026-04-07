import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { HttpResponse, http } from 'msw'
import { render, screen } from '@testing-library/react'
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
    render(<QueryClientProvider client={client}><MemoryRouter><AuditPage /></MemoryRouter></QueryClientProvider>)
    expect(await screen.findByText('faq.approve')).toBeVisible()
    await user.click(screen.getByRole('button', { name: '查看' }))
    const dialog = screen.getByRole('dialog')
    expect(await screen.findByText('request-1')).toBeVisible()
    expect(dialog).toHaveTextContent('[REDACTED]')
    expect(dialog).toHaveTextContent('visible')
  })
})
