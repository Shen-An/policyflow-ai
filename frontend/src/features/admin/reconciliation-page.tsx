import { Wrench } from '@phosphor-icons/react'
import {
  Alert as AntAlert,
  Button,
  Card,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useState } from 'react'
import { AppError } from '../../api/errors'
import type { CapacityRun, ReconciliationIssue } from '../../api/admin'
import { EmptyState, ErrorState, LoadingState } from '../../components/feedback/state-views'
import { formatDateTime } from '../../lib/datetime'
import {
  useCapacityQuery,
  useReconciliationQuery,
  useResolveReconciliationMutation,
  useRetryReconciliationMutation,
} from './queries'

const PAGE_SIZE = 20
const ACTIONABLE = new Set(['open', 'manual_required'])

function severityColor(value: string): string {
  if (value === 'critical') return 'red'
  if (value === 'high' || value === 'error') return 'volcano'
  if (value === 'warning' || value === 'medium') return 'gold'
  return 'default'
}

function CapacitySection() {
  const [page, setPage] = useState(1)
  const query = useCapacityQuery({ limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE })

  if (query.isLoading) return <LoadingState message="正在加载容量记录…" minH="min-h-24" />
  if (query.error) {
    const forbidden = query.error instanceof AppError && query.error.status === 403
    if (forbidden) {
      return (
        <AntAlert
          type="info"
          showIcon
          title="容量证据仅平台管理员可见"
          description="跨租户容量需要 cross_tenant_admin 权限；租户管理员看不到其他租户的容量记录，也无法通过列表推断它们是否存在。"
        />
      )
    }
    return (
      <ErrorState error={query.error} onRetry={() => void query.refetch()} minH="min-h-24" />
    )
  }

  const columns: ColumnsType<CapacityRun> = [
    { title: '场景', dataIndex: 'scenario' },
    { title: '提交', dataIndex: 'commitSha', width: 110 },
    { title: 'LLM 模式', dataIndex: 'llmMode', width: 110 },
    {
      title: '耗时',
      dataIndex: 'durationSeconds',
      width: 90,
      align: 'right',
      render: (value: number | null) => (value == null ? '—' : `${value.toFixed(1)}s`),
    },
    {
      title: '结论',
      dataIndex: 'verdict',
      width: 120,
      render: (value: string | null) =>
        value ? <Tag color={value === 'pass' ? 'success' : 'warning'}>{value}</Tag> : '—',
    },
    {
      title: '开始时间',
      dataIndex: 'startedAt',
      width: 170,
      render: (value: string | null) => formatDateTime(value, undefined, '—'),
    },
  ]

  return (
    <Card size="small" title="平台容量（跨租户）">
      <Table
        size="small"
        rowKey="capacityRunId"
        columns={columns}
        dataSource={query.data?.items ?? []}
        locale={{ emptyText: '还没有容量记录' }}
        pagination={{
          current: page,
          pageSize: PAGE_SIZE,
          total: query.data?.total ?? 0,
          showSizeChanger: false,
          onChange: setPage,
        }}
      />
    </Card>
  )
}

export function ReconciliationPage() {
  const [state, setState] = useState<string | undefined>(undefined)
  const [page, setPage] = useState(1)
  const [resolving, setResolving] = useState<ReconciliationIssue | null>(null)
  const [resolution, setResolution] = useState('')
  const query = useReconciliationQuery({
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
    state,
  })
  const retry = useRetryReconciliationMutation()
  const resolve = useResolveReconciliationMutation()

  function submitResolution() {
    if (!resolving) return
    const note = resolution.trim()
    if (!note) {
      message.error('请填写处理说明')
      return
    }
    resolve.mutate(
      { issueId: resolving.issueId, resolution: note },
      {
        onSuccess: () => {
          message.success('问题已关闭')
          setResolving(null)
          setResolution('')
        },
        onError: (error) => message.error(error.message),
      },
    )
  }

  const columns: ColumnsType<ReconciliationIssue> = [
    {
      title: '类型 / 严重度',
      width: 190,
      render: (_, row) => (
        <Space size={4} wrap>
          <Tag>{row.issueKind}</Tag>
          <Tag color={severityColor(row.severity)}>{row.severity}</Tag>
        </Space>
      ),
    },
    { title: '存储对', dataIndex: 'storePair', width: 150 },
    {
      title: '资源',
      width: 220,
      render: (_, row) => (
        <Space orientation="vertical" size={0}>
          <Typography.Text code>{row.resourceId ?? '—'}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {row.resourceKind ?? '未知资源'}
            {row.versionId ? ` · ${row.versionId}` : ''}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '指纹',
      width: 190,
      render: (_, row) => (
        <Space orientation="vertical" size={0}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            观察到 {row.observedFingerprint ?? '—'}
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            预期 {row.expectedFingerprint ?? '—'}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '状态',
      width: 150,
      render: (_, row) => (
        <Space orientation="vertical" size={0}>
          <Tag color={ACTIONABLE.has(row.state) ? 'warning' : 'success'}>{row.state}</Tag>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {`${row.attempts}/${row.maxAttempts} 次${row.lastErrorCode ? ` · ${row.lastErrorCode}` : ''}`}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '下次尝试',
      dataIndex: 'nextAttemptAt',
      width: 150,
      render: (value: string | null) => formatDateTime(value, undefined, '—'),
    },
    {
      title: '',
      width: 170,
      render: (_, row) =>
        ACTIONABLE.has(row.state) ? (
          <Space size={4}>
            <Button
              size="small"
              type="text"
              loading={retry.isPending}
              onClick={() =>
                retry.mutate(
                  { issueId: row.issueId, reason: 'retry from admin console' },
                  {
                    onSuccess: () => message.success('已重置重试预算'),
                    onError: (error) => message.error(error.message),
                  },
                )
              }
            >
              重试
            </Button>
            <Button size="small" type="text" onClick={() => setResolving(row)}>
              关闭
            </Button>
          </Space>
        ) : null,
    },
  ]

  return (
    <div data-testid="admin-reconciliation-page" style={{ padding: 'var(--space-6)' }}>
      <Space orientation="vertical" size={4} style={{ marginBottom: 16 }}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          <Wrench size={22} weight="duotone" style={{ marginRight: 8 }} />
          一致性修复
        </Typography.Title>
        <Typography.Text type="secondary">
          这里只展示指纹与状态，不展示对象键或主机路径；重试与关闭都会写审计。
        </Typography.Text>
      </Space>

      <Space orientation="vertical" size={16} style={{ width: '100%' }}>
        <Card
          size="small"
          title="跨存储问题"
          extra={
            <Select
              allowClear
              placeholder="全部状态"
              style={{ width: 180 }}
              value={state}
              aria-label="按问题状态过滤"
              onChange={(value) => {
                setPage(1)
                setState(value)
              }}
              options={[
                { value: 'open', label: 'open' },
                { value: 'manual_required', label: 'manual_required' },
                { value: 'resolved', label: 'resolved' },
              ]}
            />
          }
        >
          {query.isLoading ? (
            <LoadingState message="正在加载问题…" />
          ) : query.error ? (
            <ErrorState error={query.error} onRetry={() => void query.refetch()} />
          ) : (query.data?.items.length ?? 0) === 0 ? (
            <EmptyState
              title="没有跨存储问题"
              hint="巡检任务发现不一致时会在这里生成指纹记录。"
              minH="min-h-32"
            />
          ) : (
            <Table
              size="small"
              rowKey="issueId"
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

        <CapacitySection />
      </Space>

      <Modal
        title="关闭问题"
        open={Boolean(resolving)}
        onCancel={() => {
          setResolving(null)
          setResolution('')
        }}
        onOk={submitResolution}
        okText="关闭问题"
        cancelText="取消"
        confirmLoading={resolve.isPending}
      >
        <Space orientation="vertical" size={8} style={{ width: '100%' }}>
          <Typography.Text type="secondary">
            {resolving ? `${resolving.issueKind} · ${resolving.resourceId ?? resolving.issueId}` : ''}
          </Typography.Text>
          <Input.TextArea
            rows={4}
            maxLength={500}
            showCount
            value={resolution}
            onChange={(event) => setResolution(event.target.value)}
            placeholder="说明你做了什么（例如：重新上传了缺失的制度附件）"
          />
        </Space>
      </Modal>
    </div>
  )
}

