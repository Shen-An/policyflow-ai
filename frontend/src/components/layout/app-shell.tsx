import {
  BookOpen,
  ChatCircle,
  ClipboardText,
  Database,
  FileText,
  FolderOpen,
  List,
  Scales,
  SidebarSimple,
  SignOut,
  SlidersHorizontal,
  SquaresFour,
  Users,
  Wrench,
} from '@phosphor-icons/react'
import type { ReactNode } from 'react'
import { useEffect, useRef, useState } from 'react'
import { Link, Outlet, useLocation } from 'react-router-dom'
import { hasAnyRole } from '../../auth/permissions'
import { useAuth } from '../../auth/use-auth'
import { formatRoles } from '../../lib/labels'
import { OfflineState, RecoveryState } from '../../design-system/states'
import { WorkflowProvider } from '../../features/workspace/workflow-context'
import { ThemeToggle } from './theme-toggle'
import '../../design-system/tokens.css'

const COLLAPSE_STORAGE_KEY = 'policyflow.shell.sider-collapsed'
const MOBILE_BREAKPOINT = 900

type NavLink = { to: string; testid: string; label: string; icon: ReactNode }

const TITLES: Array<[string, string]> = [
  ['/chat', '制度问答'],
  ['/knowledge', '知识库'],
  ['/knowledge-bases', '知识库'],
  ['/memory', '我的记忆'],
  ['/workspace', '报销工作区'],
  ['/workflow', '报销流程'],
  ['/approval', '待我审批'],
  ['/drafts', '我的草稿'],
  ['/faq-review', 'FAQ 审核'],
  ['/evaluation', '评估中心'],
  ['/admin/users', '用户管理'],
  ['/admin/audit', '审计日志'],
  ['/admin/skills', 'Skill 管理'],
  ['/admin/integrations', 'MCP 集成'],
  ['/admin/model-settings', '模型设置'],
]

function titleFor(pathname: string): string {
  if (pathname === '/') return '工作台'
  const hit = TITLES.find(([prefix]) => pathname.startsWith(prefix))
  return hit ? hit[1] : 'PolicyFlow AI'
}

function readCollapsedPreference(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSE_STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

function useOnlineStatus(): { online: boolean; recovered: boolean; dismissRecovered: () => void } {
  const [online, setOnline] = useState(() => (typeof navigator === 'undefined' ? true : navigator.onLine))
  const [recovered, setRecovered] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    const clearTimer = () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current)
        timerRef.current = null
      }
    }
    const goOffline = () => {
      setOnline(false)
      setRecovered(false)
      clearTimer()
    }
    const goOnline = () => {
      setOnline(true)
      setRecovered(true)
      clearTimer()
      // The "connection restored" banner is transient — auto-dismiss so it never lingers.
      timerRef.current = setTimeout(() => setRecovered(false), 4000)
    }
    window.addEventListener('online', goOnline)
    window.addEventListener('offline', goOffline)
    return () => {
      window.removeEventListener('online', goOnline)
      window.removeEventListener('offline', goOffline)
      clearTimer()
    }
  }, [])
  return { online, recovered, dismissRecovered: () => setRecovered(false) }
}

function NavItem({ link, active, collapsed }: { link: NavLink; active: boolean; collapsed: boolean }) {
  return (
    <li>
      <Link
        to={link.to}
        data-testid={link.testid}
        aria-current={active ? 'page' : undefined}
        title={collapsed ? link.label : undefined}
        className="ds-focusable"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          padding: collapsed ? '10px' : '9px 12px',
          margin: '2px 8px',
          borderRadius: 'var(--ds-radius-md)',
          color: active ? 'var(--ds-accent-text)' : 'var(--ds-text-secondary)',
          background: active ? 'var(--ds-sidebar-active)' : 'transparent',
          fontWeight: active ? 600 : 500,
          fontSize: 'var(--ds-text-base)',
          textDecoration: 'none',
          justifyContent: collapsed ? 'center' : 'flex-start',
          transition: 'background var(--ds-motion-fast) var(--ds-ease)',
        }}
      >
        <span aria-hidden style={{ display: 'grid', placeItems: 'center', flexShrink: 0 }}>
          {link.icon}
        </span>
        {!collapsed ? <span style={{ minWidth: 0 }}>{link.label}</span> : null}
      </Link>
    </li>
  )
}

export function AppShell() {
  const { user, logout } = useAuth()
  const location = useLocation()
  const { online, recovered, dismissRecovered } = useOnlineStatus()
  const [collapsed, setCollapsed] = useState(() => readCollapsedPreference())

  useEffect(() => {
    const onResize = () => {
      if (window.innerWidth < MOBILE_BREAKPOINT) setCollapsed(true)
    }
    onResize()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  useEffect(() => {
    try {
      window.localStorage.setItem(COLLAPSE_STORAGE_KEY, collapsed ? '1' : '0')
    } catch {
      // ignore storage errors
    }
  }, [collapsed])

  const isAdmin = Boolean(user && hasAnyRole(user.roles, ['sys_admin']))
  const isReviewer = Boolean(user && hasAnyRole(user.roles, ['kb_admin', 'sys_admin']))

  const primaryLinks: NavLink[] = [
    { to: '/chat', testid: 'nav-chat', label: '制度问答', icon: <ChatCircle size={18} weight="duotone" /> },
    { to: '/knowledge', testid: 'nav-knowledge', label: '知识库', icon: <BookOpen size={18} weight="duotone" /> },
    { to: '/memory', testid: 'nav-memory', label: '我的记忆', icon: <Database size={18} weight="duotone" /> },
    { to: '/workspace', testid: 'nav-workspace', label: '报销工作区', icon: <FolderOpen size={18} weight="duotone" /> },
    { to: '/approval', testid: 'nav-approval', label: '待我审批', icon: <Scales size={18} weight="duotone" /> },
  ]

  const adminLinks: NavLink[] = [
    ...(isReviewer
      ? [{ to: '/faq-review', testid: 'nav-faq', label: 'FAQ 审核', icon: <FileText size={18} weight="duotone" /> }]
      : []),
    ...(isReviewer
      ? [{ to: '/evaluation', testid: 'nav-evaluation', label: '评估中心', icon: <Wrench size={18} weight="duotone" /> }]
      : []),
    ...(isAdmin
      ? [
          { to: '/admin/users', testid: 'nav-users', label: '用户管理', icon: <Users size={18} weight="duotone" /> },
          { to: '/admin/audit', testid: 'nav-audit', label: '审计日志', icon: <ClipboardText size={18} weight="duotone" /> },
          { to: '/admin/skills', testid: 'nav-skills', label: 'Skill 管理', icon: <Wrench size={18} weight="duotone" /> },
          { to: '/admin/integrations', testid: 'nav-integrations', label: 'MCP 集成', icon: <SquaresFour size={18} weight="duotone" /> },
          { to: '/admin/model-settings', testid: 'nav-model-settings', label: '模型设置', icon: <SlidersHorizontal size={18} weight="duotone" /> },
        ]
      : []),
  ]

  const isActive = (to: string): boolean =>
    to === '/' ? location.pathname === '/' : location.pathname.startsWith(to)

  const roleText = formatRoles(user?.roles)
  const sidebarWidth = collapsed ? 'var(--ds-sidebar-collapsed)' : 'var(--ds-sidebar-width)'

  return (
    <div
      data-testid="app-shell"
      style={{
        display: 'flex',
        height: '100dvh',
        overflow: 'hidden',
        background: 'var(--ds-canvas)',
        color: 'var(--ds-text)',
        fontFamily: 'var(--ds-font-sans)',
      }}
    >
      <aside
        style={{
          width: sidebarWidth,
          flexShrink: 0,
          background: 'var(--ds-sidebar)',
          borderRight: '1px solid var(--ds-border)',
          display: 'flex',
          flexDirection: 'column',
          transition: 'width var(--ds-motion-base) var(--ds-ease)',
        }}
      >
        <Link
          to="/"
          data-testid="nav-home"
          className="ds-focusable"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            height: 'var(--ds-header-height)',
            padding: collapsed ? '0 16px' : '0 18px',
            borderBottom: '1px solid var(--ds-border)',
            textDecoration: 'none',
            color: 'var(--ds-text)',
          }}
        >
          <span
            aria-hidden
            style={{
              width: 30,
              height: 30,
              borderRadius: 9,
              background: 'linear-gradient(145deg, #1fb888, #0f8f6c)',
              color: '#fff',
              display: 'grid',
              placeItems: 'center',
              fontWeight: 700,
              flexShrink: 0,
            }}
          >
            P
          </span>
          {!collapsed ? (
            <span style={{ fontWeight: 650, letterSpacing: '-0.02em' }}>PolicyFlow</span>
          ) : null}
        </Link>

        <nav aria-label="主导航" style={{ flex: 1, overflowY: 'auto', paddingTop: 8 }}>
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {primaryLinks.map((link) => (
              <NavItem key={link.to} link={link} active={isActive(link.to)} collapsed={collapsed} />
            ))}
          </ul>
          {adminLinks.length > 0 ? (
            <div data-testid="nav-admin" style={{ marginTop: 12 }}>
              {!collapsed ? (
                <div
                  style={{
                    padding: '8px 20px 4px',
                    fontSize: 'var(--ds-text-xs)',
                    fontWeight: 600,
                    letterSpacing: '0.04em',
                    color: 'var(--ds-text-muted)',
                    textTransform: 'uppercase',
                  }}
                >
                  管理
                </div>
              ) : (
                <div style={{ height: 1, background: 'var(--ds-divider)', margin: '8px 12px' }} />
              )}
              <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                {adminLinks.map((link) => (
                  <NavItem key={link.to} link={link} active={isActive(link.to)} collapsed={collapsed} />
                ))}
              </ul>
            </div>
          ) : null}
        </nav>
      </aside>

      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        <header
          style={{
            height: 'var(--ds-header-height)',
            flexShrink: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '0 20px',
            background: 'var(--ds-card)',
            borderBottom: '1px solid var(--ds-border)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <button
              type="button"
              onClick={() => setCollapsed((value) => !value)}
              aria-label={collapsed ? '展开侧栏' : '收起侧栏'}
              className="ds-focusable"
              style={{
                border: 'none',
                background: 'transparent',
                color: 'var(--ds-text-secondary)',
                cursor: 'pointer',
                display: 'grid',
                placeItems: 'center',
                width: 34,
                height: 34,
                borderRadius: 'var(--ds-radius-sm)',
              }}
            >
              {collapsed ? <List size={18} /> : <SidebarSimple size={18} />}
            </button>
            <h1 style={{ margin: 0, fontSize: 'var(--ds-text-md)', fontWeight: 650, color: 'var(--ds-text)' }}>
              {titleFor(location.pathname)}
            </h1>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <ThemeToggle />
            <div style={{ textAlign: 'right', lineHeight: 1.25, maxWidth: 180 }}>
              <div style={{ fontWeight: 600, fontSize: 'var(--ds-text-sm)', color: 'var(--ds-text)' }}>
                {user?.displayName}
              </div>
              <div
                style={{ fontSize: 'var(--ds-text-xs)', color: 'var(--ds-text-secondary)' }}
                title={roleText}
              >
                {roleText}
              </div>
            </div>
            <button
              type="button"
              data-testid="shell-logout"
              onClick={logout}
              aria-label="退出登录"
              className="ds-focusable"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                border: '1px solid var(--ds-border)',
                background: 'var(--ds-card)',
                color: 'var(--ds-text-secondary)',
                cursor: 'pointer',
                padding: '6px 12px',
                borderRadius: 'var(--ds-radius-sm)',
                fontSize: 'var(--ds-text-sm)',
              }}
            >
              <SignOut size={16} aria-hidden /> 退出
            </button>
          </div>
        </header>

        <div aria-live="polite" style={{ padding: online && !recovered ? 0 : undefined }}>
          {!online ? (
            <div style={{ borderBottom: '1px solid var(--ds-border)' }}>
              <OfflineState variant="inline" />
            </div>
          ) : recovered ? (
            <div style={{ borderBottom: '1px solid var(--ds-border)' }}>
              <RecoveryState variant="inline" onRetry={dismissRecovered} />
            </div>
          ) : null}
        </div>

        <main
          style={{
            flex: 1,
            minHeight: 0,
            overflow: 'auto',
            padding: 'var(--ds-space-6)',
          }}
        >
          <div style={{ maxWidth: 'var(--ds-content-max)', margin: '0 auto', height: '100%' }}>
            <WorkflowProvider>
              <Outlet />
            </WorkflowProvider>
          </div>
        </main>
      </div>

      <div id="toast-root" aria-live="polite" aria-atomic="true" />
      <div id="dialog-root" />
    </div>
  )
}
