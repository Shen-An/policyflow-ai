import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  checkMCPHealth,
  createMCPServer,
  listMCPServers,
  updateMCPServer,
  type MCPServerInput,
  type MCPServerUpdate,
} from '../../api/mcp'

export const mcpKeys = {
  all: ['mcp-servers'] as const,
}

export function useMCPServersQuery() {
  return useQuery({
    queryKey: mcpKeys.all,
    queryFn: ({ signal }) => listMCPServers(signal),
  })
}

export function useCreateMCPServerMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: MCPServerInput) => createMCPServer(input),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: mcpKeys.all })
    },
  })
}

export function useUpdateMCPServerMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: MCPServerUpdate }) =>
      updateMCPServer(id, input),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: mcpKeys.all })
    },
  })
}

export function useMCPHealthMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: checkMCPHealth,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: mcpKeys.all })
    },
  })
}
