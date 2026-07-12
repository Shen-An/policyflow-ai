import type { ReactNode } from 'react'
import { cn } from '../../lib/cn'

export type AlertTone = 'danger' | 'warning' | 'success' | 'info'

const toneContainer: Record<AlertTone, string> = {
  danger: 'border-[var(--color-danger-200)] bg-[var(--color-danger-50)]',
  warning: 'border-[var(--color-warning-200)] bg-[var(--color-warning-50)]',
  success: 'border-[var(--color-success-200)] bg-[var(--color-success-50)]',
  info: 'border-[var(--color-primary-200)] bg-[var(--color-primary-50)]',
}

const toneTitle: Record<AlertTone, string> = {
  danger: 'text-[var(--color-danger)]',
  warning: 'text-[var(--color-warning)]',
  success: 'text-[var(--color-success-700)]',
  info: 'text-[var(--color-primary-700)]',
}

/**
 * Presentational alert banner. Title is tone-coloured; body text defaults to
 * secondary so messages stay readable on tinted backgrounds.
 */
export function Alert({
  tone = 'info',
  title,
  children,
  action,
  className,
  role,
  tabIndex,
}: {
  tone?: AlertTone
  title?: ReactNode
  children?: ReactNode
  action?: ReactNode
  className?: string
  /** Defaults to "alert" for danger, "status" otherwise. */
  role?: 'alert' | 'status'
  tabIndex?: number
}) {
  return (
    <div
      role={role ?? (tone === 'danger' ? 'alert' : 'status')}
      tabIndex={tabIndex}
      className={cn('rounded-md border p-[var(--space-4)]', toneContainer[tone], className)}
    >
      {title ? <p className={cn('text-sm font-semibold', toneTitle[tone])}>{title}</p> : null}
      {children ? (
        <div className={cn('text-sm text-[var(--color-text-secondary)]', title ? 'mt-[var(--space-1)]' : undefined)}>
          {children}
        </div>
      ) : null}
      {action ? <div className="mt-[var(--space-3)]">{action}</div> : null}
    </div>
  )
}
