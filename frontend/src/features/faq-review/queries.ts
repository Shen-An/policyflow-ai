import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { selfHandledMutation } from '../../app/global-feedback'
import { approveFAQ, listFAQDrafts, rejectFAQ } from '../../api/faq'

export const faqKeys = {
  all: ['faq-drafts'] as const,
  list: (knowledgeBaseId: string, status: string) =>
    [...faqKeys.all, 'list', { knowledgeBaseId, status }] as const,
}

export function useFAQDraftsQuery(knowledgeBaseId: string, status: string) {
  return useQuery({
    queryKey: faqKeys.list(knowledgeBaseId, status),
    queryFn: ({ signal }) =>
      listFAQDrafts(knowledgeBaseId || undefined, status || undefined, signal),
  })
}

export function useApproveFAQMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    meta: selfHandledMutation,
    mutationFn: approveFAQ,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: faqKeys.all })
    },
  })
}

export function useRejectFAQMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    meta: selfHandledMutation,
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      rejectFAQ(id, reason),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: faqKeys.all })
    },
  })
}
