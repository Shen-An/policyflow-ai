import {
  AuditOutlined,
  BookOutlined,
  ClusterOutlined,
  FileTextOutlined,
  FormOutlined,
  LogoutOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MessageOutlined,
  SettingOutlined,
  TeamOutlined,
  ToolOutlined,
  WifiOutlined,
} from '@ant-design/icons'
import { Avatar, Button, Layout, Menu, Space, Typography } from 'antd'
import type { MenuProps } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { canCallApi } from '../../api/readiness'
import { hasAnyRole } from '../../auth/permissions'
import { clearReturnTo } from '../../auth/auth-storage'
import { useAuth } from '../../auth/use-auth'
import { gradients, palette } from '../../styles/palette'
import { PageTransition } from '../feedback/page-transition'

const { Header, Sider, Content } = Layout

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
  const [collapsed, setCollapsed] = useState(false)

  const canManageUsers = Boolean(user && hasAnyRole(user.roles, ['sys_admin']) && canCallApi('users'))
  const canBrowseKnowledgeBases = canCallApi('knowledgeBases')
  const canChat = canCallApi('chat') && canCallApi('feedback')
  const canUseDrafts = canCallApi('drafts')
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

  const selectedKeys = useMemo(() => {
    const path = location.pathname
    if (path === '/') return ['/']
    if (path.startsWith('/chat')) return ['/chat']
    if (path.startsWith('/drafts')) return ['/drafts']
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
    if (canChat || canUseDrafts) keys.push('work')
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
    canViewAudit,
  ])

  const items: MenuProps['items'] = [
    {
      key: '/',
      icon: <ClusterOutlined />,
      label: '工作台',
    },
    ...(canChat || canUseDrafts
      ? [
          {
            key: 'work',
            label: '日常工作',
            type: 'group' as const,
            children: [
              canChat
                ? { key: '/chat', icon: <MessageOutlined />, label: '制度问答' }
                : null,
              canUseDrafts
                ? { key: '/drafts', icon: <FormOutlined />, label: '我的草稿' }
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
                ? { key: '/knowledge-bases', icon: <BookOutlined />, label: '知识库' }
                : null,
              canReviewFAQ
                ? { key: '/faq-review', icon: <FileTextOutlined />, label: 'FAQ 审核' }
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
                ? { key: '/evaluation', icon: <ToolOutlined />, label: '评估中心' }
                : null,
              canViewAudit
                ? { key: '/admin/audit', icon: <AuditOutlined />, label: '审计日志' }
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
                ? { key: '/admin/users', icon: <TeamOutlined />, label: '用户管理' }
                : null,
              canManageSkills
                ? { key: '/admin/skills', icon: <ToolOutlined />, label: 'Skill 管理' }
                : null,
              canManageIntegrations
                ? { key: '/admin/integrations', icon: <ClusterOutlined />, label: 'MCP 集成' }
                : null,
              canManageModelSettings
                ? {
                    key: '/admin/model-settings',
                    icon: <SettingOutlined />,
                    label: '模型设置',
                  }
                : null,
            ].filter(Boolean),
          },
        ]
      : []),
  ]

  return (
    <Layout style={{ minHeight: '100vh', background: 'transparent' }}>
      <Sider
        collapsible
        collapsed={collapsed}
        trigger={null}
        width={232}
        theme="dark"
        breakpoint="lg"
        onBreakpoint={(broken) => {
          if (broken) setCollapsed(true)
        }}
        style={{
          background: gradients.sider,
        }}
      >
        <Link
          to="/"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            height: 64,
            padding: collapsed ? '0 16px' : '0 20px',
            borderBottom: '1px solid rgba(255,255,255,0.08)',
            color: palette.textOnPrimary,
            textDecoration: 'none',
            background: 'linear-gradient(90deg, rgba(79,70,229,0.18), transparent 70%)',
          }}
        >
          <div
            style={{
              width: 34,
              height: 34,
              borderRadius: 10,
              background: gradients.brandMark,
              display: 'grid',
              placeItems: 'center',
              fontWeight: 700,
              flexShrink: 0,
              boxShadow: '0 8px 18px -8px rgba(99,102,241,0.8)',
            }}
          >
            P
          </div>
          {!collapsed ? (
            <div style={{ minWidth: 0 }}>
              <div style={{ fontWeight: 600, lineHeight: 1.2 }}>PolicyFlow AI</div>
              <div style={{ fontSize: 12, opacity: 0.55 }}>企业制度助手</div>
            </div>
          ) : null}
        </Link>

        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={selectedKeys}
          defaultOpenKeys={openKeys}
          items={items}
          onClick={({ key }) => navigate(key)}
          style={{ borderInlineEnd: 0, marginTop: 8, background: 'transparent' }}
        />
      </Sider>

      <Layout>
        {!online ? (
          <div
            style={{
              background: palette.warningSoft,
              color: palette.warning,
              padding: '8px 16px',
              textAlign: 'center',
              fontSize: 13,
            }}
          >
            <WifiOutlined /> 网络已断开，现有内容将保留，恢复后可重试。
          </div>
        ) : null}

        <Header style={{ justifyContent: 'space-between' }}>
          <Space size={16}>
            <Button
              type="text"
              icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setCollapsed((value) => !value)}
              style={{ color: palette.primaryHover }}
            />
            <div>
              <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
                PolicyFlow AI
              </Typography.Text>
              <Typography.Title level={5} style={{ margin: 0, color: palette.text }}>
                {titleFor(location.pathname)}
              </Typography.Title>
            </div>
          </Space>

          <Space size={12}>
            <div style={{ textAlign: 'right', lineHeight: 1.3 }}>
              <div style={{ fontWeight: 600, color: palette.text }}>{user?.displayName}</div>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {user?.roles.join(' · ')}
              </Typography.Text>
            </div>
            <Avatar
              style={{
                background: gradients.brandMark,
                boxShadow: '0 6px 14px -6px rgba(79,70,229,0.7)',
              }}
            >
              {(user?.displayName ?? 'U').slice(0, 1)}
            </Avatar>
            <Button icon={<LogoutOutlined />} onClick={logout}>
              退出
            </Button>
          </Space>
        </Header>

        <Content>
          <PageTransition>
            <Outlet />
          </PageTransition>
        </Content>
      </Layout>

      <div id="toast-root" aria-live="polite" aria-atomic="true" />
      <div id="dialog-root" />
    </Layout>
  )
}
