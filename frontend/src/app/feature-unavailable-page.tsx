import { HardHat } from '@phosphor-icons/react'
export function FeatureUnavailablePage({ title }: { title: string }) {
  return (
    <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-[var(--space-8)] shadow-sm" role="status">
      <HardHat size={16} weight="duotone" className="size-8 text-[var(--color-warning)]" aria-hidden="true" />
      <h2 className="mt-[var(--space-4)] text-lg font-semibold">{title}尚未开放</h2>
      <p className="mt-[var(--space-2)] text-sm leading-[22px] text-[var(--color-text-secondary)]">该模块的后端接口已就绪，但前端功能将在对应 Phase 验收后开放。当前不会发送业务请求。</p>
    </section>
  )
}
