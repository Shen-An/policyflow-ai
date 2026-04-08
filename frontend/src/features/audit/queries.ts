import { useQuery } from '@tanstack/react-query'
import { getAuditLog, listAuditLogs } from '../../api/audit'

export const auditKeys = {
  all: ['audit-logs'] as const,
  list: (filters: Record<string, string | number>) =>
    [...auditKeys.all, 'list', filters] as const,
  detail: (id: string) => [...auditKeys.all, 'detail', id] as const,
}

export function useAuditLogsQuery(filters: Parameters<typeof listAuditLogs>[0]) {
  return useQuery({
    queryKey: auditKeys.list(filters),
    queryFn: ({ signal }) => listAuditLogs(filters, signal),
  })
}

export function useAuditLogQuery(id: string) {
  return useQuery({
    queryKey: auditKeys.detail(id),
    queryFn: ({ signal }) => getAuditLog(id, signal),
    enabled: Boolean(id),
  })
}
