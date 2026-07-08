import { Warning } from '@phosphor-icons/react'
import { Button } from '../ui/button'

export function FullPageError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <main className="grid min-h-screen place-items-center bg-[var(--color-background)] p-[var(--space-4)]">
      <section className="max-w-lg rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-[var(--space-8)] text-center shadow-sm" role="alert">
        <Warning size={16} weight="duotone" className="mx-auto size-8 text-[var(--color-danger)]" aria-hidden="true" />
        <h1 className="mt-[var(--space-4)] text-2xl font-semibold">无法恢复登录状态</h1>
        <p className="mt-[var(--space-2)] text-sm leading-[22px] text-[var(--color-text-secondary)]">{message}</p>
        <div className="mt-[var(--space-6)]"><Button onClick={onRetry}>重新尝试</Button></div>
      </section>
    </main>
  )
}
