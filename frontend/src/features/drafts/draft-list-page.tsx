import * as Dialog from '@radix-ui/react-dialog'
import { FileEdit, Plus, X } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import type { DraftType } from '../../api/drafts'
import { Button } from '../../components/ui/button'
import { useCreateDraftMutation, useDraftsQuery } from './queries'

const draftTypes: Array<{ value: DraftType; label: string }> = [
  { value: 'email', label: '邮件' },
  { value: 'checklist', label: '清单' },
  { value: 'application', label: '申请' },
  { value: 'faq', label: 'FAQ' },
  { value: 'help_request', label: '求助' },
  { value: 'summary', label: '摘要' },
]

function positiveInt(value: string | null, fallback: number): number {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback
}

export function DraftListPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [createOpen, setCreateOpen] = useState(false)
  const page = positiveInt(searchParams.get('page'), 1)
  const pageSize = Math.min(positiveInt(searchParams.get('page_size'), 20), 100)
  const status = searchParams.get('status') ?? ''
  const draftType = searchParams.get('draft_type') ?? ''
  const query = useDraftsQuery(page, pageSize, status, draftType)
  const totalPages = Math.max(1, Math.ceil((query.data?.total ?? 0) / pageSize))

  function setFilter(key: 'status' | 'draft_type', value: string) {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(key, value)
    else next.delete(key)
    next.set('page', '1')
    setSearchParams(next, { replace: true })
  }

  function goToPage(nextPage: number) {
    const next = new URLSearchParams(searchParams)
    next.set('page', String(nextPage))
    setSearchParams(next)
  }

  return (
    <section>
      <div className="flex flex-col gap-[var(--space-4)] sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-2xl font-semibold">我的草稿</h2>
          <p className="mt-[var(--space-1)] text-sm text-[var(--color-text-secondary)]">
            草稿仅在确认后变为只读，不会自动提交到外部系统。
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus aria-hidden="true" className="size-4" />创建草稿
        </Button>
      </div>

      <div className="mt-[var(--space-6)] flex flex-wrap gap-[var(--space-3)]">
        <label className="text-sm font-semibold">
          状态
          <select
            value={status}
            onChange={(event) => setFilter('status', event.target.value)}
            className="ml-[var(--space-2)] min-h-10 rounded-md border border-[var(--color-border)] px-[var(--space-3)] font-normal"
          >
            <option value="">全部</option>
            <option value="draft">草稿</option>
            <option value="confirmed">已确认</option>
            <option value="discarded">已丢弃</option>
            <option value="exported">已导出</option>
          </select>
        </label>
        <label className="text-sm font-semibold">
          类型
          <select
            value={draftType}
            onChange={(event) => setFilter('draft_type', event.target.value)}
            className="ml-[var(--space-2)] min-h-10 rounded-md border border-[var(--color-border)] px-[var(--space-3)] font-normal"
          >
            <option value="">全部</option>
            {draftTypes.map((type) => (
              <option key={type.value} value={type.value}>{type.label}</option>
            ))}
          </select>
        </label>
      </div>

      {query.isPending ? (
        <div role="status" className="mt-[var(--space-6)] min-h-48 p-[var(--space-6)]">
          正在加载草稿…
        </div>
      ) : query.isError ? (
        <div role="alert" className="mt-[var(--space-6)] rounded-xl border border-red-200 bg-red-50 p-[var(--space-5)]">
          <h3 className="font-semibold">草稿列表加载失败</h3>
          <p className="mt-[var(--space-1)] text-sm">{query.error.message}</p>
          <Button className="mt-[var(--space-3)]" onClick={() => void query.refetch()}>
            重新加载
          </Button>
        </div>
      ) : query.data.items.length === 0 ? (
        <div className="mt-[var(--space-6)] grid min-h-64 place-items-center rounded-xl border border-dashed border-[var(--color-border)] bg-white text-center">
          <div>
            <FileEdit aria-hidden="true" className="mx-auto size-8 text-[var(--color-text-secondary)]" />
            <h3 className="mt-[var(--space-3)] font-semibold">还没有符合条件的草稿</h3>
            <p className="mt-[var(--space-1)] text-sm text-[var(--color-text-secondary)]">
              可以创建一份草稿，或调整筛选条件。
            </p>
          </div>
        </div>
      ) : (
        <>
          <div className="mt-[var(--space-6)] grid gap-[var(--space-4)] md:grid-cols-2 xl:grid-cols-3">
            {query.data.items.map((draft) => (
              <article key={draft.id} className="rounded-xl border border-[var(--color-border)] bg-white p-[var(--space-5)] shadow-sm">
                <div className="flex items-start justify-between gap-[var(--space-3)]">
                  <div>
                    <h3 className="font-semibold">{draft.title}</h3>
                    <p className="mt-[var(--space-1)] text-xs text-[var(--color-text-secondary)]">
                      {draft.draftType}
                    </p>
                  </div>
                  <span className="rounded-full bg-slate-100 px-[var(--space-2)] py-[var(--space-1)] text-xs">
                    {draft.status}
                  </span>
                </div>
                <p className="mt-[var(--space-3)] line-clamp-3 min-h-16 whitespace-pre-wrap text-sm text-[var(--color-text-secondary)]">
                  {draft.content}
                </p>
                <Button asChild className="mt-[var(--space-4)] w-full">
                  <Link to={`/drafts/${draft.id}`}>查看草稿</Link>
                </Button>
              </article>
            ))}
          </div>
          <div className="mt-[var(--space-6)] flex items-center justify-between">
            <p className="text-sm text-[var(--color-text-secondary)]">
              共 {query.data.total} 份，第 {page} / {totalPages} 页
            </p>
            <div className="flex gap-[var(--space-2)]">
              <Button
                className="bg-white text-[var(--color-text-primary)] ring-1 ring-[var(--color-border)] hover:bg-slate-50"
                disabled={page <= 1}
                onClick={() => goToPage(page - 1)}
              >
                上一页
              </Button>
              <Button
                className="bg-white text-[var(--color-text-primary)] ring-1 ring-[var(--color-border)] hover:bg-slate-50"
                disabled={page >= totalPages}
                onClick={() => goToPage(page + 1)}
              >
                下一页
              </Button>
            </div>
          </div>
        </>
      )}

      <CreateDraftDialog open={createOpen} onOpenChange={setCreateOpen} />
    </section>
  )
}

function CreateDraftDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const navigate = useNavigate()
  const mutation = useCreateDraftMutation()
  const [draftType, setDraftType] = useState<DraftType>('email')
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [sourceQuestion, setSourceQuestion] = useState('')

  function changeOpen(next: boolean) {
    if (!next && !mutation.isPending) {
      setDraftType('email')
      setTitle('')
      setContent('')
      setSourceQuestion('')
      mutation.reset()
    }
    onOpenChange(next)
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    const created = await mutation.mutateAsync({
      draftType,
      title: title.trim(),
      content: content.trim(),
      sourceQuestion: sourceQuestion.trim(),
    })
    changeOpen(false)
    navigate(`/drafts/${created.id}`)
  }

  return (
    <Dialog.Root open={open} onOpenChange={changeOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-slate-950/50" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[calc(100%-32px)] max-w-xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl bg-white p-[var(--space-6)] shadow-xl">
          <Dialog.Title className="text-lg font-semibold">创建草稿</Dialog.Title>
          <Dialog.Description className="mt-[var(--space-1)] text-sm text-[var(--color-text-secondary)]">
            创建后可继续编辑、确认或导出。
          </Dialog.Description>
          <Dialog.Close aria-label="关闭对话框" className="absolute right-4 top-4 inline-flex size-9 items-center justify-center rounded-md hover:bg-slate-100">
            <X aria-hidden="true" className="size-5" />
          </Dialog.Close>
          {mutation.isError ? (
            <p role="alert" className="mt-[var(--space-4)] rounded-md bg-red-50 p-[var(--space-3)] text-sm text-[var(--color-danger)]">
              {mutation.error.message}
            </p>
          ) : null}
          <form className="mt-[var(--space-5)] space-y-[var(--space-4)]" onSubmit={submit}>
            <label className="block text-sm font-semibold">
              类型
              <select value={draftType} onChange={(event) => setDraftType(event.target.value as DraftType)} className="mt-2 min-h-10 w-full rounded-md border border-[var(--color-border)] px-3 font-normal">
                {draftTypes.map((type) => <option key={type.value} value={type.value}>{type.label}</option>)}
              </select>
            </label>
            <label className="block text-sm font-semibold">
              标题
              <input required maxLength={255} value={title} onChange={(event) => setTitle(event.target.value)} className="mt-2 min-h-10 w-full rounded-md border border-[var(--color-border)] px-3 font-normal" />
            </label>
            <label className="block text-sm font-semibold">
              正文
              <textarea required rows={7} value={content} onChange={(event) => setContent(event.target.value)} className="mt-2 w-full rounded-md border border-[var(--color-border)] p-3 font-normal" />
            </label>
            <label className="block text-sm font-semibold">
              来源问题
              <input value={sourceQuestion} onChange={(event) => setSourceQuestion(event.target.value)} className="mt-2 min-h-10 w-full rounded-md border border-[var(--color-border)] px-3 font-normal" />
            </label>
            <div className="flex justify-end gap-[var(--space-3)]">
              <Button className="bg-white text-[var(--color-text-primary)] ring-1 ring-[var(--color-border)] hover:bg-slate-50" onClick={() => changeOpen(false)} disabled={mutation.isPending}>
                取消
              </Button>
              <Button type="submit" disabled={mutation.isPending || !title.trim() || !content.trim()}>
                {mutation.isPending ? '正在创建…' : '创建草稿'}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
