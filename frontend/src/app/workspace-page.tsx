import { MessageSquareText } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Button } from '../components/ui/button'

export function WorkspacePage() {
  return (
    <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-[var(--space-8)] shadow-sm">
      <MessageSquareText aria-hidden="true" className="size-8 text-[var(--color-primary)]" />
      <h2 className="mt-[var(--space-4)] text-lg font-semibold leading-7">制度问答已开放</h2>
      <p className="mt-[var(--space-2)] max-w-2xl text-sm leading-[22px] text-[var(--color-text-secondary)]">从授权知识库检索制度依据，查看引用、提交反馈，并继续处理个人草稿。</p>
      <Button asChild className="mt-[var(--space-4)]">
        <Link to="/chat">开始提问</Link>
      </Button>
    </section>
  )
}
