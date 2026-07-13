import { PlusOutlined, SearchOutlined } from '@ant-design/icons'
import {
  Button,
  Card,
  Col,
  Empty,
  Input,
  Row,
  Space,
  Statistic,
  Tag,
  Typography,
} from 'antd'
import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import type { KnowledgeBase } from '../../api/knowledge-bases'
import { hasAnyRole } from '../../auth/permissions'
import { useAuthState } from '../../auth/auth-store'
import { Alert } from '../../components/feedback/alert'
import { LoadingState } from '../../components/feedback/state-views'
import { CreateKnowledgeBaseDialog } from './components/create-knowledge-base-dialog'
import { useKnowledgeBasesQuery } from './queries'

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
          <p>仅展示后端授权给当前用户的知识资源。</p>
        </div>
        {canCreate ? (
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            <PlusOutlined aria-hidden />
            创建知识库
          </Button>
        ) : null}
      </div>

      <Card style={{ marginBottom: 16 }}>
        <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="搜索名称、编码或描述"
            value={searchParams.get('keyword') ?? ''}
            onChange={(event) => updateSearch(event.target.value)}
            style={{ width: 320, maxWidth: '100%' }}
          />
          {query.data ? (
            <Typography.Text type="secondary">
              共 {filtered.length} 个
            </Typography.Text>
          ) : null}
        </Space>
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
            description={
              keyword ? '没有匹配的知识库' : '没有可访问的知识库'
            }
          />
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
          <Space style={{ marginTop: 16, width: '100%', justifyContent: 'space-between' }}>
            <Typography.Text type="secondary">
              共 {filtered.length} 个，第 {page} / {totalPages} 页
            </Typography.Text>
            <Space>
              <Button disabled={page <= 1} onClick={() => goToPage(page - 1)}>
                上一页
              </Button>
              <Button
                disabled={page >= totalPages}
                onClick={() => goToPage(page + 1)}
              >
                下一页
              </Button>
            </Space>
          </Space>
        </>
      )}

      <CreateKnowledgeBaseDialog open={createOpen} onOpenChange={setCreateOpen} />
    </div>
  )
}

function KnowledgeBaseCard({ knowledgeBase }: { knowledgeBase: KnowledgeBase }) {
  return (
    <Card
      hoverable
      title={knowledgeBase.name}
      extra={<Tag>{knowledgeBase.permission}</Tag>}
      actions={[
        <Link key="open" to={`/knowledge-bases/${knowledgeBase.id}`}>
          查看详情
        </Link>,
      ]}
    >
      <Typography.Text type="secondary" code>
        {knowledgeBase.code}
      </Typography.Text>
      <Typography.Paragraph
        type="secondary"
        ellipsis={{ rows: 2 }}
        style={{ marginTop: 12, minHeight: 44 }}
      >
        {knowledgeBase.description || '暂无描述'}
      </Typography.Paragraph>
      <Row gutter={16}>
        <Col span={12}>
          <Statistic title="文档" value={knowledgeBase.documentCount} />
        </Col>
        <Col span={12}>
          <Statistic title="检索模式" value={knowledgeBase.defaultQueryMode} valueStyle={{ fontSize: 16 }} />
        </Col>
      </Row>
    </Card>
  )
}
