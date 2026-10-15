import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createGrant,
  getHealth,
  getQuota,
  getRunTimeline,
  listCapacity,
  listGrants,
  listReconciliation,
  listRoles,
  listRuns,
  recoverRun,
  resolveReconciliation,
  retryReconciliation,
  revokeGrant,
} from '../../api/admin'

export const adminKeys = {
  all: ['admin'] as const,
  roles: () => [...adminKeys.all, 'roles'] as const,
  grants: (filters: Record<string, string | number>) =>
    [...adminKeys.all, 'grants', filters] as const,
  quota: () => [...adminKeys.all, 'quota'] as const,
  health: () => [...adminKeys.all, 'health'] as const,
  runs: (filters: Record<string, string | number>) =>
    [...adminKeys.all, 'runs', filters] as const,
  runAudit: (runId: string) => [...adminKeys.all, 'run-audit', runId] as const,
  reconciliation: (filters: Record<string, string | number>) =>
    [...adminKeys.all, 'reconciliation', filters] as const,
  capacity: (filters: Record<string, string | number>) =>
    [...adminKeys.all, 'capacity', filters] as const,
}

export function useRolesQuery() {
  return useQuery({ queryKey: adminKeys.roles(), queryFn: ({ signal }) => listRoles(signal) })
}

export function useGrantsQuery(filters: { limit: number; offset: number; memberId?: string }) {
  return useQuery({
    queryKey: adminKeys.grants(filters),
    queryFn: ({ signal }) => listGrants(filters, signal),
  })
}

export function useQuotaQuery() {
  return useQuery({ queryKey: adminKeys.quota(), queryFn: ({ signal }) => getQuota(signal) })
}

export function useHealthQuery() {
  return useQuery({ queryKey: adminKeys.health(), queryFn: ({ signal }) => getHealth(signal) })
}

export function useRunsQuery(filters: {
  limit: number
  offset: number
  status?: string
  kind?: string
}) {
  return useQuery({
    queryKey: adminKeys.runs(filters),
    queryFn: ({ signal }) => listRuns(filters, signal),
  })
}

export function useRunAuditQuery(runId: string) {
  return useQuery({
    queryKey: adminKeys.runAudit(runId),
    queryFn: ({ signal }) => getRunTimeline(runId, signal),
    enabled: Boolean(runId),
  })
}

export function useReconciliationQuery(filters: {
  limit: number
  offset: number
  state?: string
  issueKind?: string
}) {
  return useQuery({
    queryKey: adminKeys.reconciliation(filters),
    queryFn: ({ signal }) => listReconciliation(filters, signal),
  })
}

export function useCapacityQuery(filters: { limit: number; offset: number }) {
  return useQuery({
    queryKey: adminKeys.capacity(filters),
    queryFn: ({ signal }) => listCapacity(filters, signal),
  })
}

export function useCreateGrantMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Parameters<typeof createGrant>[0]) => createGrant(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: [...adminKeys.all, 'grants'] })
      void queryClient.invalidateQueries({ queryKey: adminKeys.roles() })
    },
  })
}

export function useRevokeGrantMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ grantId, reason }: { grantId: string; reason?: string }) =>
      revokeGrant(grantId, reason),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: [...adminKeys.all, 'grants'] })
      void queryClient.invalidateQueries({ queryKey: adminKeys.roles() })
    },
  })
}

export function useRecoverRunMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ runId, extendAttempts }: { runId: string; extendAttempts: boolean }) =>
      recoverRun(runId, extendAttempts),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: [...adminKeys.all, 'runs'] })
      void queryClient.invalidateQueries({ queryKey: [...adminKeys.all, 'run-audit'] })
      void queryClient.invalidateQueries({ queryKey: adminKeys.health() })
    },
  })
}

export function useRetryReconciliationMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ issueId, reason }: { issueId: string; reason?: string }) =>
      retryReconciliation(issueId, reason),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: [...adminKeys.all, 'reconciliation'] })
      void queryClient.invalidateQueries({ queryKey: adminKeys.health() })
    },
  })
}

export function useResolveReconciliationMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ issueId, resolution }: { issueId: string; resolution: string }) =>
      resolveReconciliation(issueId, resolution),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: [...adminKeys.all, 'reconciliation'] })
      void queryClient.invalidateQueries({ queryKey: adminKeys.health() })
    },
  })
}
