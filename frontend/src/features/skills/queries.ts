import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { listSkills, runSkill, setSkillEnabled } from '../../api/skills'
import { getToolLog, listToolLogs, listTools } from '../../api/tools'

export const skillKeys = {
  all: ['skills'] as const,
}

export const toolLogKeys = {
  all: ['tool-call-logs'] as const,
  list: (filters: Record<string, string | number>) =>
    [...toolLogKeys.all, 'list', filters] as const,
  detail: (id: string) => [...toolLogKeys.all, 'detail', id] as const,
  tools: ['tools'] as const,
}

export function useSkillsQuery() {
  return useQuery({
    queryKey: skillKeys.all,
    queryFn: ({ signal }) => listSkills(signal),
  })
}

export function useSetSkillEnabledMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      setSkillEnabled(name, enabled),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: skillKeys.all })
    },
  })
}

export function useRunSkillMutation() {
  return useMutation({
    mutationFn: ({ name, input }: { name: string; input: Record<string, unknown> }) =>
      runSkill(name, input),
  })
}

export function useToolsQuery() {
  return useQuery({
    queryKey: toolLogKeys.tools,
    queryFn: ({ signal }) => listTools(signal),
  })
}

export function useToolLogsQuery(filters: Parameters<typeof listToolLogs>[0]) {
  return useQuery({
    queryKey: toolLogKeys.list(filters),
    queryFn: ({ signal }) => listToolLogs(filters, signal),
  })
}

export function useToolLogQuery(id: string) {
  return useQuery({
    queryKey: toolLogKeys.detail(id),
    queryFn: ({ signal }) => getToolLog(id, signal),
    enabled: Boolean(id),
  })
}
