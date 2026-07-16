import {
  BookOutlined,
  DeleteOutlined,
  FileTextOutlined,
  PlusOutlined,
  SearchOutlined,
} from '@ant-design/icons'
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
      <div className="page-header">
        <div>
          <h2>知识库</h2>
          <p>浏览授权范围内的制度库，管理文档与检索配置。仅展示后端授权给当前用户的资源。</p>
        </div>
        {canCreate ? (
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            <PlusOutlined aria-hidden />
            创建知识库
          </Button>
        ) : null}
      </div>

      <Card styles={{ body: { paddingBottom: 8 } }} style={{ marginBottom: 16 }}>
        <div className="page-toolbar" style={{ justifyContent: 'space-between', marginBottom: 0 }}>
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="搜索名称、编码或描述"
            value={searchParams.get('keyword') ?? ''}
            onChange={(event) => updateSearch(event.target.value)}
            style={{ width: 320, maxWidth: '100%' }}
            aria-label="搜索知识库"
          />
          {query.data ? (
            <Typography.Text type="secondary">
              共 {filtered.length} 个
              {query.isFetching && !query.isPending ? '，正在刷新…' : ''}
            </Typography.Text>
          ) : null}
        </div>
      </Card>

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
                <PlusOutlined aria-hidden />
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
    <Card
      hoverable
      className="kb-card"
      styles={{ body: { display: 'flex', flexDirection: 'column', gap: 14, minHeight: 220 } }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, minWidth: 0 }}>
          <div
            className="kb-card__icon"
            style={{
              width: 42,
              height: 42,
              borderRadius: 12,
              display: 'grid',
              placeItems: 'center',
              flexShrink: 0,
              background: isEvalTest ? `${palette.warning}14` : `${palette.primary}14`,
              color: isEvalTest ? palette.warning : palette.primary,
              fontSize: 18,
            }}
          >
            <BookOutlined aria-hidden />
          </div>
          <div style={{ minWidth: 0 }}>
            <Typography.Title level={5} style={{ margin: 0 }} ellipsis={{ tooltip: knowledgeBase.name }}>
              <Link
                to={`/knowledge-bases/${knowledgeBase.id}`}
                style={{ color: 'inherit', textDecoration: 'none' }}
              >
                {knowledgeBase.name}
              </Link>
            </Typography.Title>
            <Typography.Text type="secondary" code style={{ fontSize: 12 }}>
              {knowledgeBase.code}
            </Typography.Text>
          </div>
        </div>
        <Space size={4} wrap style={{ justifyContent: 'flex-end' }}>
          <Tag color={permissionColor[knowledgeBase.permission]}>
            {permissionLabel[knowledgeBase.permission]}
          </Tag>
          <Tag color={statusColor[knowledgeBase.status] ?? 'default'}>{statusText}</Tag>
        </Space>
      </div>

      <Typography.Paragraph
        type="secondary"
        ellipsis={{ rows: 2, tooltip: knowledgeBase.description || undefined }}
        style={{ marginBottom: 0, minHeight: 44 }}
      >
        {knowledgeBase.description || '暂无描述'}
      </Typography.Paragraph>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 10,
          marginTop: 'auto',
        }}
      >
        <div className="kb-card__stat">
          <div className="kb-card__stat-label">
            <FileTextOutlined aria-hidden /> 文档
          </div>
          <div className="kb-card__stat-value">{knowledgeBase.documentCount}</div>
        </div>
        <div className="kb-card__stat">
          <div className="kb-card__stat-label">检索模式</div>
          <div className="kb-card__stat-value" style={{ fontSize: 14 }}>
            {queryModeLabel[knowledgeBase.defaultQueryMode] ?? knowledgeBase.defaultQueryMode}
          </div>
        </div>
      </div>

      {isEvalTest ? (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          评估专用「测试库」，CRUD / Hit@K 导入默认进入此库。
        </Typography.Text>
      ) : null}

      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: 8,
          paddingTop: 4,
          borderTop: '1px solid var(--color-border-secondary)',
        }}
      >
        <Button type="link" style={{ paddingInline: 0 }}>
          <Link to={`/knowledge-bases/${knowledgeBase.id}`}>查看详情</Link>
        </Button>
        {canManage ? (
          <Button
            type="link"
            danger
            loading={deleteMutation.isPending}
            onClick={handleDelete}
          >
            <DeleteOutlined aria-hidden />
            删除
          </Button>
        ) : (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            只读访问
          </Typography.Text>
        )}
      </div>
    </Card>
  )
}
