import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { deleteMemory, listMemories } from '../../api/memory'

export const memoryKeys = {
  all: ['memory'] as const,
  lists: () => [...memoryKeys.all, 'list'] as const,
  list: (page: number, pageSize: number, memoryType: string, keyword: string) =>
    [...memoryKeys.lists(), { page, pageSize, memoryType, keyword }] as const,
}

export function useMemoriesQuery(
  page: number,
  pageSize: number,
  memoryType: string,
  keyword: string,
) {
  return useQuery({
    queryKey: memoryKeys.list(page, pageSize, memoryType, keyword),
    queryFn: ({ signal }) =>
      listMemories(page, pageSize, memoryType || undefined, keyword || undefined, signal),
  })
}

export function useDeleteMemoryMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (memoryId: string) => deleteMemory(memoryId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: memoryKeys.lists() })
    },
  })
}
