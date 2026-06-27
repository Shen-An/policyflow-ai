import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConfigProvider, Modal } from 'antd'
import { HttpResponse, http } from 'msw'
import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { server } from '../../mocks/server'
import { SkillsPage } from './skills-page'

const summarySkill = {
  name: 'summary',
  version: '1.0.0',
  description: '制度摘要',
  enabled: true,
  risk_level: 'low',
  input_schema: {
    type: 'object',
    properties: { text: { type: 'string' } },
  },
  runnable: true,
  implemented: true,
  config_summary: {},
}

const log = {
  id: 'log-1',
  agent_name: 'manual',
  tool_name: 'mcp.call',
  user_id: 'admin-1',
  conversation_id: null,
  request_id: 'tool-request-1',
  input_summary: { arguments: { password: '[REDACTED]', safe: 'visible' } },
  output_summary: { status: 'mock' },
  status: 'success',
  error_message: null,
  latency_ms: 5,
  created_at: '2026-07-10T08:00:00Z',
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <ConfigProvider theme={{ token: { motion: false } }} button={{ autoInsertSpace: false }}>
      <QueryClientProvider client={client}>
        <MemoryRouter><SkillsPage /></MemoryRouter>
      </QueryClientProvider>
    </ConfigProvider>,
  )
}

afterEach(() => {
  Modal.destroyAll()
  vi.restoreAllMocks()
})

describe('SkillsPage', () => {
  beforeEach(() => {
    Modal.destroyAll()
    document.body.innerHTML = ''
  })
  it('runs only implemented Skills and displays audit identifiers and Tool redaction', async () => {
    server.use(
      http.get('*/api/skills', () => HttpResponse.json({
        items: [
          summarySkill,
          {
            ...summarySkill,
            name: 'knowledge_qa',
            implemented: false,
            runnable: false,
            input_schema: {},
          },
        ],
      })),
      http.get('*/api/tools', () => HttpResponse.json({
        items: [{
          name: 'mcp.call',
          description: 'MCP',
          input_schema: {},
          output_schema: {},
          risk_level: 'medium',
          enabled: true,
          timeout_seconds: 30,
        }],
      })),
      http.get('*/api/tool-call-logs', () =>
        HttpResponse.json({ items: [log], total: 1, page: 1, page_size: 20 }),
      ),
      http.get('*/api/tool-call-logs/log-1', () => HttpResponse.json(log)),
      http.post('*/api/skills/summary/run', async ({ request }) => {
        expect(await request.json()).toEqual({ input: { text: 'Annual leave policy' } })
        return HttpResponse.json({
          name: 'summary',
          output: { summary: 'Annual leave policy' },
          audit_id: 'audit-1',
          request_id: 'skill-request-1',
        })
      }),
    )
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByText('knowledge_qa')).toBeVisible()
    const rows = screen.getAllByRole('row')
    const unavailableRow = rows.find((row) => within(row).queryByText('knowledge_qa'))
    expect(within(unavailableRow as HTMLElement).getByRole('button', { name: '手动运行' })).toBeDisabled()

    const summaryRow = rows.find((row) => within(row).queryByText('summary'))
    await user.click(within(summaryRow as HTMLElement).getByRole('button', { name: '手动运行' }))
    const runDialog = await screen.findByRole('dialog')
    expect(runDialog).toHaveTextContent('运行 summary')
    const input = within(runDialog).getByLabelText('运行参数')
    fireEvent.change(input, { target: { value: JSON.stringify({ text: 'Annual leave policy' }) } })
    await user.click(within(runDialog).getByRole('button', { name: '确认运行' }))
    expect(await within(runDialog).findByText('Audit ID：audit-1')).toBeVisible()
    expect(within(runDialog).getByText('Request ID：skill-request-1')).toBeVisible()
    await user.click(within(runDialog).getByLabelText('关闭运行对话框'))

    await user.click(screen.getByRole('button', { name: '查看' }))
    const logDialog = await screen.findByRole('dialog')
    expect(logDialog).toHaveTextContent('Tool 日志详情')
    await user.click(within(logDialog).getByText('脱敏输入参数'))
    expect(await within(logDialog).findByText(/\[REDACTED\]/u)).toBeVisible()
    expect(within(logDialog).getByText(/visible/u)).toBeVisible()
  }, 15_000)

  it('requires confirmation before changing Skill state', async () => {
    let disabled = false
    server.use(
      http.get('*/api/skills', () => HttpResponse.json({
        items: [{ ...summarySkill, enabled: !disabled, runnable: !disabled }],
      })),
      http.get('*/api/tools', () => HttpResponse.json({ items: [] })),
      http.get('*/api/tool-call-logs', () =>
        HttpResponse.json({ items: [], total: 0, page: 1, page_size: 20 }),
      ),
      http.post('*/api/skills/summary/disable', () => {
        disabled = true
        return HttpResponse.json({ ...summarySkill, enabled: false, runnable: false })
      }),
    )
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: '禁用' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('确认禁用 Skill“summary”吗')
    await user.click(within(dialog).getByRole('button', { name: '禁用' }))
    expect(await screen.findByText('已禁用')).toBeVisible()
  }, 15_000)
})
