import type { ReactNode } from 'react'
import { RefreshCw } from 'lucide-react'
import { cn } from '../../lib/cn'
import { Button } from '../ui/button'
import { Alert } from './alert'

/**
 * Shared list/section state views. Use these instead of rolling per-page
 * loading / empty / error markup so spacing, icons, and retry behaviour stay
 * consistent. `minH` lets a page match its container height (e.g. `min-h-48`
 * inside a nested panel vs `min-h-64` for a full list).
 */
export function LoadingState({
  message = '正在加载…',
  minH = 'min-h-64',
}: {
  message?: string
  minH?: string
}) {
  return (
    <div
      className={cn(
        'flex items-center justify-center gap-[var(--space-3)] text-sm text-[var(--color-text-secondary)]',
        minH,
      )}
      role="status"
    >
      <RefreshCw aria-hidden="true" className="size-5 animate-spin motion-reduce:animate-none" />
      <span>{message}</span>
    </div>
  )
}

export function EmptyState({
  icon,
  title,
  hint,
  minH = 'min-h-64',
}: {
  icon?: ReactNode
  title: string
  hint?: ReactNode
  minH?: string
}) {
  return (
    <div className={cn('grid place-items-center p-[var(--space-8)] text-center', minH)}>
      <div>
        {icon ? <div className="flex justify-center text-[var(--color-text-secondary)]">{icon}</div> : null}
        <h3 className={cn('font-semibold', icon ? 'mt-[var(--space-3)]' : undefined)}>{title}</h3>
        {hint ? <p className="mt-[var(--space-1)] text-sm text-[var(--color-text-secondary)]">{hint}</p> : null}
      </div>
    </div>
  )
}

export function ErrorState({
  error,
  onRetry,
  title = '加载失败',
  minH = 'min-h-64',
  requestId,
}: {
  error: Error
  onRetry?: () => void
  title?: string
  minH?: string
  requestId?: string
}) {
  return (
    <div className={cn('grid place-items-center p-[var(--space-8)] text-center', minH)}>
      <div className="w-full max-w-md">
        <Alert tone="danger" title={title}>
          <p>{error.message}</p>
          {requestId ? (
            <p className="mt-[var(--space-1)] text-xs text-[var(--color-text-secondary)]">请求编号：{requestId}</p>
          ) : null}
        </Alert>
        {onRetry ? (
          <div className="mt-[var(--space-4)]">
            <Button onClick={onRetry}>重新加载</Button>
          </div>
        ) : null}
      </div>
    </div>
  )
}
