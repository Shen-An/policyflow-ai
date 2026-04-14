import * as Dialog from '@radix-ui/react-dialog'
import { Check, FileQuestion, X } from 'lucide-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { FAQDraft } from '../../api/faq'
import { Button } from '../../components/ui/button'
import { useDocumentStatusQuery } from '../documents/queries'
import { useKnowledgeBasesQuery } from '../knowledge-bases/queries'
import {
  useApproveFAQMutation,
  useFAQDraftsQuery,
  useRejectFAQMutation,
} from './queries'

export function FAQReviewPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [rejecting, setRejecting] = useState<FAQDraft | null>(null)
  const [approvedDocumentId, setApprovedDocumentId] = useState<string | null>(null)
  const knowledgeBaseId = searchParams.get('knowledge_base_id') ?? ''
  const status = searchParams.get('status') ?? 'draft'
  const knowledgeBases = useKnowledgeBasesQuery()
  const query = useFAQDraftsQuery(knowledgeBaseId, status)
  const approve = useApproveFAQMutation()

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(key, value)
    else next.delete(key)
    setSearchParams(next, { replace: true })
  }

  async function approveItem(item: FAQDraft) {
    if (!window.confirm('审核通过后会创建知识文档并触发增量索引，是否继续？')) return
    const result = await approve.mutateAsync(item.id)
    setApprovedDocumentId(result.documentId)
  }

  return (
    <section>
      <div>
        <h2 className="text-2xl font-semibold">FAQ 审核</h2>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          审核通过会写入知识库并触发索引；驳回必须填写原因。
        </p>
      </div>
      <div className="mt-6 flex flex-wrap gap-3">
        <label className="text-sm font-semibold">
          知识库
          <select value={knowledgeBaseId} onChange={(event) => setFilter('knowledge_base_id', event.target.value)} className="ml-2 min-h-10 rounded-md border border-[var(--color-border)] px-3 font-normal">
            <option value="">全部</option>
            {knowledgeBases.data?.map((kb) => <option key={kb.id} value={kb.id}>{kb.name}</option>)}
          </select>
        </label>
        <label className="text-sm font-semibold">
          状态
          <select value={status} onChange={(event) => setFilter('status', event.target.value)} className="ml-2 min-h-10 rounded-md border border-[var(--color-border)] px-3 font-normal">
            <option value="">全部</option>
            <option value="draft">待审核</option>
            <option value="approved">已通过</option>
            <option value="rejected">已驳回</option>
          </select>
        </label>
      </div>

      {approvedDocumentId ? <IndexStatus documentId={approvedDocumentId} /> : null}
      {approve.isError ? <p role="alert" className="mt-4 rounded-md bg-red-50 p-3 text-sm text-[var(--color-danger)]">{approve.error.message}</p> : null}

      {query.isPending ? (
        <div role="status" className="mt-6 min-h-48 p-6">正在加载 FAQ…</div>
      ) : query.isError ? (
        <div role="alert" className="mt-6 rounded-xl border border-red-200 bg-red-50 p-5">
          <h3 className="font-semibold">FAQ 加载失败</h3>
          <p className="mt-1 text-sm">{query.error.message}</p>
          <Button className="mt-3" onClick={() => void query.refetch()}>重新加载</Button>
        </div>
      ) : query.data.length === 0 ? (
        <div className="mt-6 grid min-h-64 place-items-center rounded-xl border border-dashed border-[var(--color-border)] bg-white text-center">
          <div><FileQuestion className="mx-auto size-8 text-[var(--color-text-secondary)]" /><h3 className="mt-3 font-semibold">没有符合条件的 FAQ</h3></div>
        </div>
      ) : (
        <div className="mt-6 grid gap-4 lg:grid-cols-2">
          {query.data.map((item) => (
            <article key={item.id} className="rounded-xl border border-[var(--color-border)] bg-white p-5 shadow-sm">
              <div className="flex items-start justify-between gap-3">
                <h3 className="font-semibold">{item.question}</h3>
                <span className="rounded-full bg-slate-100 px-2 py-1 text-xs">{item.status}</span>
              </div>
              <p className="mt-3 whitespace-pre-wrap text-sm leading-6">{item.answer}</p>
              <dl className="mt-4 grid gap-2 rounded-lg bg-slate-50 p-3 text-xs sm:grid-cols-2">
                <div><dt className="font-semibold">知识库</dt><dd>{item.knowledgeBaseName}</dd></div>
                <div><dt className="font-semibold">来源文档</dt><dd>{item.sourceDocumentTitle ?? '无'}</dd></div>
                {item.reviewNote ? <div className="sm:col-span-2"><dt className="font-semibold">审核备注</dt><dd>{item.reviewNote}</dd></div> : null}
              </dl>
              {item.status === 'draft' || item.status === 'pending_review' ? (
                <div className="mt-4 flex justify-end gap-3">
                  <Button className="bg-white text-[var(--color-text-primary)] ring-1 ring-[var(--color-border)] hover:bg-slate-50" onClick={() => setRejecting(item)}>驳回</Button>
                  <Button disabled={approve.isPending} onClick={() => void approveItem(item)}><Check className="size-4" />审核通过</Button>
                </div>
              ) : null}
            </article>
          ))}
        </div>
      )}
      <RejectDialog item={rejecting} onOpenChange={(open) => { if (!open) setRejecting(null) }} />
    </section>
  )
}

function IndexStatus({ documentId }: { documentId: string }) {
  const query = useDocumentStatusQuery(documentId, 'pending')
  const status = query.data?.indexStatus ?? 'pending'
  return <p role="status" className="mt-4 rounded-md bg-blue-50 p-3 text-sm">FAQ 文档索引状态：{status}</p>
}

function RejectDialog({ item, onOpenChange }: { item: FAQDraft | null; onOpenChange: (open: boolean) => void }) {
  const mutation = useRejectFAQMutation()
  const [reason, setReason] = useState('')
  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!item) return
    await mutation.mutateAsync({ id: item.id, reason: reason.trim() })
    setReason('')
    onOpenChange(false)
  }
  return (
    <Dialog.Root open={Boolean(item)} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-slate-950/50" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-32px)] max-w-lg -translate-x-1/2 -translate-y-1/2 rounded-xl bg-white p-6 shadow-xl">
          <Dialog.Title className="text-lg font-semibold">驳回 FAQ</Dialog.Title>
          <Dialog.Description className="mt-1 text-sm text-[var(--color-text-secondary)]">请说明驳回原因，最多 1000 字。</Dialog.Description>
          <Dialog.Close aria-label="关闭对话框" className="absolute right-4 top-4"><X className="size-5" /></Dialog.Close>
          {mutation.isError ? <p role="alert" className="mt-4 text-sm text-[var(--color-danger)]">{mutation.error.message}</p> : null}
          <form className="mt-5" onSubmit={submit}>
            <label className="text-sm font-semibold">驳回原因<textarea required maxLength={1000} rows={5} value={reason} onChange={(event) => setReason(event.target.value)} className="mt-2 w-full rounded-md border border-[var(--color-border)] p-3 font-normal" /></label>
            <div className="mt-5 flex justify-end gap-3"><Button className="bg-white text-[var(--color-text-primary)] ring-1 ring-[var(--color-border)]" onClick={() => onOpenChange(false)}>取消</Button><Button type="submit" disabled={!reason.trim() || mutation.isPending}>确认驳回</Button></div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
