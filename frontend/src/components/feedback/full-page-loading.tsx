import { CircleNotch } from '@phosphor-icons/react'
export function FullPageLoading({ message = '正在恢复登录状态…' }: { message?: string }) {
  return (
    <main className="grid min-h-screen place-items-center bg-[var(--color-background)] p-[var(--space-4)]">
      <div className="flex items-center gap-[var(--space-3)] text-[var(--color-text-secondary)]" role="status">
        <CircleNotch size={16} weight="duotone" className="animate-spin size-5 motion-reduce:animate-none" aria-hidden="true" />
        <span>{message}</span>
      </div>
    </main>
  )
}
