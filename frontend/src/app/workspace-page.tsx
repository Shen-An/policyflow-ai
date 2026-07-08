import { ArrowRight, BookOpen, ChartBar, ChatCircle, CheckCircle, CircleNotch, FileText, XCircle } from '@phosphor-icons/react'
import { useQuery } from '@tanstack/react-query'
import { Card, Col, Row, Space, Statistic, Tag, Typography } from 'antd'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { apiClient } from '../api/client'
import { listConversations } from '../api/chat'
import { listDrafts } from '../api/drafts'
import { listEvalRuns } from '../api/eval'
import { listKnowledgeBases } from '../api/knowledge-bases'
import { canCallApi } from '../api/readiness'
import { hasAnyRole } from '../auth/permissions'
import { useAuth } from '../auth/use-auth'
import { LoadingState } from '../components/feedback/state-views'
import { conversationKeys } from '../features/chat/queries'
import { draftKeys } from '../features/drafts/queries'
import { evalKeys } from '../features/evaluation/queries'
import { knowledgeBaseKeys } from '../features/knowledge-bases/queries'
import { palette } from '../styles/palette'

const { Title, Paragraph, Text } = Typography

type ActivityItem = {
  key: string
  action: string
  detail: string
  time: string
  href?: string
}

function formatRelativeTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const diffMs = Date.now() - date.getTime()
  const minutes = Math.floor(diffMs / 60_000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days} 天前`
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
  }).format(date)
}

function useHealthQuery() {
  return useQuery({
    queryKey: ['health'],
    queryFn: ({ signal }) =>
      apiClient.request<{ status: string }>('/health', { signal }),
    refetchInterval: 60_000,
    retry: 1,
  })
}

export function WorkspacePage() {
  const { user } = useAuth()
  const hour = new Date().getHours()
  const greeting =
    hour < 6 ? '夜深了' : hour < 12 ? '早上好' : hour < 18 ? '下午好' : '晚上好'

  const canBrowseKnowledgeBases = canCallApi('knowledgeBases')
  const canChat = canCallApi('chat') && canCallApi('feedback')
  const canUseDrafts = canCallApi('drafts')
  const canEvaluate = Boolean(
    user && hasAnyRole(user.roles, ['kb_admin', 'sys_admin']) && canCallApi('eval'),
  )

  const health = useHealthQuery()
  const knowledgeBases = useQuery({
    queryKey: knowledgeBaseKeys.list(),
    queryFn: ({ signal }) => listKnowledgeBases(signal),
    enabled: canBrowseKnowledgeBases,
  })
  const conversations = useQuery({
    queryKey: conversationKeys.list(1, 5, ''),
    queryFn: ({ signal }) => listConversations(1, 5, '', signal),
    enabled: canChat,
  })
  const drafts = useQuery({
    queryKey: draftKeys.list(1, 5, '', ''),
    queryFn: ({ signal }) => listDrafts(1, 5, undefined, undefined, signal),
    enabled: canUseDrafts,
  })
  const evalRuns = useQuery({
    queryKey: evalKeys.runList(1, 5, ''),
    queryFn: ({ signal }) => listEvalRuns(1, 5, undefined, signal),
    enabled: canEvaluate,
  })

  const kbCount = knowledgeBases.data?.length
  const conversationTotal = conversations.data?.total
  const draftTotal = drafts.data?.total
  const draftPending =
    drafts.data?.items.filter((item) => item.status === 'draft').length ?? null
  const evalTotal = evalRuns.data?.total

  const statsLoading =
    (canBrowseKnowledgeBases && knowledgeBases.isPending) ||
    (canChat && conversations.isPending) ||
    (canUseDrafts && drafts.isPending) ||
    (canEvaluate && evalRuns.isPending)

  const activityItems: ActivityItem[] = []
  if (canChat && conversations.data?.items) {
    for (const item of conversations.data.items) {
      activityItems.push({
        key: `conv-${item.id}`,
        action: '制度问答',
        detail: item.title || item.lastMessagePreview || '未命名会话',
        time: item.updatedAt,
        href: `/chat/${item.id}`,
      })
    }
  }
  if (canUseDrafts && drafts.data?.items) {
    for (const item of drafts.data.items) {
      activityItems.push({
        key: `draft-${item.id}`,
        action: '草稿',
        detail: item.title || '未命名草稿',
        time: item.updatedAt,
        href: `/drafts/${item.id}`,
      })
    }
  }
  if (canEvaluate && evalRuns.data?.items) {
    for (const item of evalRuns.data.items) {
      activityItems.push({
        key: `eval-${item.id}`,
        action: '评估 Run',
        detail: item.name,
        time: item.createdAt,
        href: `/evaluation?run_id=${item.id}`,
      })
    }
  }
  activityItems.sort(
    (a, b) => new Date(b.time).getTime() - new Date(a.time).getTime(),
  )
  const recentActivity = activityItems.slice(0, 6)

  const healthStatus = health.isPending
    ? 'checking'
    : health.isError || health.data?.status !== 'ok'
      ? 'down'
      : 'ok'

  const shortcuts = [
    canChat
      ? {
          title: '制度问答',
          desc: '向授权知识库提问，获取制度依据与引用溯源。',
          href: '/chat',
          color: palette.primary,
          icon: <ChatCircle size={16} weight="duotone" />,
        }
      : null,
    canUseDrafts
      ? {
          title: '我的草稿',
          desc: '查看正在编辑的政策草案，继续写作或确认发布。',
          href: '/drafts',
          color: palette.primaryDeep,
          icon: <FileText size={16} weight="duotone" />,
        }
      : null,
    canBrowseKnowledgeBases
      ? {
          title: '知识库管理',
          desc: '浏览和维护授权知识库，管理文档与标签。',
          href: '/knowledge-bases',
          color: palette.accentTeal,
          icon: <BookOpen size={16} weight="duotone" />,
        }
      : null,
    canEvaluate
      ? {
          title: '评估中心',
          desc: '导入测试语料，查看 Hit@K / MRR 检索指标。',
          href: '/evaluation',
          color: palette.textSecondary,
          icon: <ChartBar size={16} weight="duotone" />,
        }
      : null,
  ].filter(Boolean) as Array<{
    title: string
    desc: string
    href: string
    color: string
    icon: ReactNode
  }>

  const statCards = [
    canBrowseKnowledgeBases
      ? {
          title: '知识库',
          value: kbCount ?? 0,
          suffix: '授权',
          icon: <BookOpen size={16} weight="duotone" />,
          chip: palette.primarySoft,
          ink: palette.primary,
          loading: knowledgeBases.isPending,
        }
      : null,
    canChat
      ? {
          title: '历史对话',
          value: conversationTotal ?? 0,
          suffix: '会话',
          icon: <ChatCircle size={16} weight="duotone" />,
          chip: '#eef2f1',
          ink: palette.textSecondary,
          loading: conversations.isPending,
        }
      : null,
    canUseDrafts
      ? {
          title: '草稿箱',
          value: draftTotal ?? 0,
          suffix:
            draftPending !== null && draftPending > 0
              ? `${draftPending} 待确认`
              : '份',
          icon: <FileText size={16} weight="duotone" />,
          chip: palette.warningSoft,
          ink: palette.warning,
          loading: drafts.isPending,
        }
      : null,
    canEvaluate
      ? {
          title: '评估报告',
          value: evalTotal ?? 0,
          suffix: '份',
          icon: <ChartBar size={16} weight="duotone" />,
          chip: '#eef2f1',
          ink: palette.primaryDeep,
          loading: evalRuns.isPending,
        }
      : null,
  ].filter(Boolean) as Array<{
    title: string
    value: number
    suffix: ReactNode
    icon: ReactNode
    chip: string
    ink: string
    loading: boolean
  }>

  return (
    <div>
      <div className="page-toolbar page-toolbar--split">
        <p className="page-lede">
          {greeting}
          {user?.displayName ? `，${user.displayName}` : ''}
          ，这里是你的工作总览。
        </p>
        {healthStatus === 'checking' ? (
          <Tag icon={<CircleNotch size={16} weight="duotone" className="animate-spin" />} color="processing">
            检查服务中
          </Tag>
        ) : healthStatus === 'ok' ? (
          <Tag icon={<CheckCircle size={16} weight="duotone" />} color="success">
            服务运行正常
          </Tag>
        ) : (
          <Tag icon={<XCircle size={16} weight="duotone" />} color="error">
            服务异常
          </Tag>
        )}
      </div>

      {statCards.length > 0 ? (
        <Row gutter={[16, 16]}>
          {statCards.map((item) => (
            <Col xs={24} sm={12} lg={6} key={item.title}>
              <Card styles={{ body: { padding: 18 } }}>
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14 }}>
                  <div
                    style={{
                      width: 44,
                      height: 44,
                      borderRadius: 14,
                      background: item.chip,
                      color: item.ink,
                      display: 'grid',
                      placeItems: 'center',
                      fontSize: 18,
                      flexShrink: 0,
                    }}
                  >
                    {item.icon}
                  </div>
                  <div style={{ minWidth: 0, flex: 1 }}>
                    {item.loading ? (
                      <LoadingState message="加载中…" minH="min-h-0" />
                    ) : (
                      <Statistic title={item.title} value={item.value} suffix={item.suffix} />
                    )}
                  </div>
                </div>
              </Card>
            </Col>
          ))}
        </Row>
      ) : null}

      {shortcuts.length > 0 ? (
        <>
          <Title level={4} style={{ marginTop: 28, marginBottom: 16 }}>
            快捷入口
          </Title>
          <Row gutter={[16, 16]}>
            {shortcuts.map((item) => (
              <Col xs={24} md={12} xl={8} key={item.href}>
                <Link to={item.href} style={{ textDecoration: 'none' }}>
                  <Card hoverable styles={{ body: { minHeight: 160 } }}>
                    <Space orientation="vertical" size={12} style={{ width: '100%' }}>
                      <div
                        style={{
                          width: 40,
                          height: 40,
                          borderRadius: 10,
                          background: `${item.color}14`,
                          color: item.color,
                          display: 'grid',
                          placeItems: 'center',
                          fontSize: 18,
                        }}
                      >
                        {item.icon}
                      </div>
                      <div>
                        <Title level={5} style={{ margin: 0 }}>
                          {item.title}
                        </Title>
                        <Paragraph type="secondary" style={{ marginBottom: 0, marginTop: 6 }}>
                          {item.desc}
                        </Paragraph>
                      </div>
                      <Text type="secondary">
                        进入 <ArrowRight size={16} weight="regular" />
                      </Text>
                    </Space>
                  </Card>
                </Link>
              </Col>
            ))}
          </Row>
        </>
      ) : null}

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={12}>
          <Card title="最近活动" extra={<Text type="secondary">来自你的会话与草稿</Text>}>
            {statsLoading && recentActivity.length === 0 ? (
              <LoadingState message="正在加载最近活动…" minH="min-h-32" />
            ) : recentActivity.length === 0 ? (
              <Text type="secondary">暂无最近活动，去制度问答或草稿里开始吧。</Text>
            ) : (
              <Space orientation="vertical" size={0} style={{ width: '100%' }}>
                {recentActivity.map((item, index) => (
                  <div
                    key={item.key}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      gap: 12,
                      padding: '12px 0',
                      borderTop: index === 0 ? undefined : `1px solid ${palette.borderSecondary}`,
                    }}
                  >
                    <div style={{ minWidth: 0 }}>
                      {item.href ? (
                        <Link to={item.href} style={{ fontWeight: 600, color: palette.text }}>
                          {item.action}
                        </Link>
                      ) : (
                        <Text strong>{item.action}</Text>
                      )}
                      <div>
                        <Text type="secondary" ellipsis>
                          {item.detail}
                        </Text>
                      </div>
                    </div>
                    <Text type="secondary" style={{ flexShrink: 0 }}>
                      {formatRelativeTime(item.time)}
                    </Text>
                  </div>
                ))}
              </Space>
            )}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="使用提示" extra={<Text type="secondary">让问答更精准的小技巧</Text>}>
            <Space orientation="vertical" size={12} style={{ width: '100%' }}>
              {[
                '尽量使用完整的句子描述问题，例如“出差时每天住宿上限是多少？”',
                '在检索范围中勾选相关知识库，缩小检索范围可提高回答精度。',
                '查看引用时，可点击 chunk 跳转到原文段落。',
                '对回答提交反馈（有用/无用/引用错误），有助于持续优化检索质量。',
              ].map((tip, index) => (
                <div key={tip} style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
                  <Tag color="processing">{index + 1}</Tag>
                  <Text>{tip}</Text>
                </div>
              ))}
            </Space>
          </Card>
        </Col>
      </Row>
    </div>
  )
}
