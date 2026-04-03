import { apiClient } from './client'

export type Tool = {
  name: string
  description: string
  inputSchema: Record<string, unknown>
  outputSchema: Record<string, unknown>
  riskLevel: string
  enabled: boolean
  timeoutSeconds: number
}

export type ToolCallLog = {
  id: string
  agentName: string
  toolName: string
  userId: string | null
  conversationId: string | null
  requestId: string | null
  inputSummary: Record<string, unknown>
  outputSummary: Record<string, unknown>
  status: string
  errorMessage: string | null
  latencyMs: number
  createdAt: string
}

export type ToolLogList = {
  items: ToolCallLog[]
  total: number
  page: number
  pageSize: number
}

type ToolRaw = {
  name: string
  description: string
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown>
  risk_level: string
  enabled: boolean
  timeout_seconds: number
}

type ToolCallLogRaw = {
  id: string
  agent_name: string
  tool_name: string
  user_id: string | null
  conversation_id: string | null
  request_id: string | null
  input_summary: Record<string, unknown>
  output_summary: Record<string, unknown>
  status: string
  error_message: string | null
  latency_ms: number
  created_at: string
}

function toLog(raw: ToolCallLogRaw): ToolCallLog {
  return {
    id: raw.id,
    agentName: raw.agent_name,
    toolName: raw.tool_name,
    userId: raw.user_id,
    conversationId: raw.conversation_id,
    requestId: raw.request_id,
    inputSummary: raw.input_summary,
    outputSummary: raw.output_summary,
    status: raw.status,
    errorMessage: raw.error_message,
    latencyMs: raw.latency_ms,
    createdAt: raw.created_at,
  }
}

export async function listTools(signal?: AbortSignal): Promise<Tool[]> {
  const raw = await apiClient.request<{ items: ToolRaw[] }>('/api/tools', { signal })
  return raw.items.map((item) => ({
    name: item.name,
    description: item.description,
    inputSchema: item.input_schema,
    outputSchema: item.output_schema,
    riskLevel: item.risk_level,
    enabled: item.enabled,
    timeoutSeconds: item.timeout_seconds,
  }))
}

export async function listToolLogs(
  filters: { page: number; pageSize: number; toolName?: string; status?: string },
  signal?: AbortSignal,
): Promise<ToolLogList> {
  const search = new URLSearchParams({
    page: String(filters.page),
    page_size: String(filters.pageSize),
  })
  if (filters.toolName) search.set('tool_name', filters.toolName)
  if (filters.status) search.set('status', filters.status)
  const raw = await apiClient.request<{
    items: ToolCallLogRaw[]
    total: number
    page: number
    page_size: number
  }>(`/api/tool-call-logs?${search.toString()}`, { signal })
  return {
    items: raw.items.map(toLog),
    total: raw.total,
    page: raw.page,
    pageSize: raw.page_size,
  }
}

export async function getToolLog(id: string, signal?: AbortSignal): Promise<ToolCallLog> {
  return toLog(await apiClient.request<ToolCallLogRaw>(
    `/api/tool-call-logs/${encodeURIComponent(id)}`,
    { signal },
  ))
}
