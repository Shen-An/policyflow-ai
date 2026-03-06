import { Activity, CheckCircle2 } from 'lucide-react'
import { Button } from '../components/ui/button'

export function FoundationPage() {
  return (
    <main className="min-h-screen bg-[var(--color-background)] px-[var(--space-4)] py-[var(--space-12)] text-[var(--color-text-primary)]">
      <section className="mx-auto max-w-3xl rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-[var(--space-8)] shadow-sm">
        <div className="flex items-center gap-[var(--space-3)] text-[var(--color-success)]">
          <CheckCircle2 aria-hidden="true" className="size-6" />
          <span className="text-sm font-semibold">F0 工程基础设施已加载</span>
        </div>
        <h1 className="mt-[var(--space-4)] text-2xl font-semibold leading-8">PolicyFlow AI</h1>
        <p className="mt-[var(--space-2)] max-w-2xl text-sm leading-[22px] text-[var(--color-text-secondary)]">
          当前仅提供前端工程骨架、统一 API 基础设施与质量门禁；业务功能将在后续 Phase 按依赖启用。
        </p>
        <div className="mt-[var(--space-6)]">
          <Button asChild>
            <a href="/health">
              <Activity aria-hidden="true" className="size-4" />
              检查后端健康状态
            </a>
          </Button>
        </div>
      </section>
    </main>
  )
}
