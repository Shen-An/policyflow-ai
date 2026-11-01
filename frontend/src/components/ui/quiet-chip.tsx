import type { ReactNode } from 'react'

import type { ChipTone } from './chip-tone'

export type { ChipTone }

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
