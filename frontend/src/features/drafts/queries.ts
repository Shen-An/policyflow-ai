import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  confirmDraft,
  createDraft,
  discardDraft,
  exportDraft,
  getDraft,
  listDrafts,
  updateDraft,
  type CreateDraftInput,
} from '../../api/drafts'

export const draftKeys = {
  all: ['drafts'] as const,
  lists: () => [...draftKeys.all, 'list'] as const,
  list: (page: number, pageSize: number, status: string, draftType: string) =>
    [...draftKeys.lists(), { page, pageSize, status, draftType }] as const,
  detail: (id: string) => [...draftKeys.all, 'detail', id] as const,
}

export function useDraftsQuery(
  page: number,
  pageSize: number,
  status: string,
  draftType: string,
) {
  return useQuery({
    queryKey: draftKeys.list(page, pageSize, status, draftType),
    queryFn: ({ signal }) =>
      listDrafts(page, pageSize, status || undefined, draftType || undefined, signal),
  })
}

export function useDraftQuery(id: string) {
  return useQuery({
    queryKey: draftKeys.detail(id),
    queryFn: ({ signal }) => getDraft(id, signal),
    enabled: Boolean(id),
  })
}

export function useCreateDraftMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: CreateDraftInput) => createDraft(input),
    onSuccess: async (draft) => {
      queryClient.setQueryData(draftKeys.detail(draft.id), draft)
      await queryClient.invalidateQueries({ queryKey: draftKeys.lists() })
    },
  })
}

export function useUpdateDraftMutation(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: { title: string; content: string }) => updateDraft(id, input),
    onSuccess: async (draft) => {
      queryClient.setQueryData(draftKeys.detail(id), draft)
      await queryClient.invalidateQueries({ queryKey: draftKeys.lists() })
    },
  })
}

function useDraftAction(id: string, action: typeof confirmDraft) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => action(id),
    onSuccess: async (draft) => {
      queryClient.setQueryData(draftKeys.detail(id), draft)
      await queryClient.invalidateQueries({ queryKey: draftKeys.lists() })
    },
  })
}

export const useConfirmDraftMutation = (id: string) =>
  useDraftAction(id, confirmDraft)
export const useDiscardDraftMutation = (id: string) =>
  useDraftAction(id, discardDraft)
export const useExportDraftMutation = (id: string) =>
  useMutation({ mutationFn: () => exportDraft(id) })
