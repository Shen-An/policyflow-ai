import { BookOpen, FileText, MagnifyingGlass, Plus, Trash } from '@phosphor-icons/react'
import {
  Button,
  Card,
  Col,
  Empty,
  Input,
  Row,
  Space,
  Tag,
  Typography,
  message,
} from 'antd'
import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import type { KnowledgeBase } from '../../api/knowledge-bases'
import { hasAnyRole } from '../../auth/permissions'
import { useAuthState } from '../../auth/auth-store'
import { Alert } from '../../components/feedback/alert'
import { LoadingState } from '../../components/feedback/state-views'
import { palette } from '../../styles/palette'
import { CreateKnowledgeBaseDialog } from './components/create-knowledge-base-dialog'
import {
  permissionColor,
  permissionLabel,
  queryModeLabel,
  statusColor,
  statusLabel,
} from './labels'
import { useDeleteKnowledgeBaseMutation, useKnowledgeBasesQuery } from './queries'
import { confirmAction } from '../../lib/confirm'

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
  const canCreate = Boolean(user && hasAnyRole(user.roles, ['kb_admin', 'sys_admin']))

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
    <div>
      <div className="page-toolbar page-toolbar--split">
        <p className="page-lede">浏览授权范围内的制度库，管理文档与检索配置。</p>
        {canCreate ? (
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            <Plus size={16} weight="regular" aria-hidden />
            创建知识库
          </Button>
        ) : null}
      </div>

      <div className="pf-filter-bar" style={{ marginBottom: 12, justifyContent: 'space-between' }}>
        <Input
          allowClear
          prefix={<MagnifyingGlass size={16} weight="regular" />}
          placeholder="搜索名称、编码或描述"
          value={searchParams.get('keyword') ?? ''}
          onChange={(event) => updateSearch(event.target.value)}
          style={{ width: 320, maxWidth: '100%' }}
          aria-label="搜索知识库" />
        {query.data ? (
          <Typography.Text type="secondary">
            共 {filtered.length} 个
            {query.isFetching && !query.isPending ? '，正在刷新…' : ''}
          </Typography.Text>
        ) : null}
      </div>

      {query.isPending ? (
        <LoadingState message="正在加载知识库…" />
      ) : query.isError ? (
        <Alert
          tone="danger"
          title="知识库加载失败"
          action={<Button onClick={() => void query.refetch()}>重新加载</Button>}
        >
          <p>{query.error.message}</p>
        </Alert>
      ) : visible.length === 0 ? (
        <Card>
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              keyword ? (
                <div>
                  <div>没有匹配的知识库</div>
                  <div style={{ marginTop: 4, color: 'var(--color-text-secondary)' }}>
                    试试更换关键词，或清空搜索查看全部。
                  </div>
                </div>
              ) : (
                <div>
                  <div>没有可访问的知识库</div>
                  {canCreate ? (
                    <div style={{ marginTop: 4, color: 'var(--color-text-secondary)' }}>
                      创建第一个知识库后即可上传制度文档。
                    </div>
                  ) : (
                    <div style={{ marginTop: 4, color: 'var(--color-text-secondary)' }}>
                      请联系管理员为你分配知识库权限。
                    </div>
                  )}
                </div>
              )
            }
          >
            {!keyword && canCreate ? (
              <Button type="primary" onClick={() => setCreateOpen(true)}>
                <Plus size={16} weight="regular" aria-hidden />
                创建知识库
              </Button>
            ) : null}
          </Empty>
        </Card>
      ) : (
        <>
          <Row gutter={[16, 16]}>
            {visible.map((knowledgeBase) => (
              <Col xs={24} md={12} xl={8} key={knowledgeBase.id}>
                <KnowledgeBaseCard knowledgeBase={knowledgeBase} />
              </Col>
            ))}
          </Row>
          <div
            style={{
              marginTop: 20,
              display: 'flex',
              flexWrap: 'wrap',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 12,
            }}
          >
            <Typography.Text type="secondary">
              共 {filtered.length} 个，第 {page} / {totalPages} 页
            </Typography.Text>
            <Space>
              <Button disabled={page <= 1} onClick={() => goToPage(page - 1)}>
                上一页
              </Button>
              <Button disabled={page >= totalPages} onClick={() => goToPage(page + 1)}>
                下一页
              </Button>
            </Space>
          </div>
        </>
      )}

      <CreateKnowledgeBaseDialog open={createOpen} onOpenChange={setCreateOpen} />
    </div>
  )
}

function KnowledgeBaseCard({ knowledgeBase }: { knowledgeBase: KnowledgeBase }) {
  const deleteMutation = useDeleteKnowledgeBaseMutation()
  const canManage = knowledgeBase.permission === 'admin'
  const isEvalTest = knowledgeBase.code === 'eval_test'
  const statusText = statusLabel[knowledgeBase.status] ?? knowledgeBase.status
  const modeText =
    queryModeLabel[knowledgeBase.defaultQueryMode] ?? knowledgeBase.defaultQueryMode
  const accent = isEvalTest ? palette.warning : palette.primary
  const accentSoft = isEvalTest ? `${palette.warning}14` : `${palette.primary}12`

  function handleDelete() {
    confirmAction({
      title: `物理删除知识库「${knowledgeBase.name}」？`,
      content: isEvalTest
        ? '将永久删除评估测试库、文档与本地工作区，不可恢复。'
        : '将永久删除该知识库、其下全部文档与本地工作区，不可恢复。',
      okText: '永久删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        await deleteMutation.mutateAsync(knowledgeBase.id)
        message.success('知识库已物理删除')
      },
    })
  }

  return (
    <Card hoverable className={`kb-card${isEvalTest ? ' kb-card--eval' : ''}`}>
      <div className="kb-card__top">
        <div className="kb-card__identity">
          <div className="kb-card__icon" style={{ background: accentSoft, color: accent }}>
            <BookOpen size={16} weight="duotone" aria-hidden />
          </div>
          <div className="kb-card__title-block">
            <Link to={`/knowledge-bases/${knowledgeBase.id}`} className="kb-card__title">
              {knowledgeBase.name}
            </Link>
            <div className="kb-card__code">{knowledgeBase.code}</div>
          </div>
        </div>
        <Space size={6} wrap className="kb-card__tags">
          <Tag color={permissionColor[knowledgeBase.permission]}>
            {permissionLabel[knowledgeBase.permission]}
          </Tag>
          <Tag color={statusColor[knowledgeBase.status] ?? 'default'}>{statusText}</Tag>
        </Space>
      </div>

      <Typography.Paragraph
        type="secondary"
        ellipsis={{ rows: 2, tooltip: knowledgeBase.description || undefined }}
        className="kb-card__desc"
      >
        {knowledgeBase.description || (isEvalTest ? '评估专用测试库，CRUD / Hit@K 导入默认进入此库。' : '暂无描述')}
      </Typography.Paragraph>

      <div className="kb-card__meta">
        <div className="kb-card__meta-item">
          <FileText size={16} weight="duotone" className="kb-card__meta-icon" aria-hidden />
          <span className="kb-card__meta-label">文档</span>
          <span className="kb-card__meta-value">{knowledgeBase.documentCount}</span>
        </div>
        <div className="kb-card__meta-divider" aria-hidden />
        <div className="kb-card__meta-item">
          <span className="kb-card__meta-label">检索</span>
          <span className="kb-card__meta-value kb-card__meta-value--mode">{modeText}</span>
        </div>
      </div>

      <div className="kb-card__footer">
        <Link to={`/knowledge-bases/${knowledgeBase.id}`} className="kb-card__link">
          查看详情
        </Link>
        {canManage ? (
          <Button
            type="text"
            danger
            size="small"
            icon={<Trash size={16} weight="duotone" aria-hidden />}
            loading={deleteMutation.isPending}
            onClick={handleDelete}
          >
            删除
          </Button>
        ) : (
          <span className="kb-card__readonly">只读</span>
        )}
      </div>
    </Card>
  )
}
