import { apiClient } from './client'

export type AuditLog = {
  id: string
  actorId: string | null
  actor: { id: string; username: string; displayName: string } | null
  action: string
  targetType: string
  targetId: string | null
  detail: Record<string, unknown>
  ipAddress: string | null
  requestId: string | null
  createdAt: string
}

export type AuditListResult = {
  items: AuditLog[]
  total: number
  page: number
  pageSize: number
}

type AuditRaw = {
  id: string
  actor_id: string | null
  actor: { id: string; username: string; display_name: string } | null
  action: string
  target_type: string
  target_id: string | null
  detail: Record<string, unknown>
  ip_address: string | null
  request_id: string | null
  created_at: string
}

function toAudit(raw: AuditRaw): AuditLog {
  return {
    id: raw.id,
    actorId: raw.actor_id,
    actor: raw.actor
      ? { id: raw.actor.id, username: raw.actor.username, displayName: raw.actor.display_name }
      : null,
    action: raw.action,
    targetType: raw.target_type,
    targetId: raw.target_id,
    detail: raw.detail,
    ipAddress: raw.ip_address,
    requestId: raw.request_id,
    createdAt: raw.created_at,
  }
}

export async function listAuditLogs(
  filters: {
    page: number
    pageSize: number
    action?: string
    targetType?: string
    actorId?: string
    createdFrom?: string
    createdTo?: string
  },
  signal?: AbortSignal,
): Promise<AuditListResult> {
  const search = new URLSearchParams({
    page: String(filters.page),
    page_size: String(filters.pageSize),
  })
  if (filters.action) search.set('action', filters.action)
  if (filters.targetType) search.set('target_type', filters.targetType)
  if (filters.actorId) search.set('actor_id', filters.actorId)
  if (filters.createdFrom) search.set('created_from', filters.createdFrom)
  if (filters.createdTo) search.set('created_to', filters.createdTo)
  const raw = await apiClient.request<{
    items: AuditRaw[]
    total: number
    page: number
    page_size: number
  }>(`/api/audit-logs?${search.toString()}`, { signal })
  return {
    items: raw.items.map(toAudit),
    total: raw.total,
    page: raw.page,
    pageSize: raw.page_size,
  }
}

export async function getAuditLog(id: string, signal?: AbortSignal): Promise<AuditLog> {
  return toAudit(
    await apiClient.request<AuditRaw>(`/api/audit-logs/${encodeURIComponent(id)}`, {
      signal,
    }),
  )
}
