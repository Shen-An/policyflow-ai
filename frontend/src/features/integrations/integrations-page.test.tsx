import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { HttpResponse, http } from 'msw'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { server } from '../../mocks/server'
import { IntegrationsPage } from './integrations-page'

const mcp = {
  id: 'mcp-1',
  name: 'office-mock',
  type: 'mock',
  integration_mode: 'mock',
  endpoint: null,
  command_configured: true,
  config_summary: { password: '[REDACTED]', safe: 'visible' },
  enabled: true,
  health_status: 'unknown',
  tools: [],
  last_error_code: null,
  last_error_message: null,
  last_checked_at: null,
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <IntegrationsPage />
    </QueryClientProvider>,
  )
}

describe('IntegrationsPage', () => {
  it('marks mock integrations, runs health checks, and only shows redacted config', async () => {
    let healthy = false
    server.use(
      http.get('*/api/mcp/servers', () => HttpResponse.json({
        items: [{
          ...mcp,
          health_status: healthy ? 'healthy' : 'unknown',
          tools: healthy ? ['mcp.email.create_draft'] : [],
          last_checked_at: healthy ? '2026-07-10T09:00:00Z' : null,
        }],
      })),
      http.post('*/api/mcp/servers/mcp-1/health-check', () => {
        healthy = true
        return HttpResponse.json({
          server_id: 'mcp-1',
          health_status: 'healthy',
          tools: ['mcp.email.create_draft'],
          checked_at: '2026-07-10T09:00:00Z',
          error_code: null,
          error_message: null,
        })
      }),
    )
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByText('MOCK')).toBeVisible()
    await user.click(screen.getByText('工具列表与脱敏配置摘要'))
    expect(screen.getByText(/\[REDACTED\]/u)).toBeVisible()
    expect(screen.queryByText('plain-secret')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '健康检查' }))
    expect(await screen.findByText(/健康检查完成：healthy/u)).toBeVisible()
    expect(await screen.findByText('mcp.email.create_draft')).toBeVisible()
  })

  it('validates credential-free HTTP endpoints before creating an external config', async () => {
    server.use(http.get('*/api/mcp/servers', () => HttpResponse.json({ items: [] })))
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: '创建 MCP' }))
    const dialog = screen.getByRole('dialog', { name: '创建 MCP 集成' })
    await user.type(within(dialog).getByLabelText('名称'), 'external-http')
    await user.selectOptions(within(dialog).getByLabelText('集成模式'), 'http')
    await user.type(
      within(dialog).getByLabelText('Endpoint'),
      'https://user:password@example.com/mcp?token=secret',
    )
    await user.click(within(dialog).getByRole('button', { name: '保存' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('无凭据')
  })
})
