import { HttpResponse, http } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../mocks/server'
import { checkMCPHealth, createMCPServer, listMCPServers, updateMCPServer } from './mcp'
import { listSkills, runSkill, setSkillEnabled } from './skills'
import { getToolLog, listToolLogs, listTools } from './tools'

const skill = {
  name: 'summary',
  version: '1.0.0',
  description: 'Summary',
  enabled: true,
  risk_level: 'low',
  input_schema: { type: 'object', required: ['text'] },
  runnable: true,
  implemented: true,
  config_summary: { prompt: '[REDACTED]' },
}

const toolLog = {
  id: 'log-1',
  agent_name: 'manual',
  tool_name: 'mcp.call',
  user_id: 'user-1',
  conversation_id: null,
  request_id: 'request-1',
  input_summary: { password: '[REDACTED]' },
  output_summary: { status: 'mock' },
  status: 'success',
  error_message: null,
  latency_ms: 3,
  created_at: '2026-07-10T08:00:00Z',
}

const mcp = {
  id: 'mcp-1',
  name: 'office-mock',
  type: 'mock',
  integration_mode: 'mock',
  endpoint: null,
  command_configured: true,
  config_summary: { password: '[REDACTED]', safe: 'visible' },
  enabled: true,
  health_status: 'healthy',
  tools: ['mcp.email.create_draft'],
  last_error_code: null,
  last_error_message: null,
  last_checked_at: '2026-07-10T08:00:00Z',
}

describe('F6 API adapters', () => {
  it('maps Skill schema, state changes, and audited run results', async () => {
    server.use(
      http.get('*/api/skills', () => HttpResponse.json({ items: [skill] })),
      http.post('*/api/skills/summary/disable', () =>
        HttpResponse.json({ ...skill, enabled: false, runnable: false }),
      ),
      http.post('*/api/skills/summary/run', async ({ request }) => {
        expect(await request.json()).toEqual({ input: { text: 'Policy text' } })
        return HttpResponse.json({
          name: 'summary',
          output: { summary: 'Policy text' },
          audit_id: 'audit-1',
          request_id: 'request-1',
        })
      }),
    )
    await expect(listSkills()).resolves.toMatchObject([
      { riskLevel: 'low', inputSchema: { type: 'object' }, configSummary: { prompt: '[REDACTED]' } },
    ])
    await expect(setSkillEnabled('summary', false)).resolves.toMatchObject({
      enabled: false,
      runnable: false,
    })
    await expect(runSkill('summary', { text: 'Policy text' })).resolves.toMatchObject({
      auditId: 'audit-1',
      requestId: 'request-1',
    })
  })

  it('maps Tool inventory, pagination, related request, and redacted detail', async () => {
    server.use(
      http.get('*/api/tools', () => HttpResponse.json({
        items: [{
          name: 'mcp.call',
          description: 'MCP',
          input_schema: { type: 'object' },
          output_schema: { type: 'object' },
          risk_level: 'medium',
          enabled: true,
          timeout_seconds: 30,
        }],
      })),
      http.get('*/api/tool-call-logs', ({ request }) => {
        const search = new URL(request.url).searchParams
        expect(search.get('tool_name')).toBe('mcp.call')
        expect(search.get('status')).toBe('success')
        return HttpResponse.json({ items: [toolLog], total: 1, page: 1, page_size: 20 })
      }),
      http.get('*/api/tool-call-logs/log-1', () => HttpResponse.json(toolLog)),
    )
    await expect(listTools()).resolves.toMatchObject([{ timeoutSeconds: 30 }])
    await expect(listToolLogs({
      page: 1,
      pageSize: 20,
      toolName: 'mcp.call',
      status: 'success',
    })).resolves.toMatchObject({
      total: 1,
      items: [{ requestId: 'request-1', inputSummary: { password: '[REDACTED]' } }],
    })
    await expect(getToolLog('log-1')).resolves.toMatchObject({
      toolName: 'mcp.call',
      outputSummary: { status: 'mock' },
    })
  })

  it('maps MCP create, edit, list, and stable health results without plaintext config', async () => {
    server.use(
      http.get('*/api/mcp/servers', () => HttpResponse.json({ items: [mcp] })),
      http.post('*/api/mcp/servers', async ({ request }) => {
        expect(await request.json()).toMatchObject({
          type: 'mock',
          integration_mode: 'mock',
          config: { password: 'submit-only' },
        })
        return HttpResponse.json(mcp, { status: 201 })
      }),
      http.patch('*/api/mcp/servers/mcp-1', async ({ request }) => {
        expect(await request.json()).toEqual({ name: 'renamed' })
        return HttpResponse.json({ ...mcp, name: 'renamed' })
      }),
      http.post('*/api/mcp/servers/mcp-1/health-check', () => HttpResponse.json({
        server_id: 'mcp-1',
        health_status: 'healthy',
        tools: ['mcp.email.create_draft'],
        checked_at: '2026-07-10T09:00:00Z',
        error_code: null,
        error_message: null,
      })),
    )
    await expect(listMCPServers()).resolves.toMatchObject([
      { integrationMode: 'mock', commandConfigured: true, configSummary: { password: '[REDACTED]' } },
    ])
    await expect(createMCPServer({
      name: 'office-mock',
      type: 'mock',
      integrationMode: 'mock',
      config: { password: 'submit-only' },
      enabled: true,
    })).resolves.toMatchObject({ type: 'mock' })
    await expect(updateMCPServer('mcp-1', { name: 'renamed' })).resolves.toMatchObject({
      name: 'renamed',
    })
    await expect(checkMCPHealth('mcp-1')).resolves.toMatchObject({
      healthStatus: 'healthy',
      checkedAt: '2026-07-10T09:00:00Z',
    })
  })
})
