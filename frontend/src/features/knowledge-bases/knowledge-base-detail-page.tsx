import { ArrowLeft, FileText, Info } from 'lucide-react'
import { Link, Outlet, useLocation, useParams } from 'react-router-dom'
import { Button } from '../../components/ui/button'
import { ErrorState, LoadingState } from '../../components/feedback/state-views'
import { useKnowledgeBaseQuery } from './queries'

export function KnowledgeBaseDetailPage() {
  const { kbId = '' } = useParams()
  const location = useLocation()
  const query = useKnowledgeBaseQuery(kbId)

  if (query.isPending) {
    return <LoadingState message="正在加载知识库详情…" />
  }
  if (query.isError) {
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} title="知识库详情加载失败" />
  }

  const knowledgeBase = query.data
  const documentsActive = location.pathname.endsWith('/documents')

  return (
    <section>
      <Button
        asChild
        variant="secondary"
      >
        <Link to="/knowledge-bases"><ArrowLeft aria-hidden="true" className="size-4" />返回知识库</Link>
      </Button>
      <div className="mt-[var(--space-6)] rounded-xl border border-[var(--color-border)] bg-white p-[var(--space-6)] shadow-sm">
        <div className="flex flex-col gap-[var(--space-3)] sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 className="text-2xl font-semibold">{knowledgeBase.name}</h2>
            <p className="mt-[var(--space-1)] text-sm text-[var(--color-text-secondary)]">
              {knowledgeBase.code} · {knowledgeBase.defaultQueryMode}
            </p>
          </div>
          <span className="w-fit rounded-full bg-blue-50 px-[var(--space-3)] py-[var(--space-1)] text-sm text-blue-700">
            {knowledgeBase.permission}
          </span>
        </div>
        <nav aria-label="知识库详情导航" className="mt-[var(--space-6)] flex gap-[var(--space-2)] border-b border-[var(--color-border)]">
          <Link
            to={`/knowledge-bases/${knowledgeBase.id}`}
            className={!documentsActive ? 'border-b-2 border-blue-600 px-[var(--space-3)] py-[var(--space-2)] text-sm font-semibold text-blue-700' : 'px-[var(--space-3)] py-[var(--space-2)] text-sm text-[var(--color-text-secondary)]'}
          >
            <Info aria-hidden="true" className="mr-[var(--space-1)] inline size-4" />概览
          </Link>
          <Link
            to={`/knowledge-bases/${knowledgeBase.id}/documents`}
            className={documentsActive ? 'border-b-2 border-blue-600 px-[var(--space-3)] py-[var(--space-2)] text-sm font-semibold text-blue-700' : 'px-[var(--space-3)] py-[var(--space-2)] text-sm text-[var(--color-text-secondary)]'}
          >
            <FileText aria-hidden="true" className="mr-[var(--space-1)] inline size-4" />文档
          </Link>
        </nav>
        <div className="mt-[var(--space-6)]">
          <Outlet context={{ knowledgeBase }} />
        </div>
      </div>
    </section>
  )
}
