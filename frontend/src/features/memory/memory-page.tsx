import { ArrowClockwise, Database, Trash } from '@phosphor-icons/react'
import {
  App,
  Button,
  Card,
  Input,
  Popover,
  Select,
  Space,
  Table,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { MemoryItem } from '../../api/memory'
import { EmptyState, ErrorState, LoadingState } from '../../components/feedback/state-views'
import { MarkdownContent } from '../../components/markdown/markdown-content'
import { QuietChip, type ChipTone } from '../../components/ui/quiet-chip'
import { confirmAction } from '../../lib/confirm'
import { formatDateTime } from '../../lib/datetime'
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

const typeMeta: Record<string, { label: string; tone: ChipTone }> = {
  user_preference: { label: '用户偏好', tone: 'active' },
  long_term_event: { label: '长期事件', tone: 'accent' },
  entity: { label: '实体', tone: 'active' },
  conversation_summary: { label: '会话摘要', tone: 'neutral' },
  stm_summary: { label: '短期摘要', tone: 'neutral' },
  system_note: { label: '系统备注', tone: 'warning' },
}

function positiveInt(value: string | null, fallback: number): number {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback
}

export function MemoryPage() {
  const { message } = App.useApp()
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
          const meta = typeMeta[value] ?? { label: value, tone: 'neutral' as const }
          return <QuietChip tone={meta.tone}>{meta.label}</QuietChip>
        },
      },
      {
        title: '内容',
        dataIndex: 'content',
        render: (value: string) => (
          <Popover
            placement="leftTop"
            trigger="hover"
            mouseEnterDelay={0.25}
            overlayStyle={{ maxWidth: 520 }}
            content={
              <div className="memory-content-popover">
                <MarkdownContent content={value} />
              </div>
            }
          >
            <div className="memory-content-cell">
              <MarkdownContent content={value} />
            </div>
          </Popover>
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
          value ? <QuietChip tone="success">已嵌入</QuietChip> : <QuietChip>无</QuietChip>,
      },
      {
        title: '更新时间',
        dataIndex: 'updatedAt',
        width: 170,
        render: (value: string) => formatDateTime(value, undefined, value),
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
            icon={<Trash size={16} weight="duotone" />}
            loading={deleteMutation.isPending && deleteMutation.variables === record.id}
            onClick={() => {
              confirmAction({
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
    [deleteMutation, message],
  )

  return (
    <div>
      <div className="page-toolbar page-toolbar--split">
        <p className="page-lede">查看助手为你保留的偏好、实体与长期事件。制度事实仍以知识库检索为准。</p>
        <Space>
          <Button icon={<ArrowClockwise size={16} weight="duotone" />} onClick={() => void query.refetch()}>
            刷新
          </Button>
        </Space>
      </div>

      <div className="pf-filter-bar" style={{ marginBottom: 12 }}>
        <Select
          aria-label="记忆类型"
          style={{ width: 160 }}
          value={memoryType}
          options={typeOptions}
          onChange={(value) => updateParams({ memory_type: value || null, page: '1' })} />
        <Input.Search
          allowClear
          placeholder="按内容搜索"
          style={{ width: 260 }}
          value={keywordDraft}
          onChange={(event) => setKeywordDraft(event.target.value)}
          onSearch={(value) => {
            setKeywordDraft(value)
            updateParams({ keyword: value.trim() || null, page: '1' })
          }} />
      </div>

      <Card className="pf-table-card" styles={{ body: { padding: query.isLoading || query.isError ? 16 : 0 } }}>
        {query.isLoading ? (
          <LoadingState message="正在加载记忆…" minH="min-h-48" />
        ) : query.isError ? (
          <ErrorState
            error={query.error instanceof Error ? query.error : new Error('加载失败')}
            onRetry={() => void query.refetch()}
            title="记忆列表加载失败"
            minH="min-h-48" />
        ) : (
          <Table<MemoryItem>
            size="middle"
            rowKey="id"
            columns={columns}
            dataSource={query.data?.items ?? []}
            locale={{
              emptyText: (
                <EmptyState
                  icon={<Database size={16} weight="duotone" style={{fontSize: 18}} />}
                  title={keyword || memoryType ? '没有符合条件的记忆' : '暂无记忆'}
                  hint="助手会在对话中逐步沉淀偏好与实体；制度事实仍以知识库为准。"
                  minH="min-h-48" />
              ),
            }}
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
            }} />
        )}
      </Card>
    </div>
  )
}
