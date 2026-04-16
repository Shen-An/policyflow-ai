import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createKnowledgeBase,
  getCreateOptions,
  getKnowledgeBase,
  listKnowledgeBases,
  type CreateKnowledgeBaseInput,
} from '../../api/knowledge-bases'

export const knowledgeBaseKeys = {
  all: ['knowledge-bases'] as const,
  list: () => [...knowledgeBaseKeys.all, 'list'] as const,
  detail: (id: string) => [...knowledgeBaseKeys.all, 'detail', id] as const,
  createOptions: () => [...knowledgeBaseKeys.all, 'create-options'] as const,
}

export function useKnowledgeBasesQuery() {
  return useQuery({
    queryKey: knowledgeBaseKeys.list(),
    queryFn: ({ signal }) => listKnowledgeBases(signal),
  })
}

export function useKnowledgeBaseQuery(id: string) {
  return useQuery({
    queryKey: knowledgeBaseKeys.detail(id),
    queryFn: ({ signal }) => getKnowledgeBase(id, signal),
    enabled: Boolean(id),
  })
}

export function useCreateOptionsQuery(enabled: boolean) {
  return useQuery({
    queryKey: knowledgeBaseKeys.createOptions(),
    queryFn: ({ signal }) => getCreateOptions(signal),
    enabled,
    staleTime: 5 * 60_000,
  })
}

export function useCreateKnowledgeBaseMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: CreateKnowledgeBaseInput) => createKnowledgeBase(input),
    onSuccess: async (created) => {
      queryClient.setQueryData(knowledgeBaseKeys.detail(created.id), created)
      await queryClient.invalidateQueries({ queryKey: knowledgeBaseKeys.list() })
    },
  })
}
