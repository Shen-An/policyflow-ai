import { Plus, RefreshCw, Search, Users } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { UserRecord } from '../../api/users'
import { AppError } from '../../api/errors'
import { Button } from '../../components/ui/button'
import { CreateUserDialog } from './components/create-user-dialog'
import { EditRolesDialog } from './components/edit-roles-dialog'
import { useUsersQuery } from './queries'

function positiveInt(value: string | null, fallback: number): number {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

export function UsersPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const page = positiveInt(searchParams.get('page'), 1)
  const pageSize = Math.min(positiveInt(searchParams.get('page_size'), 20), 100)
  const keyword = searchParams.get('keyword')?.trim() ?? ''
  const [keywordInput, setKeywordInput] = useState(keyword)
  const [createOpen, setCreateOpen] = useState(false)
  const [editingUser, setEditingUser] = useState<UserRecord | null>(null)
  const params = useMemo(() => ({ page, pageSize, keyword: keyword || undefined }), [keyword, page, pageSize])
  const query = useUsersQuery(params)

  useEffect(() => {
    if (keywordInput.trim() === keyword) return
    const timer = window.setTimeout(() => {
      const next = new URLSearchParams(searchParams)
      const normalized = keywordInput.trim()
      if (normalized) next.set('keyword', normalized); else next.delete('keyword')
      next.set('page', '1')
      setSearchParams(next, { replace: true })
    }, 300)
    return () => window.clearTimeout(timer)
  }, [keyword, keywordInput, searchParams, setSearchParams])

  const totalPages = Math.max(1, Math.ceil((query.data?.total ?? 0) / pageSize))
  function goToPage(nextPage: number) {
    const next = new URLSearchParams(searchParams)
    next.set('page', String(nextPage))
    setSearchParams(next)
  }

  return (
    <section>
      <div className="flex flex-col gap-[var(--space-4)] sm:flex-row sm:items-start sm:justify-between">
        <div><h2 className="text-2xl font-semibold leading-8">用户管理</h2><p className="mt-[var(--space-1)] text-sm leading-[22px] text-[var(--color-text-secondary)]">查看组织用户、创建账户并维护角色。删除、禁用和密码重置不在当前范围。</p></div>
        <Button onClick={() => setCreateOpen(true)}><Plus aria-hidden="true" className="size-4" />创建用户</Button>
      </div>

      <div className="mt-[var(--space-6)] rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] shadow-sm">
        <div className="flex flex-col gap-[var(--space-3)] border-b border-[var(--color-border)] p-[var(--space-4)] sm:flex-row sm:items-center sm:justify-between">
          <label className="relative block w-full max-w-sm"><span className="sr-only">搜索用户</span><Search aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--color-text-secondary)]" /><input value={keywordInput} onChange={(event) => setKeywordInput(event.target.value)} placeholder="搜索用户名、邮箱或显示名" className="min-h-10 w-full rounded-md border border-[var(--color-border)] pl-10 pr-[var(--space-3)]" /></label>
          <p className="text-sm text-[var(--color-text-secondary)]">共 {query.data?.total ?? 0} 位用户{query.isFetching && !query.isPending ? '，正在刷新…' : ''}</p>
        </div>

        {query.isPending ? <LoadingState /> : query.isError ? <ErrorState error={query.error} onRetry={() => void query.refetch()} /> : query.data.items.length === 0 ? <EmptyState filtered={Boolean(keyword)} /> : <UserTable users={query.data.items} onEditRoles={setEditingUser} />}

        {!query.isPending && !query.isError && query.data.items.length > 0 ? <div className="flex items-center justify-between border-t border-[var(--color-border)] p-[var(--space-4)]"><p className="text-sm text-[var(--color-text-secondary)]">第 {page} / {totalPages} 页</p><div className="flex gap-[var(--space-2)]"><Button className="bg-white text-[var(--color-text-primary)] ring-1 ring-[var(--color-border)] hover:bg-slate-50" disabled={page <= 1} onClick={() => goToPage(page - 1)}>上一页</Button><Button className="bg-white text-[var(--color-text-primary)] ring-1 ring-[var(--color-border)] hover:bg-slate-50" disabled={page >= totalPages} onClick={() => goToPage(page + 1)}>下一页</Button></div></div> : null}
      </div>

      <CreateUserDialog open={createOpen} onOpenChange={setCreateOpen} />
      {editingUser ? <EditRolesDialog key={editingUser.id} user={editingUser} open onOpenChange={(open) => { if (!open) setEditingUser(null) }} /> : null}
    </section>
  )
}

function LoadingState() { return <div className="flex min-h-64 items-center justify-center gap-[var(--space-3)] text-sm text-[var(--color-text-secondary)]" role="status"><RefreshCw aria-hidden="true" className="size-5 animate-spin motion-reduce:animate-none" />正在加载用户…</div> }
function EmptyState({ filtered }: { filtered: boolean }) { return <div className="grid min-h-64 place-items-center p-[var(--space-8)] text-center"><div><Users aria-hidden="true" className="mx-auto size-8 text-[var(--color-text-secondary)]" /><h3 className="mt-[var(--space-3)] font-semibold">{filtered ? '没有匹配的用户' : '还没有用户'}</h3><p className="mt-[var(--space-1)] text-sm text-[var(--color-text-secondary)]">{filtered ? '请调整关键词后重试。' : '使用“创建用户”添加第一个账户。'}</p></div></div> }
function ErrorState({ error, onRetry }: { error: Error; onRetry: () => void }) { const requestId = error instanceof AppError ? error.requestId : undefined; return <div className="grid min-h-64 place-items-center p-[var(--space-8)] text-center" role="alert"><div><h3 className="font-semibold">用户列表加载失败</h3><p className="mt-[var(--space-1)] text-sm text-[var(--color-text-secondary)]">{error.message} 请稍后重试。</p>{requestId ? <p className="mt-[var(--space-1)] text-xs text-[var(--color-text-secondary)]">请求编号：{requestId}</p> : null}<div className="mt-[var(--space-4)]"><Button onClick={onRetry}>重新加载</Button></div></div></div> }

function UserTable({ users, onEditRoles }: { users: UserRecord[]; onEditRoles: (user: UserRecord) => void }) {
  return <div className="overflow-x-auto"><table className="min-w-full border-collapse text-left text-sm"><thead className="bg-slate-50 text-xs font-semibold text-[var(--color-text-secondary)]"><tr>{['显示名 / 用户名','邮箱','部门','角色','状态','创建时间','操作'].map((heading) => <th key={heading} className="whitespace-nowrap px-[var(--space-4)] py-[var(--space-3)]">{heading}</th>)}</tr></thead><tbody className="divide-y divide-[var(--color-border)]">{users.map((user) => <tr key={user.id}><td className="px-[var(--space-4)] py-[var(--space-3)]"><p className="font-semibold">{user.displayName}</p><p className="text-xs text-[var(--color-text-secondary)]">{user.username}</p></td><td className="px-[var(--space-4)] py-[var(--space-3)]">{user.email}</td><td className="px-[var(--space-4)] py-[var(--space-3)]">{user.department?.name ?? '未分配'}</td><td className="px-[var(--space-4)] py-[var(--space-3)]"><div className="flex flex-wrap gap-[var(--space-1)]">{user.roles.map((role) => <span key={role} className="rounded-full bg-blue-50 px-[var(--space-2)] py-[var(--space-1)] text-xs text-blue-700">{role}</span>)}</div></td><td className="px-[var(--space-4)] py-[var(--space-3)]"><span className="inline-flex items-center gap-[var(--space-1)] text-[var(--color-success)]"><span aria-hidden="true">●</span>{user.status}</span></td><td className="whitespace-nowrap px-[var(--space-4)] py-[var(--space-3)]">{formatDate(user.createdAt)}</td><td className="px-[var(--space-4)] py-[var(--space-3)]"><Button className="bg-white text-[var(--color-text-primary)] ring-1 ring-[var(--color-border)] hover:bg-slate-50" onClick={() => onEditRoles(user)}>修改角色</Button></td></tr>)}</tbody></table></div>
}
