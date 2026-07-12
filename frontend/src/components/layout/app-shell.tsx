import { Menu, ShieldCheck, UserCircle, WifiOff, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, Outlet, useLocation } from 'react-router-dom'
import { canCallApi } from '../../api/readiness'
import { hasAnyRole } from '../../auth/permissions'
import { clearReturnTo } from '../../auth/auth-storage'
import { useAuth } from '../../auth/use-auth'
import { Button } from '../ui/button'

function titleFor(pathname: string): string {
  if (pathname.startsWith('/knowledge-bases')) return '知识库'
  if (pathname.startsWith('/chat')) return '制度问答'
  if (pathname.startsWith('/drafts')) return '我的草稿'
  if (pathname.startsWith('/faq-review')) return 'FAQ 审核'
  if (pathname.startsWith('/evaluation')) return '评估中心'
  if (pathname.startsWith('/admin/audit')) return '审计日志'
  if (pathname.startsWith('/admin/skills')) return 'Skill 管理'
  if (pathname.startsWith('/admin/integrations')) return 'MCP 集成'
  if (pathname.startsWith('/admin/model-settings')) return '模型设置'
  if (pathname === '/admin/users') return '用户管理'
  if (pathname === '/') return '工作台'
  return 'PolicyFlow AI'
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

function NavLink({ to, href, children, active, onClick }: {
  to?: string
  href?: string
  children: React.ReactNode
  active: boolean
  onClick?: () => void
}) {
  const base = 'block rounded-md px-[var(--space-3)] py-[var(--space-2)] text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary)]'
  const activeStyle = 'bg-[var(--color-primary)] font-semibold text-[var(--color-primary-foreground)]'
  const inactiveStyle = 'text-[var(--color-text-secondary)] hover:bg-[var(--color-background-soft)]'
  const classes = `${base} ${active ? activeStyle : inactiveStyle}`
  if (to) return <Link to={to} className={classes}>{children}</Link>
  if (href) return <a href={href} className={classes} onClick={onClick}>{children}</a>
  return null
}

export function AppShell() {
  const { user, logout } = useAuth()
  const location = useLocation()
  const online = useOnlineStatus()
  const [mobileOpen, setMobileOpen] = useState(false)
  const title = titleFor(location.pathname)

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
    <div className="flex h-full flex-col bg-[var(--color-sidebar-bg)] text-[var(--color-sidebar-text)]">
      <div className="flex min-h-16 items-center gap-[var(--space-3)] border-b border-[var(--color-sidebar-border)] px-[var(--space-4)]">
        <ShieldCheck aria-hidden="true" className="size-6 text-[var(--color-primary)]" />
        <span className="font-semibold text-[var(--color-text-primary)]">PolicyFlow AI</span>
      </div>
      <nav aria-label="主导航" className="flex-1 p-[var(--space-4)]">
        <p className="text-xs font-semibold uppercase tracking-wide text-[var(--color-sidebar-text-muted)]">工作台</p>
        {canChat ? <NavLink to="/chat" active={location.pathname.startsWith('/chat')}>制度问答</NavLink> : null}
        {canUseDrafts ? <NavLink to="/drafts" active={location.pathname.startsWith('/drafts')}>我的草稿</NavLink> : null}
        <p className="mt-[var(--space-5)] text-xs font-semibold uppercase tracking-wide text-[var(--color-sidebar-text-muted)]">知识管理</p>
        {canBrowseKnowledgeBases ? <NavLink to="/knowledge-bases" active={location.pathname.startsWith('/knowledge-bases')}>知识库</NavLink> : null}
        {canReviewFAQ ? <NavLink to="/faq-review" active={location.pathname.startsWith('/faq-review')}>FAQ 审核</NavLink> : null}
        {(canEvaluate || canViewAudit) ? <p className="mt-[var(--space-5)] text-xs font-semibold uppercase tracking-wide text-[var(--color-sidebar-text-muted)]">质量与运维</p> : null}
        {canEvaluate ? <NavLink to="/evaluation" active={location.pathname.startsWith('/evaluation')}>评估中心</NavLink> : null}
        {canViewAudit ? <NavLink to="/admin/audit" active={location.pathname.startsWith('/admin/audit')}>审计日志</NavLink> : null}
        {(canManageUsers || canManageSkills || canManageIntegrations || canManageModelSettings) ? <p className="mt-[var(--space-5)] text-xs font-semibold uppercase tracking-wide text-[var(--color-sidebar-text-muted)]">系统管理</p> : null}
        {canManageUsers ? <NavLink to="/admin/users" active={location.pathname === '/admin/users'}>用户管理</NavLink> : null}
        {canManageSkills ? <NavLink to="/admin/skills" active={location.pathname.startsWith('/admin/skills')}>Skill 管理</NavLink> : null}
        {canManageIntegrations ? <NavLink to="/admin/integrations" active={location.pathname.startsWith('/admin/integrations')}>MCP 集成</NavLink> : null}
        {canManageModelSettings ? <NavLink to="/admin/model-settings" active={location.pathname.startsWith('/admin/model-settings')}>模型设置</NavLink> : null}
        {!canChat && !canUseDrafts && !canBrowseKnowledgeBases && !canReviewFAQ && !canEvaluate && !canViewAudit && !canManageUsers && !canManageSkills && !canManageIntegrations && !canManageModelSettings ? <p className="mt-[var(--space-3)] rounded-md border border-[var(--color-sidebar-border)] p-[var(--space-3)] text-xs leading-[18px] text-[var(--color-sidebar-text-muted)]">当前没有已开放的业务模块。</p> : null}
      </nav>
      <div className="border-t border-[var(--color-sidebar-border)] p-[var(--space-4)] text-xs text-[var(--color-sidebar-text-muted)]">PolicyFlow AI</div>
    </div>
  )

  return (
    <div className="min-h-screen bg-[var(--color-background)] text-[var(--color-text-primary)]">
      <a href="#main-content" className="sr-only z-50 rounded-md bg-[var(--color-surface)] px-4 py-2 font-semibold text-[var(--color-primary)] shadow focus:not-sr-only focus:fixed focus:left-4 focus:top-4">跳到主要内容</a>
      {!online ? <div role="status" className="flex min-h-10 items-center justify-center gap-[var(--space-2)] bg-[var(--color-warning-50)] px-[var(--space-4)] text-sm text-[var(--color-warning)]"><WifiOff aria-hidden="true" className="size-4" />网络已断开，现有内容将保留，恢复后可重试。</div> : null}
      <aside className="fixed inset-y-0 left-0 hidden w-64 lg:block">{sidebar}</aside>
      {mobileOpen ? <div className="fixed inset-0 z-40 lg:hidden"><button aria-label="关闭导航" className="absolute inset-0 bg-slate-950/50" onClick={() => setMobileOpen(false)} /><aside className="relative h-full w-72 shadow-xl">{sidebar}</aside></div> : null}
      <div className="lg:pl-64">
        <header className="sticky top-0 z-30 flex min-h-16 items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-surface)] px-[var(--space-4)] lg:px-[var(--space-6)]">
          <div className="flex items-center gap-[var(--space-3)]">
            <button type="button" className="inline-flex size-11 items-center justify-center rounded-md border border-[var(--color-border)] transition-colors hover:bg-[var(--color-background)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary)] lg:hidden" aria-label={mobileOpen ? '关闭导航' : '打开导航'} aria-expanded={mobileOpen} onClick={() => setMobileOpen((value) => !value)}>{mobileOpen ? <X aria-hidden="true" className="size-5" /> : <Menu aria-hidden="true" className="size-5" />}</button>
            <div><p className="text-xs text-[var(--color-text-secondary)]">PolicyFlow AI / {title}</p><h1 className="text-lg font-semibold leading-7">{title}</h1></div>
          </div>
          <div className="flex items-center gap-[var(--space-3)]">
            <div className="hidden text-right sm:block"><p className="text-sm font-semibold">{user?.displayName}</p><p className="text-xs text-[var(--color-text-secondary)]">{user?.roles.join('、')}</p></div>
            <UserCircle aria-hidden="true" className="size-7 text-[var(--color-text-secondary)]" />
            <Button variant="secondary" onClick={logout}>退出登录</Button>
          </div>
        </header>
        <main id="main-content" tabIndex={-1} className="p-[var(--space-4)] lg:p-[var(--space-8)]"><Outlet /></main>
      </div>
      <div id="toast-root" aria-live="polite" aria-atomic="true" />
      <div id="dialog-root" />
    </div>
  )
}
