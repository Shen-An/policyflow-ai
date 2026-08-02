import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  deleteConversation,
  getConversation,
  listConversations,
  renameConversation,
  sendChatStream,
  submitFeedback,
  type ChatStreamHandlers,
  type FeedbackRating,
  type SendChatInput,
} from '../../api/chat'

export const conversationKeys = {
  all: ['conversations'] as const,
  list: (page: number, pageSize: number, keyword = '') =>
    [...conversationKeys.all, 'list', { page, pageSize, keyword }] as const,
  detail: (id: string) => [...conversationKeys.all, 'detail', id] as const,
}

export function useConversationsQuery(page = 1, pageSize = 30, keyword = '') {
  return useQuery({
    queryKey: conversationKeys.list(page, pageSize, keyword),
    queryFn: ({ signal }) => listConversations(page, pageSize, keyword, signal),
  })
}

export function useConversationQuery(id: string) {
  return useQuery({
    queryKey: conversationKeys.detail(id),
    queryFn: ({ signal }) => getConversation(id, signal),
    enabled: Boolean(id),
  })
}

export type SendChatMutationInput = SendChatInput & {
  streamHandlers?: ChatStreamHandlers
}

// chat 页面对 mutateAsync 自行 try/catch 并提示，标记跳过全局错误弹层
const chatHandlesOwnError = { suppressGlobalError: true } as const

export function useSendChatMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    meta: chatHandlesOwnError,
    mutationFn: ({ streamHandlers, ...input }: SendChatMutationInput) =>
      sendChatStream(input, streamHandlers),
    onSuccess: async (result) => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: conversationKeys.detail(result.conversationId),
        }),
        queryClient.invalidateQueries({
          queryKey: conversationKeys.all,
        }),
      ])
    },
  })
}

export function useRenameConversationMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    meta: chatHandlesOwnError,
    mutationFn: ({ conversationId, title }: { conversationId: string; title: string }) =>
      renameConversation(conversationId, title),
    onSuccess: async (result) => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: conversationKeys.detail(result.id),
        }),
        queryClient.invalidateQueries({
          queryKey: conversationKeys.all,
        }),
      ])
    },
  })
}

export function useDeleteConversationMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    meta: chatHandlesOwnError,
    mutationFn: (conversationId: string) => deleteConversation(conversationId),
    onSuccess: async (_result, conversationId) => {
      queryClient.removeQueries({
        queryKey: conversationKeys.detail(conversationId),
      })
      await queryClient.invalidateQueries({
        queryKey: conversationKeys.all,
      })
    },
  })
}

export function useFeedbackMutation(queryLogId: string) {
  return useMutation({
    mutationFn: ({ rating, comment }: { rating: FeedbackRating; comment?: string }) =>
      submitFeedback(queryLogId, rating, comment),
  })
}
