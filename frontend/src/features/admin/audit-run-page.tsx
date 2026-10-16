import { ArrowClockwise, ListMagnifyingGlass } from '@phosphor-icons/react'
import {
  Button,
  Card,
  Descriptions,
  Drawer,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Timeline,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useState } from 'react'
import type { AdminRun, TimelineEntry } from '../../api/admin'
import { EmptyState, ErrorState, LoadingState } from '../../components/feedback/state-views'
import { formatDateTime } from '../../lib/datetime'
import {
  useRecoverRunMutation,
  useRunAuditQuery,
  useRunsQuery,
} from './queries'

const PAGE_SIZE = 20
const RECOVERABLE_STATUSES = new Set(['failed', 'recoverable_failed', 'cancelled', 'error'])

function outcomeColor(entry: TimelineEntry): string {
  if (entry.source === 'run_event') return 'blue'
  if (entry.outcome === 'allowed') return 'green'
  if (entry.outcome === 'denied') return 'red'
  if (entry.outcome === 'blocked') return 'orange'
  return 'gray'
}

function entryLabel(entry: TimelineEntry): string {
  return entry.action ?? entry.eventType ?? entry.eventId
}

function TimelineItemBody({ entry }: { entry: TimelineEntry }) {
  const metadata = Object.keys(entry.metadata).length
  return (
    <Space orientation="vertical" size={2} style={{ width: '100%' }}>
      <Space size={8} wrap>
        <Tag color={entry.source === 'audit' ? 'geekblue' : 'default'}>
          {entry.source === 'audit' ? '审计' : '运行事件'}
        </Tag>
        <Typography.Text strong>{entryLabel(entry)}</Typography.Text>
        {entry.outcome ? <Tag color={outcomeColor(entry)}>{entry.outcome}</Tag> : null}
        {entry.reasonCode ? <Tag color="error">{entry.reasonCode}</Tag> : null}
        {entry.stage ? <Tag>{entry.stage}</Tag> : null}
      </Space>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {formatDateTime(entry.occurredAt, undefined, '时间未知')}
        {entry.actorRef ? ` · ${entry.actorRef}` : ''}
        {entry.redactions.length > 0 ? ` · 已脱敏 ${entry.redactions.join(', ')}` : ''}
      </Typography.Text>
      {metadata > 0 ? (
        <Typography.Paragraph
          type="secondary"
          style={{ margin: 0, fontSize: 12, wordBreak: 'break-word' }}
        >
          {JSON.stringify(entry.metadata)}
        </Typography.Paragraph>
      ) : null}
    </Space>
  )
}

function RunDrawer({ runId, onClose }: { runId: string; onClose: () => void }) {
  const query = useRunAuditQuery(runId)
  const recover = useRecoverRunMutation()

  return (
    <Drawer
      open={Boolean(runId)}
      onClose={onClose}
      width={720}
      title="运行审计时间线"
      destroyOnHidden
    >
      {query.isLoading ? (
        <LoadingState message="正在加载时间线…" />
      ) : query.error ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : !query.data ? null : (
        <Space orientation="vertical" size={16} style={{ width: '100%' }}>
          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label="Run">{query.data.run.runId}</Descriptions.Item>
            <Descriptions.Item label="类型 / 状态">
              {query.data.run.kind} · {query.data.run.status}
            </Descriptions.Item>
            <Descriptions.Item label="证据门">
              {query.data.run.evidenceGate ?? '—'}
            </Descriptions.Item>
            <Descriptions.Item label="工具调用">
              {query.data.run.toolCallCount}
            </Descriptions.Item>
            <Descriptions.Item label="开始 / 结束">
              {formatDateTime(query.data.run.startedAt, undefined, '—')} →{' '}
              {formatDateTime(query.data.run.finishedAt, undefined, '—')}
            </Descriptions.Item>
          </Descriptions>

          {RECOVERABLE_STATUSES.has(query.data.run.status) ? (
            <Popconfirm
              title="把这次运行重新放回队列？"
              description="仅 recoverable_failed 的任务可以恢复；终态任务会被服务端拒绝。恢复动作会写审计。"
              okText="恢复"
              cancelText="取消"
              onConfirm={() =>
                recover.mutate(
                  { runId: query.data!.run.runId, extendAttempts: false },
                  {
                    onSuccess: (result) =>
                      message.success(
                        `已重新排队：${result.previousState} → ${result.state}（第 ${result.attempts}/${result.maxAttempts} 次）`,
                      ),
                    onError: (error) => message.error(error.message),
                  },
                )
              }
            >
              <Button icon={<ArrowClockwise size={16} />} loading={recover.isPending}>
                尝试恢复运行
              </Button>
            </Popconfirm>
          ) : null}

          {query.data.items.length === 0 ? (
            <EmptyState
              title="这次运行还没有审计记录"
              hint="运行事件与审计都按 run_id 关联；没有记录通常意味着任务尚未开始。"
              minH="min-h-24"
            />
          ) : (
            <Timeline
              items={query.data.items.map((entry) => ({
                color: outcomeColor(entry),
                children: <TimelineItemBody entry={entry} />,
              }))}
            />
          )}
        </Space>
      )}
    </Drawer>
  )
}

export function AuditRunPage() {
  const [status, setStatus] = useState<string | undefined>(undefined)
  const [page, setPage] = useState(1)
  const [selectedRun, setSelectedRun] = useState('')
  const query = useRunsQuery({
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
    status,
  })

  const columns: ColumnsType<AdminRun> = [
    {
      title: 'Run',
      dataIndex: 'runId',
      render: (value: string) => <Typography.Text code>{value}</Typography.Text>,
    },
    { title: '类型', dataIndex: 'kind', width: 90 },
    {
      title: '状态',
      dataIndex: 'status',
      width: 150,
      render: (value: string) => (
        <Tag color={value === 'completed' ? 'success' : value === 'failed' ? 'error' : 'default'}>
          {value}
        </Tag>
      ),
    },
    {
      title: '证据门',
      dataIndex: 'evidenceGate',
      width: 160,
      render: (value: string | null) => value ?? '—',
    },
    { title: '工具调用', dataIndex: 'toolCallCount', width: 90, align: 'right' },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      width: 170,
      render: (value: string | null) => formatDateTime(value, undefined, '—'),
    },
    {
      title: '',
      width: 110,
      render: (_, row) => (
        <Button
          size="small"
          type="text"
          icon={<ListMagnifyingGlass size={16} />}
          onClick={() => setSelectedRun(row.runId)}
        >
          时间线
        </Button>
      ),
    },
  ]

  return (
    <div data-testid="admin-audit-run-page" style={{ padding: 'var(--space-6)' }}>
      <Space orientation="vertical" size={4} style={{ marginBottom: 16 }}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          运行与审计
        </Typography.Title>
        <Typography.Text type="secondary">
          只展示当前租户的运行；时间线把审计事件与运行事件按时间合并，并在展示前脱敏。
        </Typography.Text>
      </Space>

      <Card
        size="small"
        extra={
          <Select
            allowClear
            placeholder="全部状态"
            style={{ width: 180 }}
            value={status}
            aria-label="按运行状态过滤"
            onChange={(value) => {
              setPage(1)
              setStatus(value)
            }}
            options={[
              { value: 'running', label: 'running' },
              { value: 'completed', label: 'completed' },
              { value: 'failed', label: 'failed' },
              { value: 'recoverable_failed', label: 'recoverable_failed' },
            ]}
          />
        }
        title="运行列表"
      >
        {query.isLoading ? (
          <LoadingState message="正在加载运行…" />
        ) : query.error ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ) : (query.data?.items.length ?? 0) === 0 ? (
          <EmptyState
            title="没有匹配的运行"
            hint="运行记录只在当前租户内可见。"
            minH="min-h-32"
          />
        ) : (
          <Table
            size="small"
            rowKey="runId"
            columns={columns}
            dataSource={query.data?.items ?? []}
            pagination={{
              current: page,
              pageSize: PAGE_SIZE,
              total: query.data?.total ?? 0,
              showSizeChanger: false,
              onChange: setPage,
            }}
          />
        )}
      </Card>

      {selectedRun ? (
        <RunDrawer runId={selectedRun} onClose={() => setSelectedRun('')} />
      ) : null}
    </div>
  )
}
