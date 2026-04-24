import { ArrowLeft, Download, Save, ShieldCheck, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import {
  Link,
  useBeforeUnload,
  useBlocker,
  useNavigate,
  useParams,
} from 'react-router-dom'
import { Button } from '../../components/ui/button'
import { Alert } from '../../components/feedback/alert'
import { ErrorState, LoadingState } from '../../components/feedback/state-views'
import { downloadMarkdown } from './download'
import {
  useConfirmDraftMutation,
  useDiscardDraftMutation,
  useDraftQuery,
  useExportDraftMutation,
  useUpdateDraftMutation,
} from './queries'

export function DraftDetailPage() {
  const { draftId = '' } = useParams()
  return <DraftDetailScreen key={draftId} draftId={draftId} />
}

function DraftDetailScreen({ draftId }: { draftId: string }) {
  const navigate = useNavigate()
  const query = useDraftQuery(draftId)
  const update = useUpdateDraftMutation(draftId)
  const confirm = useConfirmDraftMutation(draftId)
  const discard = useDiscardDraftMutation(draftId)
  const exportMutation = useExportDraftMutation(draftId)
  const [titleOverride, setTitleOverride] = useState<string | null>(null)
  const [contentOverride, setContentOverride] = useState<string | null>(null)
  const title = titleOverride ?? query.data?.title ?? ''
  const content = contentOverride ?? query.data?.content ?? ''

  const dirty = Boolean(
    query.data &&
    (title !== query.data.title || content !== query.data.content),
  )
  const editable = query.data?.status === 'draft'
  const blocker = useBlocker(dirty)

  useEffect(() => {
    if (blocker.state !== 'blocked') return
    if (window.confirm('草稿有未保存修改，确定离开吗？')) blocker.proceed()
    else blocker.reset()
  }, [blocker])

  useBeforeUnload((event) => {
    if (!dirty) return
    event.preventDefault()
  })

  if (query.isPending) {
    return <LoadingState message="正在加载草稿…" />
  }
  if (query.isError) {
    return (
      <ErrorState error={query.error} onRetry={() => void query.refetch()} title="草稿加载失败" />
    )
  }

  const draft = query.data

  async function save() {
    await update.mutateAsync({ title: title.trim(), content: content.trim() })
  }

  async function confirmDraft() {
    if (dirty) return
    await confirm.mutateAsync()
  }

  async function discardDraft() {
    if (!window.confirm('确定丢弃这份草稿吗？此操作会改变草稿状态。')) return
    await discard.mutateAsync()
  }

  async function exportDraft() {
    const result = await exportMutation.mutateAsync()
    downloadMarkdown(title, result.content)
  }

  const actionError =
    update.error ?? confirm.error ?? discard.error ?? exportMutation.error

  return (
    <section className="mx-auto max-w-5xl">
      <Button
        asChild
        variant="secondary"
      >
        <Link to="/drafts"><ArrowLeft aria-hidden="true" className="size-4" />返回草稿</Link>
      </Button>

      <div className="mt-[var(--space-6)] rounded-xl border border-[var(--color-border)] bg-white p-[var(--space-5)] shadow-sm sm:p-[var(--space-6)]">
        <div className="flex flex-col gap-[var(--space-3)] sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="text-xs text-[var(--color-text-secondary)]">{draft.draftType}</p>
            <h2 className="mt-[var(--space-1)] text-2xl font-semibold">{draft.title}</h2>
          </div>
          <span className="w-fit rounded-full bg-slate-100 px-[var(--space-3)] py-[var(--space-1)] text-sm">
            {draft.status}
          </span>
        </div>

        {!editable ? (
          <div className="mt-[var(--space-4)] flex items-center gap-[var(--space-2)] rounded-lg bg-blue-50 p-[var(--space-3)] text-sm text-blue-800">
            <ShieldCheck aria-hidden="true" className="size-4" />
            当前状态为 {draft.status}，正文已只读。
          </div>
        ) : null}

        {actionError ? (
          <Alert tone="danger" className="mt-[var(--space-4)]">{actionError.message}</Alert>
        ) : null}

        <div className="mt-[var(--space-6)] space-y-[var(--space-4)]">
          <label className="block text-sm font-semibold">
            标题
            <input
              value={title}
              maxLength={255}
              disabled={!editable}
              onChange={(event) => setTitleOverride(event.target.value)}
              className="mt-[var(--space-2)] min-h-10 w-full rounded-md border border-[var(--color-border)] px-[var(--space-3)] font-normal disabled:bg-slate-50"
            />
          </label>
          <label className="block text-sm font-semibold">
            正文
            <textarea
              value={content}
              rows={16}
              disabled={!editable}
              onChange={(event) => setContentOverride(event.target.value)}
              className="mt-[var(--space-2)] w-full rounded-md border border-[var(--color-border)] p-[var(--space-3)] font-normal leading-6 disabled:bg-slate-50"
            />
          </label>
          <div className="rounded-lg bg-slate-50 p-[var(--space-4)]">
            <h3 className="text-sm font-semibold">来源问题</h3>
            <p className="mt-[var(--space-1)] whitespace-pre-wrap text-sm text-[var(--color-text-secondary)]">
              {draft.sourceQuestion || '无'}
            </p>
          </div>
          {draft.relatedSources.length > 0 ? (
            <details className="rounded-lg border border-[var(--color-border)]">
              <summary className="cursor-pointer p-[var(--space-3)] text-sm font-semibold">
                查看关联来源（{draft.relatedSources.length}）
              </summary>
              <pre className="overflow-x-auto border-t border-[var(--color-border)] p-[var(--space-3)] text-xs">
                {JSON.stringify(draft.relatedSources, null, 2)}
              </pre>
            </details>
          ) : null}
        </div>

        <div className="mt-[var(--space-6)] flex flex-wrap justify-end gap-[var(--space-3)]">
          {editable ? (
            <>
              <Button
                variant="secondary"
                disabled={!dirty || update.isPending || !title.trim() || !content.trim()}
                onClick={() => void save()}
              >
                <Save aria-hidden="true" className="size-4" />
                {update.isPending ? '正在保存…' : '保存草稿'}
              </Button>
              <Button
                disabled={dirty || confirm.isPending}
                onClick={() => void confirmDraft()}
              >
                <ShieldCheck aria-hidden="true" className="size-4" />
                {confirm.isPending ? '正在确认…' : '确认草稿'}
              </Button>
              <Button
                variant="danger"
                disabled={discard.isPending}
                onClick={() => void discardDraft()}
              >
                <Trash2 aria-hidden="true" className="size-4" />
                丢弃草稿
              </Button>
            </>
          ) : null}
          {draft.status !== 'discarded' ? (
            <Button
              variant="secondary"
              disabled={dirty || exportMutation.isPending}
              onClick={() => void exportDraft()}
            >
              <Download aria-hidden="true" className="size-4" />
              {exportMutation.isPending ? '正在导出…' : '导出 Markdown'}
            </Button>
          ) : null}
        </div>
      </div>

      <button type="button" className="sr-only" onClick={() => navigate('/drafts')}>
        返回草稿列表
      </button>
    </section>
  )
}
