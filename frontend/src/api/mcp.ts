import { apiClient } from './client'

export type MCPServer = {
  id: string
  name: string
  type: string
  integrationMode: string
  endpoint: string | null
  commandConfigured: boolean
  configSummary: Record<string, unknown>
  enabled: boolean
  healthStatus: string
  tools: string[]
  lastErrorCode: string | null
  lastErrorMessage: string | null
  lastCheckedAt: string | null
}

export type MCPServerInput = {
  name: string
  type: 'mock' | 'external'
  integrationMode: 'mock' | 'stdio' | 'http'
  endpoint?: string
  command?: string
  config?: Record<string, unknown>
  enabled: boolean
}

export type MCPServerUpdate = Partial<MCPServerInput>

export type MCPHealthResult = {
  serverId: string
  healthStatus: string
  tools: string[]
  checkedAt: string
  errorCode: string | null
  errorMessage: string | null
}

type MCPServerRaw = {
  id: string
  name: string
  type: string
  integration_mode: string
  endpoint: string | null
  command_configured: boolean
  config_summary: Record<string, unknown>
  enabled: boolean
  health_status: string
  tools: string[]
  last_error_code: string | null
  last_error_message: string | null
  last_checked_at: string | null
}

function toServer(raw: MCPServerRaw): MCPServer {
  return {
    id: raw.id,
    name: raw.name,
    type: raw.type,
    integrationMode: raw.integration_mode,
    endpoint: raw.endpoint,
    commandConfigured: raw.command_configured,
    configSummary: raw.config_summary,
    enabled: raw.enabled,
    healthStatus: raw.health_status,
    tools: raw.tools,
    lastErrorCode: raw.last_error_code,
    lastErrorMessage: raw.last_error_message,
    lastCheckedAt: raw.last_checked_at,
  }
}

function toPayload(input: MCPServerUpdate): Record<string, unknown> {
  return {
    ...(input.name !== undefined ? { name: input.name } : {}),
    ...(input.type !== undefined ? { type: input.type } : {}),
    ...(input.integrationMode !== undefined
      ? { integration_mode: input.integrationMode }
      : {}),
    ...(input.endpoint !== undefined ? { endpoint: input.endpoint || null } : {}),
    ...(input.command !== undefined ? { command: input.command || null } : {}),
    ...(input.config !== undefined ? { config: input.config } : {}),
    ...(input.enabled !== undefined ? { enabled: input.enabled } : {}),
  }
}

export async function listMCPServers(signal?: AbortSignal): Promise<MCPServer[]> {
  const raw = await apiClient.request<{ items: MCPServerRaw[] }>('/api/mcp/servers', { signal })
  return raw.items.map(toServer)
}

export async function createMCPServer(input: MCPServerInput): Promise<MCPServer> {
  return toServer(await apiClient.request<MCPServerRaw>('/api/mcp/servers', {
    method: 'POST',
    body: JSON.stringify(toPayload(input)),
  }))
}

export async function updateMCPServer(
  id: string,
  input: MCPServerUpdate,
): Promise<MCPServer> {
  return toServer(await apiClient.request<MCPServerRaw>(
    `/api/mcp/servers/${encodeURIComponent(id)}`,
    { method: 'PATCH', body: JSON.stringify(toPayload(input)) },
  ))
}

export async function checkMCPHealth(id: string): Promise<MCPHealthResult> {
  const raw = await apiClient.request<{
    server_id: string
    health_status: string
    tools: string[]
    checked_at: string
    error_code: string | null
    error_message: string | null
  }>(`/api/mcp/servers/${encodeURIComponent(id)}/health-check`, { method: 'POST' })
  return {
    serverId: raw.server_id,
    healthStatus: raw.health_status,
    tools: raw.tools,
    checkedAt: raw.checked_at,
    errorCode: raw.error_code,
    errorMessage: raw.error_message,
  }
}
