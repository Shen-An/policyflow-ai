import { BookOpen, Plus, Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import type { KnowledgeBase } from '../../api/knowledge-bases'
import { hasAnyRole } from '../../auth/permissions'
import { useAuthState } from '../../auth/auth-store'
import { Button } from '../../components/ui/button'
import { CreateKnowledgeBaseDialog } from './components/create-knowledge-base-dialog'
import { useKnowledgeBasesQuery } from './queries'

function positiveInt(value: string | null, fallback: number): number {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback
}

export function KnowledgeBaseListPage() {
  const user = useAuthState((state) => state.user)
  const [searchParams, setSearchParams] = useSearchParams()
  const [createOpen, setCreateOpen] = useState(false)
  const query = useKnowledgeBasesQuery()
  const keyword = searchParams.get('keyword')?.trim().toLowerCase() ?? ''
  const page = positiveInt(searchParams.get('page'), 1)
  const pageSize = Math.min(positiveInt(searchParams.get('page_size'), 12), 100)
  const canCreate = Boolean(
    user && hasAnyRole(user.roles, ['kb_admin', 'sys_admin']),
  )

  const filtered = useMemo(() => {
    const items = query.data ?? []
    if (!keyword) return items
    return items.filter((item) =>
      [item.name, item.code, item.description].some((value) =>
        value.toLowerCase().includes(keyword),
      ),
    )
  }, [keyword, query.data])
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize))
  const visible = filtered.slice((page - 1) * pageSize, page * pageSize)

  function updateSearch(value: string) {
    const next = new URLSearchParams(searchParams)
    if (value.trim()) next.set('keyword', value)
    else next.delete('keyword')
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
          <h2 className="text-2xl font-semibold leading-8">知识库</h2>
          <p className="mt-[var(--space-1)] text-sm text-[var(--color-text-secondary)]">
            仅展示后端授权给当前用户的知识资源。
          </p>
        </div>
        {canCreate ? (
          <Button onClick={() => setCreateOpen(true)}>
            <Plus aria-hidden="true" className="size-4" />
            创建知识库
          </Button>
        ) : null}
      </div>

      <label className="relative mt-[var(--space-6)] block max-w-sm">
        <span className="sr-only">筛选知识库</span>
        <Search
          aria-hidden="true"
          className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--color-text-secondary)]"
        />
        <input
          value={searchParams.get('keyword') ?? ''}
          onChange={(event) => updateSearch(event.target.value)}
          placeholder="搜索名称、编码或描述"
          className="min-h-10 w-full rounded-md border border-[var(--color-border)] pl-10 pr-[var(--space-3)]"
        />
      </label>

      {query.isPending ? (
        <div className="mt-[var(--space-6)] min-h-64 rounded-xl border border-[var(--color-border)] bg-white p-[var(--space-8)]" role="status">
          正在加载知识库…
        </div>
      ) : query.isError ? (
        <div className="mt-[var(--space-6)] rounded-xl border border-red-200 bg-red-50 p-[var(--space-6)]" role="alert">
          <h3 className="font-semibold">知识库加载失败</h3>
          <p className="mt-[var(--space-1)] text-sm">{query.error.message}</p>
          <div className="mt-[var(--space-4)]">
            <Button onClick={() => void query.refetch()}>重新加载</Button>
          </div>
        </div>
      ) : visible.length === 0 ? (
        <div className="mt-[var(--space-6)] grid min-h-64 place-items-center rounded-xl border border-[var(--color-border)] bg-white p-[var(--space-8)] text-center">
          <div>
            <BookOpen className="mx-auto size-8 text-[var(--color-text-secondary)]" aria-hidden="true" />
            <h3 className="mt-[var(--space-3)] font-semibold">
              {keyword ? '没有匹配的知识库' : '没有可访问的知识库'}
            </h3>
            <p className="mt-[var(--space-1)] text-sm text-[var(--color-text-secondary)]">
              {keyword
                ? '请调整筛选条件。'
                : '请联系知识库管理员授予读取权限。'}
            </p>
          </div>
        </div>
      ) : (
        <>
          <div className="mt-[var(--space-6)] grid gap-[var(--space-4)] md:grid-cols-2 xl:grid-cols-3">
            {visible.map((knowledgeBase) => (
              <KnowledgeBaseCard key={knowledgeBase.id} knowledgeBase={knowledgeBase} />
            ))}
          </div>
          <div className="mt-[var(--space-6)] flex items-center justify-between">
            <p className="text-sm text-[var(--color-text-secondary)]">
              共 {filtered.length} 个，第 {page} / {totalPages} 页
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

      <CreateKnowledgeBaseDialog open={createOpen} onOpenChange={setCreateOpen} />
    </section>
  )
}

function KnowledgeBaseCard({ knowledgeBase }: { knowledgeBase: KnowledgeBase }) {
  return (
    <article className="rounded-xl border border-[var(--color-border)] bg-white p-[var(--space-6)] shadow-sm">
      <div className="flex items-start justify-between gap-[var(--space-3)]">
        <div>
          <h3 className="font-semibold">{knowledgeBase.name}</h3>
          <p className="mt-[var(--space-1)] text-xs text-[var(--color-text-secondary)]">
            {knowledgeBase.code}
          </p>
        </div>
        <span className="rounded-full bg-blue-50 px-[var(--space-2)] py-[var(--space-1)] text-xs text-blue-700">
          {knowledgeBase.permission}
        </span>
      </div>
      <p className="mt-[var(--space-3)] min-h-11 text-sm leading-[22px] text-[var(--color-text-secondary)]">
        {knowledgeBase.description || '暂无描述'}
      </p>
      <div className="mt-[var(--space-4)] flex items-center justify-between text-xs text-[var(--color-text-secondary)]">
        <span>{knowledgeBase.documentCount} 份文档</span>
        <span>{knowledgeBase.defaultQueryMode}</span>
      </div>
      <div className="mt-[var(--space-4)]">
        <Button asChild className="w-full">
          <Link to={`/knowledge-bases/${knowledgeBase.id}`}>查看详情</Link>
        </Button>
      </div>
    </article>
  )
}
