import * as Dialog from '@radix-ui/react-dialog'
import { Clipboard, Search, X } from 'lucide-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Button } from '../../components/ui/button'
import { Alert } from '../../components/feedback/alert'
import { EmptyState, LoadingState } from '../../components/feedback/state-views'
import { useAuditLogQuery, useAuditLogsQuery } from './queries'

function positiveInt(value: string | null, fallback: number): number {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback
}

export function AuditPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [selectedId, setSelectedId] = useState('')
  const page = positiveInt(searchParams.get('page'), 1)
  const pageSize = Math.min(positiveInt(searchParams.get('page_size'), 20), 100)
  const filters = {
    page,
    pageSize,
    action: searchParams.get('action') || undefined,
    targetType: searchParams.get('target_type') || undefined,
    actorId: searchParams.get('actor_id') || undefined,
    createdFrom: searchParams.get('created_from') || undefined,
    createdTo: searchParams.get('created_to') || undefined,
  }
  const query = useAuditLogsQuery(filters)
  const totalPages = Math.max(1, Math.ceil((query.data?.total ?? 0) / pageSize))

  function setFilter(key: string, value: string) {
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
      <h2 className="text-2xl font-semibold">审计日志</h2>
      <p className="mt-1 text-sm text-[var(--color-text-secondary)]">仅系统管理员可访问；敏感字段由后端递归脱敏。</p>
      <div className="mt-6 grid gap-3 rounded-xl border border-[var(--color-border)] bg-white p-4 md:grid-cols-3">
        <Filter label="动作" value={filters.action ?? ''} onChange={(value) => setFilter('action', value)} />
        <Filter label="目标类型" value={filters.targetType ?? ''} onChange={(value) => setFilter('target_type', value)} />
        <Filter label="操作者 ID" value={filters.actorId ?? ''} onChange={(value) => setFilter('actor_id', value)} />
        <Filter label="开始时间" type="datetime-local" value={filters.createdFrom ?? ''} onChange={(value) => setFilter('created_from', value)} />
        <Filter label="结束时间" type="datetime-local" value={filters.createdTo ?? ''} onChange={(value) => setFilter('created_to', value)} />
      </div>
      {query.isPending ? <div className="mt-6 rounded-xl border border-[var(--color-border)] bg-white"><LoadingState message="正在加载审计日志…" minH="min-h-48" /></div> : query.isError ? (
        <Alert tone="danger" className="mt-6" title="审计日志加载失败" action={<Button onClick={() => void query.refetch()}>重新加载</Button>}><p>{query.error.message}</p></Alert>
      ) : query.data.items.length === 0 ? <div className="mt-6 rounded-xl border border-dashed border-[var(--color-border)] bg-white"><EmptyState title="没有符合条件的审计记录" minH="min-h-48" /></div> : (
        <>
          <div className="mt-6 overflow-x-auto rounded-xl border border-[var(--color-border)] bg-white">
            <table className="min-w-full text-left text-sm"><thead className="bg-slate-50 text-xs"><tr>{['时间','操作者','动作','目标','IP','详情'].map((item) => <th key={item} className="px-3 py-2">{item}</th>)}</tr></thead>
              <tbody className="divide-y divide-[var(--color-border)]">{query.data.items.map((item) => <tr key={item.id}><td className="whitespace-nowrap px-3 py-3">{new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'medium' }).format(new Date(item.createdAt))}</td><td className="px-3 py-3">{item.actor?.displayName ?? item.actorId ?? '系统'}</td><td className="px-3 py-3 font-semibold">{item.action}</td><td className="px-3 py-3">{item.targetType}<br/><span className="text-xs text-[var(--color-text-secondary)]">{item.targetId ?? '—'}</span></td><td className="px-3 py-3">{item.ipAddress ?? '—'}</td><td className="px-3 py-3"><Button className="min-h-8 py-1 text-xs" onClick={() => setSelectedId(item.id)}><Search className="size-3" />查看</Button></td></tr>)}</tbody>
            </table>
          </div>
          <div className="mt-4 flex items-center justify-between"><p className="text-sm">共 {query.data.total} 条，第 {page} / {totalPages} 页</p><div className="flex gap-2"><Button disabled={page <= 1} onClick={() => goToPage(page - 1)}>上一页</Button><Button disabled={page >= totalPages} onClick={() => goToPage(page + 1)}>下一页</Button></div></div>
        </>
      )}
      <AuditDetailDialog id={selectedId} onOpenChange={(open) => { if (!open) setSelectedId('') }} />
    </section>
  )
}

function Filter({ label, value, onChange, type = 'text' }: { label: string; value: string; onChange: (value: string) => void; type?: string }) {
  return <label className="text-sm font-semibold">{label}<input type={type} value={value} onChange={(event) => onChange(event.target.value)} className="mt-2 min-h-10 w-full rounded-md border border-[var(--color-border)] px-3 font-normal" /></label>
}

function AuditDetailDialog({ id, onOpenChange }: { id: string; onOpenChange: (open: boolean) => void }) {
  const query = useAuditLogQuery(id)
  return <Dialog.Root open={Boolean(id)} onOpenChange={onOpenChange}><Dialog.Portal><Dialog.Overlay className="fixed inset-0 z-40 bg-slate-950/50" /><Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[calc(100%-32px)] max-w-2xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl bg-white p-6 shadow-xl"><Dialog.Title className="text-lg font-semibold">审计详情</Dialog.Title><Dialog.Close aria-label="关闭对话框" className="absolute right-4 top-4"><X className="size-5" /></Dialog.Close>
    {query.isPending ? <div className="mt-5"><LoadingState message="正在加载详情…" minH="min-h-0" /></div> : query.isError ? <Alert tone="danger" className="mt-5">{query.error.message}</Alert> : query.data ? <div className="mt-5 space-y-4 text-sm"><dl className="grid gap-3 sm:grid-cols-2"><div><dt className="font-semibold">动作</dt><dd>{query.data.action}</dd></div><div><dt className="font-semibold">操作者</dt><dd>{query.data.actor?.displayName ?? '系统'}</dd></div><div><dt className="font-semibold">目标</dt><dd>{query.data.targetType} / {query.data.targetId ?? '—'}</dd></div><div><dt className="font-semibold">IP</dt><dd>{query.data.ipAddress ?? '—'}</dd></div></dl>
      <div><div className="flex items-center justify-between"><h3 className="font-semibold">Request ID</h3>{query.data.requestId ? <Button className="min-h-8 py-1 text-xs" onClick={() => void navigator.clipboard?.writeText(query.data.requestId ?? '')}><Clipboard className="size-3" />复制</Button> : null}</div><p className="mt-1 break-all">{query.data.requestId ?? '无'}</p></div>
      <details open className="rounded-lg border border-[var(--color-border)]"><summary className="cursor-pointer p-3 font-semibold">脱敏详情</summary><pre className="max-h-80 overflow-auto border-t border-[var(--color-border)] p-3 text-xs">{JSON.stringify(query.data.detail, null, 2)}</pre></details>
    </div> : null}
  </Dialog.Content></Dialog.Portal></Dialog.Root>
}
