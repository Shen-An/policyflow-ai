import { MutationCache, QueryClient } from '@tanstack/react-query'
import { AppError } from '../api/errors'
import { notifyGlobalError } from './global-feedback'

function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= 2 || !(error instanceof AppError)) return false
  return error.retryable && (error.kind === 'network' || error.kind === 'server')
}

function describeMutationError(error: unknown): string {
  if (error instanceof AppError) {
    if (error.kind === 'auth') return '登录已过期，请重新登录'
    if (error.kind === 'permission') return '没有执行该操作的权限'
    return error.message || '操作失败，请稍后重试'
  }
  if (error instanceof Error && error.message) return error.message
  return '操作失败，请稍后重试'
}

export const queryClient = new QueryClient({
  // 全局兜底：mutation 失败必须让用户看见。页面若自带 onError（内联 Alert 等）则跳过，避免重复提示。
  mutationCache: new MutationCache({
    onError: (error, _variables, _context, mutation) => {
      if (mutation.options.onError) return
      if (mutation.options.meta?.suppressGlobalError) return
      notifyGlobalError(describeMutationError(error))
    },
  }),
  defaultOptions: {
    queries: { retry: shouldRetry, refetchOnWindowFocus: false, staleTime: 30_000 },
    mutations: { retry: false },
  },
})
