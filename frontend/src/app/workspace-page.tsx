import { ArrowRight, BookOpen, ChartBar, ChatCircle, CheckCircle, CircleNotch, FileText, XCircle } from '@phosphor-icons/react'
import { useQuery } from '@tanstack/react-query'
import { Card, Col, Row, Space, Typography } from 'antd'
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
import { QuietChip } from '../components/ui/quiet-chip'
import { conversationKeys } from '../features/chat/queries'
import { draftKeys } from '../features/drafts/queries'
import { evalKeys } from '../features/evaluation/queries'
import { knowledgeBaseKeys } from '../features/knowledge-bases/queries'
import { formatRelativeTime, parseApiDate } from '../lib/datetime'

const { Text } = Typography

type ActivityItem = {
  key: string
  action: string
  detail: string
  time: string
  href?: string
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
    (a, b) => (parseApiDate(b.time)?.getTime() ?? 0) - (parseApiDate(a.time)?.getTime() ?? 0),
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
          icon: <ChatCircle size={16} weight="duotone" />,
        }
      : null,
    canUseDrafts
      ? {
          title: '我的草稿',
          desc: '查看正在编辑的政策草案，继续写作或确认发布。',
          href: '/drafts',
          icon: <FileText size={16} weight="duotone" />,
        }
      : null,
    canBrowseKnowledgeBases
      ? {
          title: '知识库管理',
          desc: '浏览和维护授权知识库，管理文档与标签。',
          href: '/knowledge-bases',
          icon: <BookOpen size={16} weight="duotone" />,
        }
      : null,
    canEvaluate
      ? {
          title: '评估中心',
          desc: '导入测试语料，查看 Hit@K / MRR 检索指标。',
          href: '/evaluation',
          icon: <ChartBar size={16} weight="duotone" />,
        }
      : null,
  ].filter(Boolean) as Array<{
    title: string
    desc: string
    href: string
    icon: ReactNode
  }>

  const statCards = [
    canBrowseKnowledgeBases
      ? {
          title: '知识库',
          value: kbCount ?? 0,
          suffix: '授权',
          icon: <BookOpen size={16} weight="duotone" />,
          tone: 'primary',
          loading: knowledgeBases.isPending,
        }
      : null,
    canChat
      ? {
          title: '历史对话',
          value: conversationTotal ?? 0,
          suffix: '会话',
          icon: <ChatCircle size={16} weight="duotone" />,
          tone: 'neutral',
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
          tone: 'warning',
          loading: drafts.isPending,
        }
      : null,
    canEvaluate
      ? {
          title: '评估报告',
          value: evalTotal ?? 0,
          suffix: '份',
          icon: <ChartBar size={16} weight="duotone" />,
          tone: 'deep',
          loading: evalRuns.isPending,
        }
      : null,
  ].filter(Boolean) as Array<{
    title: string
    value: number
    suffix: ReactNode
    icon: ReactNode
    tone: 'primary' | 'neutral' | 'warning' | 'deep'
    loading: boolean
  }>

  const today = new Date()
  const dateLine = today.toLocaleDateString('zh-CN', {
    month: 'long',
    day: 'numeric',
    weekday: 'long',
  })

  return (
    <div>
      <header className="ws-hero">
        <div>
          <h1 className="ws-hero__greeting">
            {greeting}
            {user?.displayName ? `，${user.displayName}` : ''}
          </h1>
          <p className="ws-hero__sub">{dateLine} · 这里是你的工作总览</p>
        </div>
        <div className="ws-hero__side">
          {healthStatus === 'checking' ? (
            <QuietChip tone="active">
              <CircleNotch size={14} weight="duotone" className="animate-spin" aria-hidden style={{ marginRight: 4 }} />
              检查服务中
            </QuietChip>
          ) : healthStatus === 'ok' ? (
            <QuietChip tone="success">
              <CheckCircle size={14} weight="duotone" aria-hidden style={{ marginRight: 4 }} />
              服务运行正常
            </QuietChip>
          ) : (
            <QuietChip tone="error">
              <XCircle size={14} weight="duotone" aria-hidden style={{ marginRight: 4 }} />
              服务异常
            </QuietChip>
          )}
        </div>
      </header>

      {statCards.length > 0 ? (
        <Row gutter={[16, 16]}>
          {statCards.map((item) => (
            <Col xs={24} sm={12} lg={6} key={item.title}>
              <Card styles={{ body: { padding: '16px 18px' } }}>
                <div className="ws-stat">
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div className="ws-stat__label">{item.title}</div>
                    {item.loading ? (
                      <LoadingState message="加载中…" minH="min-h-0" />
                    ) : (
                      <div className="ws-stat__value">
                        {item.value}
                        <span className="ws-stat__suffix">{item.suffix}</span>
                      </div>
                    )}
                  </div>
                  <div className={`ws-stat__icon ws-stat__icon--${item.tone}`}>{item.icon}</div>
                </div>
              </Card>
            </Col>
          ))}
        </Row>
      ) : null}

      {shortcuts.length > 0 ? (
        <>
          <h2 className="section-title">快捷入口</h2>
          <Row gutter={[16, 16]}>
            {shortcuts.map((item) => (
              <Col xs={24} md={12} xl={8} key={item.href}>
                <Link to={item.href} style={{ textDecoration: 'none', display: 'block', height: '100%' }}>
                  <Card hoverable style={{ height: '100%' }} styles={{ body: { height: '100%' } }}>
                    <div className="ws-shortcut">
                      <div className="ws-shortcut__icon">{item.icon}</div>
                      <div>
                        <h3 className="ws-shortcut__title">{item.title}</h3>
                        <p className="ws-shortcut__desc">{item.desc}</p>
                      </div>
                      <span className="ws-shortcut__cta">
                        进入
                        <ArrowRight size={14} weight="bold" className="ws-shortcut__arrow" aria-hidden />
                      </span>
                    </div>
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
              <div className="ws-activity">
                {recentActivity.map((item) => {
                  const inner = (
                    <>
                      <div className="ws-activity__main">
                        <span className="ws-activity__badge">
                          <QuietChip tone="neutral">{item.action}</QuietChip>
                        </span>
                        <span className="ws-activity__detail">{item.detail}</span>
                      </div>
                      <span className="ws-activity__time">{formatRelativeTime(item.time)}</span>
                    </>
                  )
                  return item.href ? (
                    <Link key={item.key} to={item.href} className="ws-activity__row">
                      {inner}
                    </Link>
                  ) : (
                    <div key={item.key} className="ws-activity__row">
                      {inner}
                    </div>
                  )
                })}
              </div>
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
                  <QuietChip tone="active">{index + 1}</QuietChip>
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
