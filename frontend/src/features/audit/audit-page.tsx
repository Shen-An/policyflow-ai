import { CopyOutlined, SearchOutlined } from '@ant-design/icons'
import {
  Button,
  Card,
  Collapse,
  Descriptions,
  Empty,
  Input,
  Modal,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { AuditLog } from '../../api/audit'
import { Alert } from '../../components/feedback/alert'
import { LoadingState } from '../../components/feedback/state-views'
import { useAuditLogQuery, useAuditLogsQuery } from './queries'

function positiveInt(value: string | null, fallback: number): number {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback
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

  const columns: ColumnsType<AuditLog> = [
    {
      title: '时间',
      dataIndex: 'createdAt',
      width: 180,
      render: (value: string) =>
        new Intl.DateTimeFormat('zh-CN', {
          dateStyle: 'short',
          timeStyle: 'medium',
        }).format(new Date(value)),
    },
    {
      title: '操作者',
      key: 'actor',
      render: (_, item) => item.actor?.displayName ?? item.actorId ?? '系统',
    },
    {
      title: '动作',
      dataIndex: 'action',
      render: (value: string) => <Typography.Text strong>{value}</Typography.Text>,
    },
    {
      title: '目标',
      key: 'target',
      render: (_, item) => (
        <div>
          <div>{item.targetType}</div>
          <Tag>{item.targetId ?? '—'}</Tag>
        </div>
      ),
    },
    {
      title: 'IP',
      dataIndex: 'ipAddress',
      render: (value?: string | null) => value ?? '—',
    },
    {
      title: '详情',
      key: 'actions',
      width: 100,
      render: (_, item) => (
        <Button size="small" icon={<SearchOutlined />} onClick={() => setSelectedId(item.id)}>
          查看
        </Button>
      ),
    },
  ]

  return (
    <div>
      <div className="page-header">
        <div>
          <h2>审计日志</h2>
          <p>仅系统管理员可访问；敏感字段由后端递归脱敏。</p>
        </div>
      </div>

      <Card style={{ marginBottom: 16 }}>
        <Space wrap>
          <Input
            allowClear
            placeholder="动作"
            value={filters.action ?? ''}
            onChange={(event) => setFilter('action', event.target.value)}
            style={{ width: 160 }}
          />
          <Input
            allowClear
            placeholder="目标类型"
            value={filters.targetType ?? ''}
            onChange={(event) => setFilter('target_type', event.target.value)}
            style={{ width: 160 }}
          />
          <Input
            allowClear
            placeholder="操作者 ID"
            value={filters.actorId ?? ''}
            onChange={(event) => setFilter('actor_id', event.target.value)}
            style={{ width: 180 }}
          />
          <Input
            type="datetime-local"
            value={filters.createdFrom ?? ''}
            onChange={(event) => setFilter('created_from', event.target.value)}
            style={{ width: 220 }}
          />
          <Input
            type="datetime-local"
            value={filters.createdTo ?? ''}
            onChange={(event) => setFilter('created_to', event.target.value)}
            style={{ width: 220 }}
          />
        </Space>
      </Card>

      <Card>
        {query.isError ? (
          <Alert
            tone="danger"
            title="审计日志加载失败"
            action={<Button onClick={() => void query.refetch()}>重新加载</Button>}
          >
            <p>{query.error.message}</p>
          </Alert>
        ) : (
          <Table
            rowKey="id"
            loading={query.isPending}
            columns={columns}
            dataSource={query.data?.items ?? []}
            locale={{ emptyText: <Empty description="没有符合条件的审计记录" /> }}
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
            }}
          />
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
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Descriptions bordered size="small" column={2}>
            <Descriptions.Item label="动作">{query.data.action}</Descriptions.Item>
            <Descriptions.Item label="操作者">
              {query.data.actor?.displayName ?? '系统'}
            </Descriptions.Item>
            <Descriptions.Item label="目标">
              {query.data.targetType} / {query.data.targetId ?? '—'}
            </Descriptions.Item>
            <Descriptions.Item label="IP">{query.data.ipAddress ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="Request ID" span={2}>
              <Space>
                <Typography.Text code>{query.data.requestId ?? '无'}</Typography.Text>
                {query.data.requestId ? (
                  <Button
                    size="small"
                    icon={<CopyOutlined />}
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
                label: '脱敏详情',
                children: (
                  <pre style={{ margin: 0, maxHeight: 320, overflow: 'auto' }}>
                    {JSON.stringify(query.data.detail, null, 2)}
                  </pre>
                ),
              },
            ]}
          />
        </Space>
      ) : null}
    </Modal>
  )
}
