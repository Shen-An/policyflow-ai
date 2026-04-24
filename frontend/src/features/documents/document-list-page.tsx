import { FileText, RefreshCw, Upload } from 'lucide-react'
import { useState } from 'react'
import { useOutletContext, useSearchParams } from 'react-router-dom'
import type {
  KnowledgeBase,
  KnowledgeDocument,
  ResourcePermission,
} from '../../api/knowledge-bases'
import { Button } from '../../components/ui/button'
import { Alert } from '../../components/feedback/alert'
import { EmptyState, LoadingState } from '../../components/feedback/state-views'
import { UploadDocumentDialog } from './components/upload-document-dialog'
import {
  useDocumentsQuery,
  useDocumentStatusQuery,
  useReindexDocumentMutation,
} from './queries'

function positiveInt(value: string | null, fallback: number): number {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback
}

function canWrite(permission: ResourcePermission): boolean {
  return permission === 'write' || permission === 'admin'
}

export function DocumentListPage() {
  const { knowledgeBase } = useOutletContext<{ knowledgeBase: KnowledgeBase }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const [uploadOpen, setUploadOpen] = useState(false)
  const page = positiveInt(searchParams.get('page'), 1)
  const pageSize = Math.min(positiveInt(searchParams.get('page_size'), 20), 100)
  const query = useDocumentsQuery(knowledgeBase.id, page, pageSize)
  const totalPages = Math.max(1, Math.ceil((query.data?.total ?? 0) / pageSize))
  const writable = canWrite(knowledgeBase.permission)

  function goToPage(nextPage: number) {
    const next = new URLSearchParams(searchParams)
    next.set('page', String(nextPage))
    setSearchParams(next)
  }

  return (
    <section>
      <div className="flex flex-col gap-[var(--space-3)] sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h3 className="font-semibold">文档</h3>
          <p className="mt-[var(--space-1)] text-sm text-[var(--color-text-secondary)]">
            索引任务会在成功或失败时停止轮询。
          </p>
        </div>
        {writable ? (
          <Button onClick={() => setUploadOpen(true)}>
            <Upload aria-hidden="true" className="size-4" />上传文档
          </Button>
        ) : (
          <p className="text-sm text-[var(--color-text-secondary)]">
            当前为只读权限，不能上传或重新索引。
          </p>
        )}
      </div>

      {query.isPending ? (
        <div className="mt-[var(--space-4)]">
          <LoadingState message="正在加载文档…" minH="min-h-48" />
        </div>
      ) : query.isError ? (
        <div className="mt-[var(--space-4)]">
          <Alert tone="danger" title="文档列表加载失败" action={<Button onClick={() => void query.refetch()}>重新加载</Button>}>
            <p>{query.error.message}</p>
          </Alert>
        </div>
      ) : query.data.items.length === 0 ? (
        <div className="mt-[var(--space-4)]">
          <EmptyState
            icon={<FileText aria-hidden="true" className="size-8" />}
            title="还没有文档"
            hint={writable ? '上传第一份制度文档开始索引。' : '请联系有写权限的管理员上传文档。'}
            minH="min-h-48"
          />
        </div>
      ) : (
        <>
          <div className="mt-[var(--space-4)] overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead className="bg-slate-50 text-xs text-[var(--color-text-secondary)]">
                <tr>
                  {['标题', '类型', '版本', '索引状态', '创建时间', '操作'].map((heading) => (
                    <th key={heading} className="px-[var(--space-3)] py-[var(--space-2)]">{heading}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {query.data.items.map((document) => (
                  <DocumentRow
                    key={document.id}
                    knowledgeBaseId={knowledgeBase.id}
                    document={document}
                    writable={writable}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-[var(--space-4)] flex items-center justify-between">
            <p className="text-sm text-[var(--color-text-secondary)]">
              共 {query.data.total} 份，第 {page} / {totalPages} 页
            </p>
            <div className="flex gap-[var(--space-2)]">
              <Button
                variant="secondary"
                disabled={page <= 1}
                onClick={() => goToPage(page - 1)}
              >
                上一页
              </Button>
              <Button
                variant="secondary"
                disabled={page >= totalPages}
                onClick={() => goToPage(page + 1)}
              >
                下一页
              </Button>
            </div>
          </div>
        </>
      )}

      {writable ? (
        <UploadDocumentDialog
          knowledgeBaseId={knowledgeBase.id}
          open={uploadOpen}
          onOpenChange={setUploadOpen}
        />
      ) : null}
    </section>
  )
}

function DocumentRow({
  knowledgeBaseId,
  document,
  writable,
}: {
  knowledgeBaseId: string
  document: KnowledgeDocument
  writable: boolean
}) {
  const statusQuery = useDocumentStatusQuery(document.id, document.indexStatus)
  const reindex = useReindexDocumentMutation(knowledgeBaseId)
  const status = statusQuery.data?.indexStatus ?? document.indexStatus
  const error = statusQuery.data?.indexError
  const active = status === 'pending' || status === 'indexing'

  return (
    <tr>
      <td className="px-[var(--space-3)] py-[var(--space-3)] font-semibold">{document.title}</td>
      <td className="px-[var(--space-3)] py-[var(--space-3)]">{document.fileType}</td>
      <td className="px-[var(--space-3)] py-[var(--space-3)]">v{document.sourceVersion}</td>
      <td className="px-[var(--space-3)] py-[var(--space-3)]">
        <span className="inline-flex items-center gap-[var(--space-1)]">
          {active ? <RefreshCw aria-hidden="true" className="size-4 animate-spin motion-reduce:animate-none" /> : null}
          {status}
        </span>
        {error ? <p className="mt-[var(--space-1)] max-w-xs text-xs text-[var(--color-danger)]">{error}</p> : null}
      </td>
      <td className="whitespace-nowrap px-[var(--space-3)] py-[var(--space-3)]">
        {new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium' }).format(new Date(document.createdAt))}
      </td>
      <td className="px-[var(--space-3)] py-[var(--space-3)]">
        {writable && status === 'failed' ? (
          <Button
            variant="secondary"
            disabled={reindex.isPending}
            onClick={() => reindex.mutate(document.id)}
          >
            {reindex.isPending ? '正在重试…' : '重新索引'}
          </Button>
        ) : (
          <span className="text-xs text-[var(--color-text-secondary)]">—</span>
        )}
      </td>
    </tr>
  )
}
