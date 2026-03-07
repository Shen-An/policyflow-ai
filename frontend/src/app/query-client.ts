import { QueryClient } from '@tanstack/react-query'
import { AppError } from '../api/errors'

function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= 2 || !(error instanceof AppError)) return false
  return error.retryable && (error.kind === 'network' || error.kind === 'server')
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: shouldRetry, refetchOnWindowFocus: false, staleTime: 30_000 },
    mutations: { retry: false },
  },
})
