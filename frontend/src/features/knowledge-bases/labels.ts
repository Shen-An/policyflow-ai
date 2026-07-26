import type { QueryMode, ResourcePermission } from '../../api/knowledge-bases'
import type { ChipTone } from '../../components/ui/quiet-chip'

export const permissionLabel: Record<ResourcePermission, string> = {
  read: '只读',
  write: '可写',
  admin: '管理',
}

export const permissionTone: Record<ResourcePermission, ChipTone> = {
  read: 'neutral',
  write: 'active',
  admin: 'accent',
}

export const statusLabel: Record<string, string> = {
  active: '启用',
  disabled: '停用',
}

export const statusTone: Record<string, ChipTone> = {
  active: 'success',
  disabled: 'neutral',
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
