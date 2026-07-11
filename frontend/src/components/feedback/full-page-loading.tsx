import { LoaderCircle } from 'lucide-react'

export function FullPageLoading({ message = '正在恢复登录状态…' }: { message?: string }) {
  return (
    <main className="grid min-h-screen place-items-center bg-[var(--color-background)] p-[var(--space-4)]">
      <div className="flex items-center gap-[var(--space-3)] text-[var(--color-text-secondary)]" role="status">
        <LoaderCircle aria-hidden="true" className="size-5 animate-spin motion-reduce:animate-none" />
        <span>{message}</span>
      </div>
    </main>
  )
}
