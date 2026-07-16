import { DeleteOutlined, ReloadOutlined } from '@ant-design/icons'
import {
  App,
  Button,
  Card,
  ConfigProvider,
  Empty,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { MemoryItem } from '../../api/memory'
import { LoadingState } from '../../components/feedback/state-views'
import { useDeleteMemoryMutation, useMemoriesQuery } from './queries'

const typeOptions = [
  { value: '', label: '全部类型' },
  { value: 'user_preference', label: '用户偏好' },
  { value: 'long_term_event', label: '长期事件' },
  { value: 'entity', label: '实体记忆' },
  { value: 'conversation_summary', label: '会话摘要' },
  { value: 'stm_summary', label: '短期摘要' },
  { value: 'system_note', label: '系统备注' },
]

const typeMeta: Record<string, { label: string; color: string }> = {
  user_preference: { label: '用户偏好', color: 'blue' },
  long_term_event: { label: '长期事件', color: 'purple' },
  entity: { label: '实体', color: 'cyan' },
  conversation_summary: { label: '会话摘要', color: 'default' },
  stm_summary: { label: '短期摘要', color: 'default' },
  system_note: { label: '系统备注', color: 'gold' },
}

function positiveInt(value: string | null, fallback: number): number {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback
}

function formatTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

export function MemoryPage() {
  const { message, modal } = App.useApp()
  const [searchParams, setSearchParams] = useSearchParams()
  const page = positiveInt(searchParams.get('page'), 1)
  const pageSize = Math.min(positiveInt(searchParams.get('page_size'), 20), 100)
  const memoryType = searchParams.get('memory_type') ?? ''
  const keyword = searchParams.get('keyword') ?? ''
  const [keywordDraft, setKeywordDraft] = useState(keyword)

  const query = useMemoriesQuery(page, pageSize, memoryType, keyword)
  const deleteMutation = useDeleteMemoryMutation()

  function updateParams(patch: Record<string, string | null>) {
    const next = new URLSearchParams(searchParams)
    Object.entries(patch).forEach(([key, value]) => {
      if (!value) next.delete(key)
      else next.set(key, value)
    })
    setSearchParams(next, { replace: true })
  }

  const columns: ColumnsType<MemoryItem> = useMemo(
    () => [
      {
        title: '类型',
        dataIndex: 'memoryType',
        width: 120,
        render: (value: string) => {
          const meta = typeMeta[value] ?? { label: value, color: 'default' }
          return <Tag color={meta.color}>{meta.label}</Tag>
        },
      },
      {
        title: '内容',
        dataIndex: 'content',
        render: (value: string) => (
          <Typography.Paragraph ellipsis={{ rows: 2, tooltip: value }} style={{ marginBottom: 0 }}>
            {value}
          </Typography.Paragraph>
        ),
      },
      {
        title: '置信度',
        dataIndex: 'confidence',
        width: 90,
        render: (value: number) => value.toFixed(2),
      },
      {
        title: '来源',
        dataIndex: 'source',
        width: 90,
      },
      {
        title: '向量',
        dataIndex: 'hasEmbedding',
        width: 80,
        render: (value: boolean) =>
          value ? <Tag color="success">已嵌入</Tag> : <Tag>无</Tag>,
      },
      {
        title: '更新时间',
        dataIndex: 'updatedAt',
        width: 170,
        render: (value: string) => formatTime(value),
      },
      {
        title: '操作',
        key: 'actions',
        width: 90,
        render: (_, record) => (
          <Button
            type="link"
            danger
            size="small"
            icon={<DeleteOutlined />}
            loading={deleteMutation.isPending && deleteMutation.variables === record.id}
            onClick={() => {
              modal.confirm({
                title: '删除这条记忆？',
                content: '删除后无法恢复，后续问答将不再使用该记忆。',
                okText: '删除',
                okButtonProps: { danger: true },
                cancelText: '取消',
                onOk: async () => {
                  try {
                    await deleteMutation.mutateAsync(record.id)
                    message.success('已删除')
                  } catch (error) {
                    message.error(error instanceof Error ? error.message : '删除失败')
                    throw error
                  }
                },
              })
            }}
          >
            删除
          </Button>
        ),
      },
    ],
    [deleteMutation, message, modal],
  )

  return (
    <ConfigProvider theme={{ token: { motion: false } }}>
      <div>
        <div className="page-header">
          <div>
            <h2>我的记忆</h2>
            <p>查看助手为你保留的偏好、实体与长期事件。制度事实仍以知识库检索为准。</p>
          </div>
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => void query.refetch()}>
              刷新
            </Button>
          </Space>
        </div>

        <Card size="small" style={{ marginBottom: 16 }}>
          <Space wrap>
            <Select
              aria-label="记忆类型"
              style={{ width: 160 }}
              value={memoryType}
              options={typeOptions}
              onChange={(value) => updateParams({ memory_type: value || null, page: '1' })}
            />
            <Input.Search
              allowClear
              placeholder="按内容搜索"
              style={{ width: 260 }}
              value={keywordDraft}
              onChange={(event) => setKeywordDraft(event.target.value)}
              onSearch={(value) => {
                setKeywordDraft(value)
                updateParams({ keyword: value.trim() || null, page: '1' })
              }}
            />
          </Space>
        </Card>

        {query.isLoading ? (
          <LoadingState message="正在加载记忆…" />
        ) : query.isError ? (
          <Card>
            <Empty description={query.error instanceof Error ? query.error.message : '加载失败'} />
          </Card>
        ) : (
          <Card styles={{ body: { padding: 0 } }}>
            <Table<MemoryItem>
              rowKey="id"
              columns={columns}
              dataSource={query.data?.items ?? []}
              locale={{ emptyText: <Empty description="暂无记忆" /> }}
              pagination={{
                current: page,
                pageSize,
                total: query.data?.total ?? 0,
                showSizeChanger: true,
                pageSizeOptions: ['10', '20', '50'],
                onChange: (nextPage, nextPageSize) =>
                  updateParams({
                    page: String(nextPage),
                    page_size: String(nextPageSize),
                  }),
              }}
            />
          </Card>
        )}
      </div>
    </ConfigProvider>
  )
}
