import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  getConversation,
  sendChat,
  submitFeedback,
  type FeedbackRating,
  type SendChatInput,
} from '../../api/chat'

export const conversationKeys = {
  all: ['conversations'] as const,
  detail: (id: string) => [...conversationKeys.all, 'detail', id] as const,
}

export function useConversationQuery(id: string) {
  return useQuery({
    queryKey: conversationKeys.detail(id),
    queryFn: ({ signal }) => getConversation(id, signal),
    enabled: Boolean(id),
  })
}

export function useSendChatMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: SendChatInput) => sendChat(input),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({
        queryKey: conversationKeys.detail(result.conversationId),
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
