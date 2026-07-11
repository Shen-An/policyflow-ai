import { Menu, ShieldCheck, UserCircle, WifiOff, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, Outlet, useLocation } from 'react-router-dom'
import { canCallApi } from '../../api/readiness'
import { hasAnyRole } from '../../auth/permissions'
import { clearReturnTo } from '../../auth/auth-storage'
import { useAuth } from '../../auth/use-auth'
import { Button } from '../ui/button'

const titles: Record<string, string> = {
  '/': '工作台',
  '/admin/users': '用户管理',
  '/admin/skills': 'Skill 管理',
  '/admin/integrations': 'MCP 集成',
  '/admin/model-settings': '模型设置',
  '/forbidden': '无访问权限',
}

function useOnlineStatus(): boolean {
  const [online, setOnline] = useState(() => navigator.onLine)
  useEffect(() => {
    const on = () => setOnline(true)
    const off = () => setOnline(false)
    window.addEventListener('online', on)
    window.addEventListener('offline', off)
    return () => { window.removeEventListener('online', on); window.removeEventListener('offline', off) }
  }, [])
  return online
}

export function AppShell() {
  const { user, logout } = useAuth()
  const location = useLocation()
  const online = useOnlineStatus()
  const [mobileOpen, setMobileOpen] = useState(false)
  const title = location.pathname.startsWith('/knowledge-bases')
    ? '知识库'
    : location.pathname.startsWith('/chat')
      ? '制度问答'
      : location.pathname.startsWith('/drafts')
        ? '我的草稿'
        : location.pathname.startsWith('/faq-review')
          ? 'FAQ 审核'
          : location.pathname.startsWith('/evaluation')
            ? '评估中心'
            : location.pathname.startsWith('/admin/audit')
              ? '审计日志'
              : location.pathname.startsWith('/admin/skills')
                ? 'Skill 管理'
                : location.pathname.startsWith('/admin/integrations')
                  ? 'MCP 集成'
                  : location.pathname.startsWith('/admin/model-settings')
                    ? '模型设置'
        : titles[location.pathname] ?? 'PolicyFlow AI'
  const canManageUsers = Boolean(user && hasAnyRole(user.roles, ['sys_admin']) && canCallApi('users'))
  const canBrowseKnowledgeBases = canCallApi('knowledgeBases')
  const canChat = canCallApi('chat') && canCallApi('feedback')
  const canUseDrafts = canCallApi('drafts')
  const canReviewFAQ = Boolean(user && hasAnyRole(user.roles, ['kb_admin', 'sys_admin']) && canCallApi('faq'))
  const canEvaluate = Boolean(user && hasAnyRole(user.roles, ['kb_admin', 'sys_admin']) && canCallApi('eval'))
  const canViewAudit = Boolean(user && hasAnyRole(user.roles, ['sys_admin']) && canCallApi('audit'))
  const canManageSkills = Boolean(user && hasAnyRole(user.roles, ['sys_admin']) && canCallApi('skills') && canCallApi('tools'))
  const canManageIntegrations = Boolean(user && hasAnyRole(user.roles, ['sys_admin']) && canCallApi('mcp'))
  const canManageModelSettings = Boolean(user && hasAnyRole(user.roles, ['sys_admin']) && canCallApi('modelSettings'))

  useEffect(() => { clearReturnTo(window.sessionStorage) }, [])
  useEffect(() => {
    if (!mobileOpen) return
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setMobileOpen(false) }
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [mobileOpen])

  const sidebar = (
    <div className="flex h-full flex-col bg-slate-950 text-white">
      <div className="flex min-h-16 items-center gap-[var(--space-3)] border-b border-slate-800 px-[var(--space-4)]">
        <ShieldCheck aria-hidden="true" className="size-6 text-blue-400" />
        <span className="font-semibold">PolicyFlow AI</span>
      </div>
      <nav aria-label="主导航" className="flex-1 p-[var(--space-4)]">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">工作台</p>
        {canChat ? <Link to="/chat" className={location.pathname.startsWith('/chat') ? 'mt-[var(--space-3)] block rounded-md bg-blue-600 px-[var(--space-3)] py-[var(--space-2)] text-sm font-semibold text-white' : 'mt-[var(--space-3)] block rounded-md px-[var(--space-3)] py-[var(--space-2)] text-sm text-slate-300 hover:bg-slate-900'}>制度问答</Link> : null}
        {canUseDrafts ? <Link to="/drafts" className={location.pathname.startsWith('/drafts') ? 'mt-[var(--space-2)] block rounded-md bg-blue-600 px-[var(--space-3)] py-[var(--space-2)] text-sm font-semibold text-white' : 'mt-[var(--space-2)] block rounded-md px-[var(--space-3)] py-[var(--space-2)] text-sm text-slate-300 hover:bg-slate-900'}>我的草稿</Link> : null}
        <p className="mt-[var(--space-5)] text-xs font-semibold uppercase tracking-wide text-slate-400">知识管理</p>
        {canBrowseKnowledgeBases ? <Link to="/knowledge-bases" className={location.pathname.startsWith('/knowledge-bases') ? 'mt-[var(--space-3)] block rounded-md bg-blue-600 px-[var(--space-3)] py-[var(--space-2)] text-sm font-semibold text-white' : 'mt-[var(--space-3)] block rounded-md px-[var(--space-3)] py-[var(--space-2)] text-sm text-slate-300 hover:bg-slate-900'}>知识库</Link> : null}
        {canReviewFAQ ? <Link to="/faq-review" className={location.pathname.startsWith('/faq-review') ? 'mt-2 block rounded-md bg-blue-600 px-3 py-2 text-sm font-semibold text-white' : 'mt-2 block rounded-md px-3 py-2 text-sm text-slate-300 hover:bg-slate-900'}>FAQ 审核</Link> : null}
        {(canEvaluate || canViewAudit) ? <p className="mt-5 text-xs font-semibold uppercase tracking-wide text-slate-400">质量与运维</p> : null}
        {canEvaluate ? <Link to="/evaluation" className={location.pathname.startsWith('/evaluation') ? 'mt-3 block rounded-md bg-blue-600 px-3 py-2 text-sm font-semibold text-white' : 'mt-3 block rounded-md px-3 py-2 text-sm text-slate-300 hover:bg-slate-900'}>评估中心</Link> : null}
        {canViewAudit ? <Link to="/admin/audit" className={location.pathname.startsWith('/admin/audit') ? 'mt-2 block rounded-md bg-blue-600 px-3 py-2 text-sm font-semibold text-white' : 'mt-2 block rounded-md px-3 py-2 text-sm text-slate-300 hover:bg-slate-900'}>审计日志</Link> : null}
        {(canManageUsers || canManageSkills || canManageIntegrations || canManageModelSettings) ? <p className="mt-5 text-xs font-semibold uppercase tracking-wide text-slate-400">系统管理</p> : null}
        {canManageUsers ? <Link to="/admin/users" className={location.pathname === '/admin/users' ? 'mt-[var(--space-2)] block rounded-md bg-blue-600 px-[var(--space-3)] py-[var(--space-2)] text-sm font-semibold text-white' : 'mt-[var(--space-2)] block rounded-md px-[var(--space-3)] py-[var(--space-2)] text-sm text-slate-300 hover:bg-slate-900'}>用户管理</Link> : null}
        {canManageSkills ? <Link to="/admin/skills" className={location.pathname.startsWith('/admin/skills') ? 'mt-2 block rounded-md bg-blue-600 px-3 py-2 text-sm font-semibold text-white' : 'mt-2 block rounded-md px-3 py-2 text-sm text-slate-300 hover:bg-slate-900'}>Skill 管理</Link> : null}
        {canManageIntegrations ? <Link to="/admin/integrations" className={location.pathname.startsWith('/admin/integrations') ? 'mt-2 block rounded-md bg-blue-600 px-3 py-2 text-sm font-semibold text-white' : 'mt-2 block rounded-md px-3 py-2 text-sm text-slate-300 hover:bg-slate-900'}>MCP 集成</Link> : null}
        {canManageModelSettings ? <Link to="/admin/model-settings" className={location.pathname.startsWith('/admin/model-settings') ? 'mt-2 block rounded-md bg-blue-600 px-3 py-2 text-sm font-semibold text-white' : 'mt-2 block rounded-md px-3 py-2 text-sm text-slate-300 hover:bg-slate-900'}>模型设置</Link> : null}
        {!canChat && !canUseDrafts && !canBrowseKnowledgeBases && !canReviewFAQ && !canEvaluate && !canViewAudit && !canManageUsers && !canManageSkills && !canManageIntegrations && !canManageModelSettings ? <p className="mt-[var(--space-3)] rounded-md border border-slate-800 p-[var(--space-3)] text-xs leading-[18px] text-slate-400">当前没有已开放的业务模块。</p> : null}
      </nav>
      <div className="border-t border-slate-800 p-[var(--space-4)] text-xs text-slate-400">PolicyFlow AI · F7</div>
    </div>
  )

  return (
    <div className="min-h-screen bg-[var(--color-background)] text-[var(--color-text-primary)]">
      <a href="#main-content" className="sr-only z-50 rounded-md bg-white px-4 py-2 font-semibold text-[var(--color-primary)] shadow focus:not-sr-only focus:fixed focus:left-4 focus:top-4">跳到主要内容</a>
      {!online ? <div role="status" className="flex min-h-10 items-center justify-center gap-[var(--space-2)] bg-amber-50 px-[var(--space-4)] text-sm text-[var(--color-warning)]"><WifiOff aria-hidden="true" className="size-4" />网络已断开，现有内容将保留，恢复后可重试。</div> : null}
      <aside className="fixed inset-y-0 left-0 hidden w-64 lg:block">{sidebar}</aside>
      {mobileOpen ? <div className="fixed inset-0 z-40 lg:hidden"><button aria-label="关闭导航" className="absolute inset-0 bg-slate-950/50" onClick={() => setMobileOpen(false)} /><aside className="relative h-full w-72 shadow-xl">{sidebar}</aside></div> : null}
      <div className="lg:pl-64">
        <header className="sticky top-0 z-30 flex min-h-16 items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-surface)] px-[var(--space-4)] lg:px-[var(--space-6)]">
          <div className="flex items-center gap-[var(--space-3)]">
            <button type="button" className="inline-flex size-10 items-center justify-center rounded-md border border-[var(--color-border)] lg:hidden" aria-label={mobileOpen ? '关闭导航' : '打开导航'} aria-expanded={mobileOpen} onClick={() => setMobileOpen((value) => !value)}>{mobileOpen ? <X aria-hidden="true" className="size-5" /> : <Menu aria-hidden="true" className="size-5" />}</button>
            <div><p className="text-xs text-[var(--color-text-secondary)]">PolicyFlow AI / {title}</p><h1 className="text-lg font-semibold leading-7">{title}</h1></div>
          </div>
          <div className="flex items-center gap-[var(--space-3)]">
            <div className="hidden text-right sm:block"><p className="text-sm font-semibold">{user?.displayName}</p><p className="text-xs text-[var(--color-text-secondary)]">{user?.roles.join('、')}</p></div>
            <UserCircle aria-hidden="true" className="size-7 text-[var(--color-text-secondary)]" />
            <Button className="bg-white text-[var(--color-text-primary)] ring-1 ring-[var(--color-border)] hover:bg-slate-50" onClick={logout}>退出登录</Button>
          </div>
        </header>
        <main id="main-content" tabIndex={-1} className="p-[var(--space-4)] lg:p-[var(--space-8)]"><Outlet /></main>
      </div>
      <div id="toast-root" aria-live="polite" aria-atomic="true" />
      <div id="dialog-root" />
    </div>
  )
}

