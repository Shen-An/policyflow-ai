import type { ReactNode } from 'react'

export type ChipTone = 'neutral' | 'active' | 'success' | 'warning' | 'error' | 'accent'

export function QuietChip({
  children,
  tone = 'neutral',
  title,
}: {
  children: ReactNode
  tone?: ChipTone
  title?: string
}) {
  return (
    <span className={`chat-chip chat-chip--${tone}`} title={title}>
      {children}
    </span>
  )
}

/** Map common status strings to quiet chip tones (success/active/error/neutral). */
export function statusTone(status: string): ChipTone {
  const value = status.toLowerCase()
  if (
    value === 'running' ||
    value === 'processing' ||
    value === 'pending' ||
    value === 'indexing' ||
    value === 'exported'
  ) {
    return 'active'
  }
  if (
    value === 'success' ||
    value === 'passed' ||
    value === 'ready' ||
    value === 'indexed' ||
    value === 'active' ||
    value === 'enabled' ||
    value === 'approved' ||
    value === 'confirmed' ||
    value === 'healthy' ||
    value === 'suggested'
  ) {
    return 'success'
  }
  if (value === 'warning' || value === 'stale') return 'warning'
  if (
    value === 'error' ||
    value === 'failed' ||
    value === 'unhealthy' ||
    value === 'rejected' ||
    value === 'discarded' ||
    value === 'disabled'
  ) {
    return 'error'
  }
  return 'neutral'
}
