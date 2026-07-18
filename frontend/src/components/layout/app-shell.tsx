import { BookOpen, ChatCircle, ClipboardText, Database, FileText, GearSix, List, NotePencil, SidebarSimple, SignOut, SquaresFour, Users, WifiHigh, Wrench } from '@phosphor-icons/react'
import { Avatar, Button, Layout, Menu, Space, Tooltip, Typography } from 'antd'
import type { MenuProps } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { canCallApi } from '../../api/readiness'
import { hasAnyRole } from '../../auth/permissions'
import { clearReturnTo } from '../../auth/auth-storage'
import { useAuth } from '../../auth/use-auth'
import { formatRoles } from '../../lib/labels'
import { gradients } from '../../styles/palette'
import { PageTransition } from '../feedback/page-transition'
import { ThemeToggle } from './theme-toggle'

const { Header, Sider, Content } = Layout

const COLLAPSE_STORAGE_KEY = 'policyflow.shell.sider-collapsed'

function titleFor(pathname: string): string {
  if (pathname.startsWith('/knowledge-bases/') && pathname !== '/knowledge-bases') {
    return '知识库详情'
  }
  if (pathname.startsWith('/knowledge-bases')) return '知识库'
  if (pathname.startsWith('/chat')) return '制度问答'
  if (pathname.startsWith('/drafts/') && pathname !== '/drafts') return '草稿详情'
  if (pathname.startsWith('/drafts')) return '我的草稿'
  if (pathname.startsWith('/memory')) return '我的记忆'
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

function readCollapsedPreference(): boolean {
  try {
    const raw = window.localStorage.getItem(COLLAPSE_STORAGE_KEY)
    if (raw === '1') return true
    if (raw === '0') return false
  } catch {
    // ignore storage errors
  }
  return false
}

function useOnlineStatus(): boolean {
  const [online, setOnline] = useState(() => navigator.onLine)
  useEffect(() => {
    const on = () => setOnline(true)
    const off = () => setOnline(false)
    window.addEventListener('online', on)
    window.addEventListener('offline', off)
    return () => {
      window.removeEventListener('online', on)
      window.removeEventListener('offline', off)
    }
  }, [])
  return online
}

export function AppShell() {
  const { user, logout } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const online = useOnlineStatus()
  const [collapsed, setCollapsed] = useState(() => readCollapsedPreference())
  const [isMobile, setIsMobile] = useState(false)

  const canManageUsers = Boolean(user && hasAnyRole(user.roles, ['sys_admin']) && canCallApi('users'))
  const canBrowseKnowledgeBases = canCallApi('knowledgeBases')
  const canChat = canCallApi('chat') && canCallApi('feedback')
  const canUseDrafts = canCallApi('drafts')
  const canUseMemory = canCallApi('memory')
  const canReviewFAQ = Boolean(user && hasAnyRole(user.roles, ['kb_admin', 'sys_admin']) && canCallApi('faq'))
  const canEvaluate = Boolean(user && hasAnyRole(user.roles, ['kb_admin', 'sys_admin']) && canCallApi('eval'))
  const canViewAudit = Boolean(user && hasAnyRole(user.roles, ['sys_admin']) && canCallApi('audit'))
  const canManageSkills = Boolean(
    user && hasAnyRole(user.roles, ['sys_admin']) && canCallApi('skills') && canCallApi('tools'),
  )
  const canManageIntegrations = Boolean(
    user && hasAnyRole(user.roles, ['sys_admin']) && canCallApi('mcp'),
  )
  const canManageModelSettings = Boolean(
    user && hasAnyRole(user.roles, ['sys_admin']) && canCallApi('modelSettings'),
  )

  useEffect(() => {
    clearReturnTo(window.sessionStorage)
  }, [])

  useEffect(() => {
    try {
      window.localStorage.setItem(COLLAPSE_STORAGE_KEY, collapsed ? '1' : '0')
    } catch {
      // ignore storage errors
    }
  }, [collapsed])

  const selectedKeys = useMemo(() => {
    const path = location.pathname
    if (path === '/') return ['/']
    if (path.startsWith('/chat')) return ['/chat']
    if (path.startsWith('/drafts')) return ['/drafts']
    if (path.startsWith('/memory')) return ['/memory']
    if (path.startsWith('/knowledge-bases')) return ['/knowledge-bases']
    if (path.startsWith('/faq-review')) return ['/faq-review']
    if (path.startsWith('/evaluation')) return ['/evaluation']
    if (path.startsWith('/admin/audit')) return ['/admin/audit']
    if (path.startsWith('/admin/skills')) return ['/admin/skills']
    if (path.startsWith('/admin/integrations')) return ['/admin/integrations']
    if (path.startsWith('/admin/model-settings')) return ['/admin/model-settings']
    if (path === '/admin/users') return ['/admin/users']
    return [path]
  }, [location.pathname])

  const openKeys = useMemo(() => {
    const keys: string[] = []
    if (canChat || canUseDrafts || canUseMemory) keys.push('work')
    if (canBrowseKnowledgeBases || canReviewFAQ) keys.push('knowledge')
    if (canEvaluate || canViewAudit) keys.push('quality')
    if (canManageUsers || canManageSkills || canManageIntegrations || canManageModelSettings) {
      keys.push('admin')
    }
    return keys
  }, [
    canBrowseKnowledgeBases,
    canChat,
    canEvaluate,
    canManageIntegrations,
    canManageModelSettings,
    canManageSkills,
    canManageUsers,
    canReviewFAQ,
    canUseDrafts,
    canUseMemory,
    canViewAudit,
  ])

  const items: MenuProps['items'] = [
    {
      key: '/',
      icon: <SquaresFour size={16} weight="duotone" />,
      label: '工作台',
    },
    ...(canChat || canUseDrafts || canUseMemory
      ? [
          {
            key: 'work',
            label: '日常工作',
            type: 'group' as const,
            children: [
              canChat
                ? { key: '/chat', icon: <ChatCircle size={16} weight="duotone" />, label: '制度问答' }
                : null,
              canUseDrafts
                ? { key: '/drafts', icon: <NotePencil size={16} weight="duotone" />, label: '我的草稿' }
                : null,
              canUseMemory
                ? { key: '/memory', icon: <Database size={16} weight="duotone" />, label: '我的记忆' }
                : null,
            ].filter(Boolean),
          },
        ]
      : []),
    ...(canBrowseKnowledgeBases || canReviewFAQ
      ? [
          {
            key: 'knowledge',
            label: '知识管理',
            type: 'group' as const,
            children: [
              canBrowseKnowledgeBases
                ? { key: '/knowledge-bases', icon: <BookOpen size={16} weight="duotone" />, label: '知识库' }
                : null,
              canReviewFAQ
                ? { key: '/faq-review', icon: <FileText size={16} weight="duotone" />, label: 'FAQ 审核' }
                : null,
            ].filter(Boolean),
          },
        ]
      : []),
    ...(canEvaluate || canViewAudit
      ? [
          {
            key: 'quality',
            label: '质量与运维',
            type: 'group' as const,
            children: [
              canEvaluate
                ? { key: '/evaluation', icon: <Wrench size={16} weight="duotone" />, label: '评估中心' }
                : null,
              canViewAudit
                ? { key: '/admin/audit', icon: <ClipboardText size={16} weight="duotone" />, label: '审计日志' }
                : null,
            ].filter(Boolean),
          },
        ]
      : []),
    ...(canManageUsers || canManageSkills || canManageIntegrations || canManageModelSettings
      ? [
          {
            key: 'admin',
            label: '系统管理',
            type: 'group' as const,
            children: [
              canManageUsers
                ? { key: '/admin/users', icon: <Users size={16} weight="duotone" />, label: '用户管理' }
                : null,
              canManageSkills
                ? { key: '/admin/skills', icon: <Wrench size={16} weight="duotone" />, label: 'Skill 管理' }
                : null,
              canManageIntegrations
                ? { key: '/admin/integrations', icon: <SquaresFour size={16} weight="duotone" />, label: 'MCP 集成' }
                : null,
              canManageModelSettings
                ? {
                    key: '/admin/model-settings',
                    icon: <GearSix size={16} weight="duotone" />,
                    label: '模型设置',
                  }
                : null,
            ].filter(Boolean),
          },
        ]
      : []),
  ]

  const roleText = formatRoles(user?.roles)

  return (
    <Layout
      className={collapsed ? 'pf-shell pf-shell--collapsed' : 'pf-shell'}
      style={{ minHeight: '100dvh', background: 'transparent' }}
    >
      <Sider
        collapsible
        collapsed={collapsed}
        trigger={null}
        width={236}
        collapsedWidth={isMobile ? 0 : 68}
        theme="light"
        breakpoint="md"
        onBreakpoint={(broken) => {
          setIsMobile(broken)
          // Only auto-collapse on true mobile; desktop keeps user preference.
          if (broken) setCollapsed(true)
        }}
        style={{
          background: 'var(--color-sidebar-bg)',
          borderRight: '1px solid var(--color-sidebar-border)',
        }}
      >
        <Link
          to="/"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            height: 56,
            padding: collapsed ? '0 16px' : '0 18px',
            borderBottom: '1px solid var(--color-sidebar-border)',
            color: 'var(--color-text-primary)',
            textDecoration: 'none',
          }}
        >
          <div
            style={{
              width: 30,
              height: 30,
              borderRadius: 9,
              background: gradients.brandMark,
              color: '#ffffff',
              display: 'grid',
              placeItems: 'center',
              fontWeight: 700,
              fontSize: 14,
              flexShrink: 0,
              boxShadow: '0 6px 14px -10px rgba(15,154,116,0.35)',
            }}
          >
            P
          </div>
          {!collapsed ? (
            <div style={{ minWidth: 0 }}>
              <div
                style={{
                  fontWeight: 650,
                  lineHeight: 1.2,
                  color: 'var(--color-text-primary)',
                  letterSpacing: '-0.02em',
                }}
              >
                PolicyFlow
              </div>
              <div
                style={{
                  fontSize: 11,
                  color: 'var(--color-sidebar-text-muted)',
                  marginTop: 2,
                }}
              >
                企业制度助手
              </div>
            </div>
          ) : null}
        </Link>

        <Menu
          theme="light"
          mode="inline"
          selectedKeys={selectedKeys}
          defaultOpenKeys={openKeys}
          items={items}
          onClick={({ key }) => navigate(key)}
          style={{
            borderInlineEnd: 0,
            marginTop: 8,
            paddingInline: 4,
            background: 'transparent',
          }} />
      </Sider>

      <Layout style={{ background: 'transparent', minWidth: 0 }}>
        {!online ? (
          <div
            style={{
              background: 'var(--color-warning-50)',
              color: 'var(--color-warning)',
              padding: '8px 16px',
              textAlign: 'center',
              fontSize: 13,
            }}
          >
            <WifiHigh size={16} weight="duotone" aria-hidden /> 网络已断开，现有内容将保留，恢复后可重试。
          </div>
        ) : null}

        <Header style={{ justifyContent: 'space-between' }}>
          <Space size={8}>
            <Button
              type="text"
              icon={
                collapsed ? (
                  <List size={16} weight="regular" aria-hidden />
                ) : (
                  <SidebarSimple size={16} weight="regular" aria-hidden />
                )
              }
              onClick={() => setCollapsed((value) => !value)}
              style={{ color: 'var(--color-text-secondary)' }}
              aria-label={collapsed ? '展开侧栏' : '收起侧栏'} />
            <Typography.Text
              style={{
                margin: 0,
                color: 'var(--color-text-primary)',
                fontWeight: 600,
                fontSize: 15,
                letterSpacing: '-0.01em',
              }}
            >
              {titleFor(location.pathname)}
            </Typography.Text>
          </Space>

          <Space size={8}>
            <ThemeToggle />
            <div style={{ textAlign: 'right', lineHeight: 1.25, maxWidth: 160 }}>
              <div
                style={{
                  fontWeight: 600,
                  color: 'var(--color-text-primary)',
                  fontSize: 13,
                }}
              >
                {user?.displayName}
              </div>
              <Tooltip title={roleText}>
                <Typography.Text type="secondary" style={{ fontSize: 11 }} ellipsis>
                  {roleText}
                </Typography.Text>
              </Tooltip>
            </div>
            <Avatar
              size={32}
              style={{
                background: gradients.brandMark,
                fontSize: 13,
                fontWeight: 600,
                color: '#ffffff',
              }}
            >
              {(user?.displayName ?? 'U').slice(0, 1)}
            </Avatar>
            <Button
              type="text"
              icon={<SignOut size={16} weight="duotone" aria-hidden />}
              onClick={logout}
              aria-label="退出登录"
            >
              退出
            </Button>
          </Space>
        </Header>

        <Content>
          <div className="pf-content-frame">
            <PageTransition>
              <Outlet />
            </PageTransition>
          </div>
        </Content>
      </Layout>

      <div id="toast-root" aria-live="polite" aria-atomic="true" />
      <div id="dialog-root" />
    </Layout>
  )
}
