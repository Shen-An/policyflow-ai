import { ClipboardText, Copy, MagnifyingGlass } from '@phosphor-icons/react'
import {
  Button,
  Card,
  Collapse,
  DatePicker,
  Descriptions,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { AuditLog } from '../../api/audit'
import { Alert } from '../../components/feedback/alert'
import { EmptyState, ErrorState, LoadingState } from '../../components/feedback/state-views'
import { QuietChip, type ChipTone } from '../../components/ui/quiet-chip'
import { formatDateTime } from '../../lib/datetime'
import { useAuditLogQuery, useAuditLogsQuery } from './queries'

const { RangePicker } = DatePicker

function positiveInt(value: string | null, fallback: number): number {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback
}

const actionLabel: Record<string, string> = {
  'document.index_requested': '请求索引文档',
  'document.upload': '上传文档',
  'document.delete': '删除文档',
  'document.reindex': '重新索引',
  'knowledge_base.create': '创建知识库',
  'knowledge_base.delete': '删除知识库',
  'knowledge_base.update': '更新知识库',
  'knowledge_base.view': '查看知识库',
  'mcp.server.create': '创建 MCP',
  'mcp.server.update': '更新 MCP',
  'mcp.server.health_check': 'MCP 健康检查',
  'skill.run': '运行 Skill',
  'skill.enable': '启用 Skill',
  'skill.disable': '禁用 Skill',
  'query_feedback.create': '提交问答反馈',
  'demo.seed': '演示数据初始化',
  'user.create': '创建用户',
  'user.update_roles': '修改用户角色',
  'eval.run.create': '创建评估 Run',
  'eval.run.delete': '删除评估 Run',
  'faq.approve': '审核通过 FAQ',
  'faq.reject': '驳回 FAQ',
}

const targetTypeLabel: Record<string, string> = {
  knowledge_document: '知识文档',
  knowledge_base: '知识库',
  mcp_server: 'MCP 服务',
  skill: 'Skill',
  demo: '演示数据',
  ai_query_log: '问答日志',
  user: '用户',
  eval_run: '评估 Run',
  faq_draft: 'FAQ 草稿',
  draft: '草稿',
}

function labelAction(value: string): string {
  return actionLabel[value] ?? value
}

function labelTargetType(value: string): string {
  return targetTypeLabel[value] ?? value
}

function actionTone(value: string): ChipTone {
  if (value.includes('delete') || value.includes('disable')) return 'error'
  if (value.includes('create') || value.includes('upload') || value.includes('enable')) return 'success'
  if (value.includes('health') || value.includes('index') || value.includes('run')) return 'active'
  return 'neutral'
}

export function AuditPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [selectedId, setSelectedId] = useState('')
  const page = positiveInt(searchParams.get('page'), 1)
  const pageSize = Math.min(positiveInt(searchParams.get('page_size'), 20), 100)
  const filters = {
    page,
    pageSize,
    action: searchParams.get('action') || undefined,
    targetType: searchParams.get('target_type') || undefined,
    actorId: searchParams.get('actor_id') || undefined,
    createdFrom: searchParams.get('created_from') || undefined,
    createdTo: searchParams.get('created_to') || undefined,
  }
  const query = useAuditLogsQuery(filters)

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(key, value)
    else next.delete(key)
    next.set('page', '1')
    setSearchParams(next, { replace: true })
  }

  function clearFilters() {
    const next = new URLSearchParams()
    next.set('page', '1')
    setSearchParams(next, { replace: true })
  }

  const actionOptions = useMemo(
    () =>
      Object.entries(actionLabel).map(([value, label]) => ({
        value,
        label: `${label}（${value}）`,
      })),
    [],
  )
  const targetOptions = useMemo(
    () =>
      Object.entries(targetTypeLabel).map(([value, label]) => ({
        value,
        label: `${label}（${value}）`,
      })),
    [],
  )

  const rangeValue =
    filters.createdFrom || filters.createdTo
      ? ([
          filters.createdFrom ? dayjs(filters.createdFrom) : null,
          filters.createdTo ? dayjs(filters.createdTo) : null,
        ] as [dayjs.Dayjs | null, dayjs.Dayjs | null])
      : null

  const columns: ColumnsType<AuditLog> = [
    {
      title: '时间',
      dataIndex: 'createdAt',
      width: 170,
      render: (value: string) =>
        formatDateTime(
          value,
          {
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
          },
          value,
        ),
    },
    {
      title: '操作者',
      key: 'actor',
      width: 120,
      render: (_, item) => item.actor?.displayName ?? item.actorId ?? '系统',
    },
    {
      title: '动作',
      dataIndex: 'action',
      render: (value: string) => <QuietChip tone={actionTone(value)}>{labelAction(value)}</QuietChip>,
    },
    {
      title: '目标',
      key: 'target',
      render: (_, item) => (
        <div>
          <div style={{ fontWeight: 500 }}>{labelTargetType(item.targetType)}</div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }} code>
            {(item.targetId ?? '—').slice(0, 8)}
            {(item.targetId ?? '').length > 8 ? '…' : ''}
          </Typography.Text>
        </div>
      ),
    },
    {
      title: 'IP',
      dataIndex: 'ipAddress',
      width: 120,
      render: (value?: string | null) => value ?? '—',
    },
    {
      title: '操作',
      key: 'actions',
      width: 90,
      render: (_, item) => (
        <Button type="link" size="small" icon={<MagnifyingGlass size={16} weight="regular" />} onClick={() => setSelectedId(item.id)}>
          详情
        </Button>
      ),
    },
  ]

  const hasFilters = Boolean(
    filters.action || filters.targetType || filters.actorId || filters.createdFrom || filters.createdTo,
  )

  return (
    <div>
      <p className="page-lede" style={{ marginBottom: 12 }}>
        系统关键操作记录；敏感字段由后端递归脱敏，仅管理员可查看。
      </p>

      <div className="pf-filter-bar" style={{ marginBottom: 12, justifyContent: 'space-between' }}>
        <Space wrap size={10}>
          <Select
            allowClear
            showSearch
            optionFilterProp="label"
            placeholder="全部动作"
            style={{ width: 220 }}
            value={filters.action}
            options={actionOptions}
            onChange={(value) => setFilter('action', value ?? '')} />
          <Select
            allowClear
            showSearch
            optionFilterProp="label"
            placeholder="全部目标类型"
            style={{ width: 200 }}
            value={filters.targetType}
            options={targetOptions}
            onChange={(value) => setFilter('target_type', value ?? '')} />
          <Input
            allowClear
            placeholder="操作者 ID（可选）"
            value={filters.actorId ?? ''}
            onChange={(event) => setFilter('actor_id', event.target.value)}
            style={{ width: 180 }} />
          <RangePicker
            showTime
            style={{ width: 360 }}
            value={rangeValue}
            onChange={(values) => {
              const next = new URLSearchParams(searchParams)
              if (values?.[0]) next.set('created_from', values[0].toISOString())
              else next.delete('created_from')
              if (values?.[1]) next.set('created_to', values[1].toISOString())
              else next.delete('created_to')
              next.set('page', '1')
              setSearchParams(next, { replace: true })
            }} />
        </Space>
        {hasFilters ? (
          <Button type="link" onClick={clearFilters}>
            清空筛选
          </Button>
        ) : null}
      </div>

      <Card className="pf-table-card" styles={{ body: { padding: '4px 8px 8px' } }}>
        {query.isError ? (
          <ErrorState
            error={query.error}
            onRetry={() => void query.refetch()}
            title="审计日志加载失败"
            minH="min-h-48" />
        ) : (
          <Table
            size="middle"
            rowKey="id"
            loading={query.isPending}
            columns={columns}
            dataSource={query.data?.items ?? []}
            locale={{
              emptyText: (
                <EmptyState
                  icon={<ClipboardText size={16} weight="duotone" style={{fontSize: 18}} />}
                  title="没有符合条件的审计记录"
                  hint="调整动作、目标类型或时间范围后再试。"
                  minH="min-h-48" />
              ),
            }}
            pagination={{
              current: page,
              pageSize,
              total: query.data?.total ?? 0,
              showSizeChanger: false,
              showTotal: (total) => `共 ${total} 条`,
              onChange: (nextPage) => {
                const next = new URLSearchParams(searchParams)
                next.set('page', String(nextPage))
                setSearchParams(next)
              },
            }} />
        )}
      </Card>

      <AuditDetailModal id={selectedId} onClose={() => setSelectedId('')} />
    </div>
  )
}

function AuditDetailModal({ id, onClose }: { id: string; onClose: () => void }) {
  const query = useAuditLogQuery(id)

  return (
    <Modal
      title="审计详情"
      open={Boolean(id)}
      onCancel={onClose}
      footer={null}
      width={720}
      destroyOnHidden
    >
      {query.isPending ? (
        <LoadingState message="正在加载详情…" minH="min-h-0" />
      ) : query.isError ? (
        <Alert tone="danger">{query.error.message}</Alert>
      ) : query.data ? (
        <Space orientation="vertical" size={16} style={{ width: '100%' }}>
          <Descriptions bordered size="small" column={2}>
            <Descriptions.Item label="动作">
              <QuietChip tone={actionTone(query.data.action)}>{labelAction(query.data.action)}</QuietChip>
            </Descriptions.Item>
            <Descriptions.Item label="操作者">
              {query.data.actor?.displayName ?? '系统'}
            </Descriptions.Item>
            <Descriptions.Item label="目标类型">
              {labelTargetType(query.data.targetType)}
            </Descriptions.Item>
            <Descriptions.Item label="目标 ID">
              <Typography.Text code>{query.data.targetId ?? '—'}</Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="IP">{query.data.ipAddress ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="时间">
              {formatDateTime(query.data.createdAt, undefined, query.data.createdAt)}
            </Descriptions.Item>
            <Descriptions.Item label="Request ID" span={2}>
              <Space>
                <Typography.Text code>{query.data.requestId ?? '无'}</Typography.Text>
                {query.data.requestId ? (
                  <Button
                    size="small"
                    icon={<Copy size={16} weight="duotone" />}
                    onClick={() => {
                      void navigator.clipboard?.writeText(query.data.requestId ?? '')
                      void message.success('已复制')
                    }}
                  >
                    复制
                  </Button>
                ) : null}
              </Space>
            </Descriptions.Item>
          </Descriptions>
          <Collapse
            defaultActiveKey={['detail']}
            items={[
              {
                key: 'detail',
                label: '脱敏详情（JSON）',
                children: (
                  <pre
                    style={{
                      margin: 0,
                      maxHeight: 320,
                      overflow: 'auto',
                      background: 'var(--color-surface-muted)',
                      padding: 12,
                      borderRadius: 10,
                      fontSize: 12,
                    }}
                  >
                    {JSON.stringify(query.data.detail, null, 2)}
                  </pre>
                ),
              },
            ]} />
        </Space>
      ) : null}
    </Modal>
  )
}
