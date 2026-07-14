import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  deleteDocument,
  getDocumentDetail,
  getDocumentStatus,
  listDocuments,
  reindexDocument,
  updateDocument,
  uploadDocument,
  type DocumentStatus,
  type UpdateDocumentInput,
} from '../../api/knowledge-bases'
import { knowledgeBaseKeys } from '../knowledge-bases/queries'

export const documentKeys = {
  all: (knowledgeBaseId: string) => ['documents', knowledgeBaseId] as const,
  list: (knowledgeBaseId: string, page: number, pageSize: number) =>
    [...documentKeys.all(knowledgeBaseId), 'list', { page, pageSize }] as const,
  status: (documentId: string) => ['document-status', documentId] as const,
  detail: (documentId: string) => ['document-detail', documentId] as const,
}

export function documentStatusPollingInterval(
  data: DocumentStatus | undefined,
  dataUpdatedAt: number,
  now = Date.now(),
): number | false {
  if (data && data.indexStatus !== 'pending' && data.indexStatus !== 'indexing') {
    return false
  }
  const age = dataUpdatedAt ? now - dataUpdatedAt : 0
  return age >= 30_000 ? 5_000 : 2_000
}

export function useDocumentsQuery(knowledgeBaseId: string, page: number, pageSize: number) {
  return useQuery({
    queryKey: documentKeys.list(knowledgeBaseId, page, pageSize),
    queryFn: ({ signal }) => listDocuments(knowledgeBaseId, page, pageSize, signal),
    enabled: Boolean(knowledgeBaseId),
  })
}

export function useDocumentStatusQuery(documentId: string, initialStatus: string) {
  const active = initialStatus === 'pending' || initialStatus === 'indexing'
  return useQuery({
    queryKey: documentKeys.status(documentId),
    queryFn: ({ signal }) => getDocumentStatus(documentId, signal),
    enabled: active,
    refetchInterval: (query) =>
      documentStatusPollingInterval(query.state.data, query.state.dataUpdatedAt),
    refetchIntervalInBackground: false,
  })
}

export function useDocumentDetailQuery(documentId: string, enabled = true) {
  return useQuery({
    queryKey: documentKeys.detail(documentId),
    queryFn: ({ signal }) => getDocumentDetail(documentId, signal),
    enabled: Boolean(documentId) && enabled,
  })
}

export function useUploadDocumentMutation(knowledgeBaseId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ file, title }: { file: File; title?: string }) =>
      uploadDocument(knowledgeBaseId, file, title),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: documentKeys.all(knowledgeBaseId) }),
        queryClient.invalidateQueries({
          queryKey: knowledgeBaseKeys.detail(knowledgeBaseId),
        }),
        queryClient.invalidateQueries({ queryKey: knowledgeBaseKeys.list() }),
      ])
    },
  })
}

export function useReindexDocumentMutation(knowledgeBaseId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (documentId: string) => reindexDocument(documentId),
    onSuccess: async (_, documentId) => {
      await queryClient.invalidateQueries({ queryKey: documentKeys.status(documentId) })
      await queryClient.invalidateQueries({ queryKey: documentKeys.detail(documentId) })
      await queryClient.invalidateQueries({
        queryKey: documentKeys.all(knowledgeBaseId),
      })
    },
  })
}

export function useUpdateDocumentMutation(knowledgeBaseId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ documentId, input }: { documentId: string; input: UpdateDocumentInput }) =>
      updateDocument(documentId, input),
    onSuccess: async (detail) => {
      queryClient.setQueryData(documentKeys.detail(detail.id), detail)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: documentKeys.all(knowledgeBaseId) }),
        queryClient.invalidateQueries({ queryKey: knowledgeBaseKeys.detail(knowledgeBaseId) }),
        queryClient.invalidateQueries({ queryKey: knowledgeBaseKeys.list() }),
      ])
    },
  })
}

export function useDeleteDocumentMutation(knowledgeBaseId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (documentId: string) => deleteDocument(documentId),
    onSuccess: async (_, documentId) => {
      queryClient.removeQueries({ queryKey: documentKeys.detail(documentId) })
      queryClient.removeQueries({ queryKey: documentKeys.status(documentId) })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: documentKeys.all(knowledgeBaseId) }),
        queryClient.invalidateQueries({ queryKey: knowledgeBaseKeys.detail(knowledgeBaseId) }),
        queryClient.invalidateQueries({ queryKey: knowledgeBaseKeys.list() }),
      ])
    },
  })
}
