import { ShieldSlash } from '@phosphor-icons/react'
import { cn } from '../../lib/cn'

export function PermissionDeniedNotice({ compact = false }: { compact?: boolean }) {
  return (
    <section
      className={cn(
        'rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text-primary)]',
        compact ? 'p-[var(--space-4)]' : 'mx-auto max-w-xl p-[var(--space-8)] text-center',
      )}
      role="alert"
    >
      <ShieldSlash size={16} weight="duotone" aria-hidden="true" />
      <h1 className={cn('font-semibold', compact ? 'mt-[var(--space-2)] text-base' : 'mt-[var(--space-4)] text-2xl')}>无访问权限</h1>
      <p className="mt-[var(--space-2)] text-sm leading-[22px] text-[var(--color-text-secondary)]">
        你没有访问此功能的权限。若认为这是错误，请联系系统管理员。
      </p>
    </section>
  )
}
