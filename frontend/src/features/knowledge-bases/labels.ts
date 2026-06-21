import type { QueryMode, ResourcePermission } from '../../api/knowledge-bases'

export const permissionLabel: Record<ResourcePermission, string> = {
  read: '只读',
  write: '可写',
  admin: '管理',
}

export const permissionColor: Record<ResourcePermission, string> = {
  read: 'default',
  write: 'processing',
  admin: 'purple',
}

export const statusLabel: Record<string, string> = {
  active: '启用',
  disabled: '停用',
}

export const statusColor: Record<string, string> = {
  active: 'success',
  disabled: 'default',
}

export const queryModeLabel: Record<QueryMode, string> = {
  naive: 'Naive',
  local: 'Local',
  global: 'Global',
  hybrid: 'Hybrid',
  mix: 'Mix',
}

export const queryModeOptions = (
  Object.entries(queryModeLabel) as Array<[QueryMode, string]>
).map(([value, label]) => ({ value, label }))
