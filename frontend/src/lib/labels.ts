/** Shared Chinese labels for API status / role codes shown in the UI. */

export const roleLabel: Record<string, string> = {
  employee: '普通员工',
  kb_admin: '知识库管理员',
  sys_admin: '系统管理员',
}

export function formatRoles(roles: string[] | undefined | null): string {
  if (!roles?.length) return '未分配角色'
  return roles.map((role) => roleLabel[role] ?? role).join(' · ')
}

export const userStatusLabel: Record<string, string> = {
  active: '启用',
  disabled: '停用',
  inactive: '停用',
}

export const documentIndexStatusLabel: Record<string, string> = {
  ready: '已就绪',
  indexed: '已索引',
  pending: '排队中',
  processing: '处理中',
  indexing: '索引中',
  failed: '失败',
  error: '失败',
}

export const toolLogStatusLabel: Record<string, string> = {
  success: '成功',
  failed: '失败',
  error: '失败',
  running: '运行中',
  pending: '排队中',
}

export const skillRiskLabel: Record<string, string> = {
  low: '低',
  medium: '中',
  high: '高',
  critical: '严重',
}
