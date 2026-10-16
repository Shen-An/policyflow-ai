import { ShieldCheck, UserPlus } from '@phosphor-icons/react'
import {
  Alert as AntAlert,
  Button,
  Card,
  DatePicker,
  Descriptions,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { Dayjs } from 'dayjs'
import { useState } from 'react'
import type { AdminGrant, AdminRole, QuotaPolicy, QuotaUsage } from '../../api/admin'
import { EmptyState, ErrorState, LoadingState } from '../../components/feedback/state-views'
import { formatDateTime } from '../../lib/datetime'
import {
  useCreateGrantMutation,
  useGrantsQuery,
  useHealthQuery,
  useQuotaQuery,
  useRevokeGrantMutation,
  useRolesQuery,
} from './queries'

const PAGE_SIZE = 20

function CountList({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts).sort(([a], [b]) => a.localeCompare(b))
  if (entries.length === 0) return <Typography.Text type="secondary">无</Typography.Text>
  return (
    <Space size={[4, 4]} wrap>
      {entries.map(([key, value]) => (
        <Tag key={key}>
          {key} · {value}
        </Tag>
      ))}
    </Space>
  )
}

function QuotaSection() {
  const query = useQuotaQuery()
  if (query.isLoading) return <LoadingState message="正在加载配额…" minH="min-h-32" />
  if (query.error) {
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} minH="min-h-32" />
  }
  const data = query.data
  if (!data) return null

  const policyColumns: ColumnsType<QuotaPolicy> = [
    {
      title: '作用域',
      dataIndex: 'scope',
      width: 90,
      render: (value: string) => (
        <Tag color={value === 'global' ? 'blue' : 'default'}>{value}</Tag>
      ),
    },
    { title: '负载', dataIndex: 'workload', width: 90 },
    {
      title: '请求 / 窗口',
      width: 130,
      render: (_, row) =>
        row.requestsPerWindow == null || row.windowSeconds == null
          ? '不限'
          : `${row.requestsPerWindow} / ${row.windowSeconds}s`,
    },
    {
      title: 'token / 窗口',
      width: 120,
      render: (_, row) =>
        row.tokensPerWindow == null ? '不限' : row.tokensPerWindow.toLocaleString(),
    },
    {
      title: '并发 / 排队',
      width: 110,
      render: (_, row) =>
        `${row.maxConcurrency ?? '—'} / ${row.queueAdmissionLimit ?? '—'}`,
    },
    { title: '版本', dataIndex: 'version', width: 70 },
  ]

  const usageColumns: ColumnsType<QuotaUsage> = [
    { title: '负载', dataIndex: 'workload' },
    { title: '请求数', dataIndex: 'requests', align: 'right' },
    {
      title: '预留 token',
      dataIndex: 'reservedTokens',
      align: 'right',
      render: (value: number) => value.toLocaleString(),
    },
    {
      title: '实际 token',
      dataIndex: 'actualTokens',
      align: 'right',
      render: (value: number) => value.toLocaleString(),
    },
  ]

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <Card size="small" title="生效配额策略">
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          同一作用域与负载只展示最高版本；平台默认（global）与租户策略会同时列出，
          租户策略优先。
        </Typography.Paragraph>
        <Table
          size="small"
          rowKey="policyId"
          columns={policyColumns}
          dataSource={data.policies}
          pagination={false}
          locale={{ emptyText: '当前租户没有生效的配额策略' }}
        />
      </Card>
      <Card size="small" title={`用量（活跃租约 ${data.liveLeases}）`}>
        <Table
          size="small"
          rowKey="workload"
          columns={usageColumns}
          dataSource={data.usage}
          pagination={false}
          locale={{ emptyText: '还没有用量记录' }}
        />
      </Card>
    </Space>
  )
}

function HealthSection() {
  const query = useHealthQuery()
  if (query.isLoading) return <LoadingState message="正在加载健康度…" minH="min-h-32" />
  if (query.error) {
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} minH="min-h-32" />
  }
  const data = query.data
  if (!data) return null
  const drift = data.retrieval.manifestsWithCountDrift

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      {drift > 0 ? (
        <AntAlert
          type="warning"
          showIcon
          title={`${drift} 个可检索清单的索引计数与预期不一致`}
          description="这些缺口会出现在一致性修复页；修复动作会写审计。"
        />
      ) : null}
      <Card size="small" title="队列与索引">
        <Descriptions column={1} size="small" bordered>
          <Descriptions.Item label="任务状态">
            <CountList counts={data.jobs} />
          </Descriptions.Item>
          <Descriptions.Item label="最久排队">
            {formatDateTime(data.oldestQueuedAt, undefined, '无排队任务')}
          </Descriptions.Item>
          <Descriptions.Item label="文档索引">
            <CountList counts={data.documents} />
          </Descriptions.Item>
          <Descriptions.Item label="提交任务">
            <CountList counts={data.submissions} />
          </Descriptions.Item>
          <Descriptions.Item label="工作区">
            <CountList counts={data.workspaces} />
          </Descriptions.Item>
          <Descriptions.Item label="检索清单">
            可检索 {data.retrieval.retrievableManifests} · 漂移 {drift} · 活跃物料{' '}
            {data.retrieval.activeMaterials}
          </Descriptions.Item>
          <Descriptions.Item label="未关闭问题">
            {data.reconciliation.openIssues.length === 0 ? (
              <Typography.Text type="secondary">无</Typography.Text>
            ) : (
              <Space size={[4, 4]} wrap>
                {data.reconciliation.openIssues.map((issue) => (
                  <Tag key={`${issue.issueKind}-${issue.severity}`} color="warning">
                    {issue.issueKind} / {issue.severity} · {issue.count}
                  </Tag>
                ))}
              </Space>
            )}
          </Descriptions.Item>
        </Descriptions>
      </Card>
    </Space>
  )
}

function GrantSection() {
  const rolesQuery = useRolesQuery()
  const [memberFilter, setMemberFilter] = useState('')
  const [memberDraft, setMemberDraft] = useState('')
  const [page, setPage] = useState(1)
  const grantsQuery = useGrantsQuery({
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
    memberId: memberFilter || undefined,
  })
  const createMutation = useCreateGrantMutation()
  const revokeMutation = useRevokeGrantMutation()
  const [modalOpen, setModalOpen] = useState(false)
  const [userId, setUserId] = useState('')
  const [roleId, setRoleId] = useState<string | undefined>(undefined)
  const [expiresAt, setExpiresAt] = useState<Dayjs | null>(null)

  const roleName = (id: string): string =>
    rolesQuery.data?.find((role) => role.roleId === id)?.code ?? id

  function closeModal() {
    setModalOpen(false)
    setUserId('')
    setRoleId(undefined)
    setExpiresAt(null)
  }

  function submitGrant() {
    if (!userId.trim() || !roleId) {
      message.error('请填写成员 ID 并选择角色')
      return
    }
    createMutation.mutate(
      {
        userId: userId.trim(),
        roleId,
        expiresAt: expiresAt ? expiresAt.toISOString() : undefined,
      },
      {
        onSuccess: (result) => {
          message.success(result.created ? '已授予角色' : '该成员已有同一有效授权')
          closeModal()
        },
        onError: (error) => message.error(error.message),
      },
    )
  }

  const roleColumns: ColumnsType<AdminRole> = [
    { title: '代码', dataIndex: 'code', width: 150 },
    { title: '名称', dataIndex: 'name', width: 180 },
    {
      title: '动作',
      dataIndex: 'actions',
      render: (actions: string[]) => (
        <Space size={[4, 4]} wrap>
          {actions.map((action) => (
            <Tag key={action}>{action}</Tag>
          ))}
        </Space>
      ),
    },
    { title: '有效授权', dataIndex: 'activeGrants', width: 90, align: 'right' },
  ]

  const grantColumns: ColumnsType<AdminGrant> = [
    {
      title: '成员',
      dataIndex: 'userId',
      render: (value: string) => <Typography.Text code>{value}</Typography.Text>,
    },
    {
      title: '角色',
      dataIndex: 'roleId',
      width: 120,
      render: (value: string) => roleName(value),
    },
    { title: '作用域', dataIndex: 'scope', width: 90 },
    {
      title: '有效期',
      width: 210,
      render: (_, row) => (
        <Typography.Text type="secondary">
          {formatDateTime(row.validFrom, undefined, '立即')} →{' '}
          {formatDateTime(row.expiresAt, undefined, '长期')}
        </Typography.Text>
      ),
    },
    {
      title: '状态',
      width: 130,
      render: (_, row) =>
        row.revokedAt ? (
          <Tag color="error">已撤销 {formatDateTime(row.revokedAt)}</Tag>
        ) : (
          <Tag color="success">有效</Tag>
        ),
    },
    {
      title: '',
      width: 90,
      render: (_, row) =>
        row.revokedAt ? null : (
          <Popconfirm
            title="撤销这条授权？"
            description="撤销立即生效，下一次授权判断就会拒绝；动作会写入审计。"
            okText="撤销"
            cancelText="取消"
            onConfirm={() =>
              revokeMutation.mutate(
                { grantId: row.grantId, reason: 'revoked from admin console' },
                {
                  onSuccess: () => message.success('授权已撤销'),
                  onError: (error) => message.error(error.message),
                },
              )
            }
          >
            <Button size="small" danger type="text">
              撤销
            </Button>
          </Popconfirm>
        ),
    },
  ]

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <Card size="small" title="角色">
        {rolesQuery.isLoading ? (
          <LoadingState message="正在加载角色…" minH="min-h-24" />
        ) : rolesQuery.error ? (
          <ErrorState
            error={rolesQuery.error}
            onRetry={() => void rolesQuery.refetch()}
            minH="min-h-24"
          />
        ) : (
          <Table
            size="small"
            rowKey="roleId"
            columns={roleColumns}
            dataSource={rolesQuery.data ?? []}
            pagination={false}
          />
        )}
      </Card>

      <Card
        size="small"
        title="授权"
        extra={
          <Space>
            <Input
              allowClear
              placeholder="按成员 ID 过滤"
              value={memberDraft}
              onChange={(event) => setMemberDraft(event.target.value)}
              onPressEnter={() => {
                setPage(1)
                setMemberFilter(memberDraft.trim())
              }}
              style={{ width: 240 }}
              aria-label="按成员 ID 过滤"
            />
            <Button
              onClick={() => {
                setPage(1)
                setMemberFilter(memberDraft.trim())
              }}
            >
              过滤
            </Button>
            <Button type="primary" icon={<UserPlus size={16} />} onClick={() => setModalOpen(true)}>
              授予角色
            </Button>
          </Space>
        }
      >
        {grantsQuery.isLoading ? (
          <LoadingState message="正在加载授权…" minH="min-h-24" />
        ) : grantsQuery.error ? (
          <ErrorState
            error={grantsQuery.error}
            onRetry={() => void grantsQuery.refetch()}
            minH="min-h-24"
          />
        ) : (grantsQuery.data?.items.length ?? 0) === 0 ? (
          <EmptyState
            title="没有匹配的授权"
            hint="授权只作用于当前租户；跨租户成员不会出现在这里。"
            minH="min-h-24"
          />
        ) : (
          <Table
            size="small"
            rowKey="grantId"
            columns={grantColumns}
            dataSource={grantsQuery.data?.items ?? []}
            pagination={{
              current: page,
              pageSize: PAGE_SIZE,
              total: grantsQuery.data?.total ?? 0,
              showSizeChanger: false,
              onChange: setPage,
            }}
          />
        )}
      </Card>

      <Modal
        title="授予角色"
        open={modalOpen}
        onCancel={closeModal}
        onOk={submitGrant}
        okText="授予"
        cancelText="取消"
        confirmLoading={createMutation.isPending}
      >
        <Space orientation="vertical" size={12} style={{ width: '100%' }}>
          <div>
            <Typography.Text>成员 ID</Typography.Text>
            <Input
              value={userId}
              onChange={(event) => setUserId(event.target.value)}
              placeholder="租户内成员的用户 ID"
              maxLength={36}
            />
          </div>
          <div>
            <Typography.Text>角色</Typography.Text>
            <Select
              style={{ width: '100%' }}
              value={roleId}
              onChange={setRoleId}
              placeholder="选择要授予的角色"
              options={(rolesQuery.data ?? []).map((role) => ({
                value: role.roleId,
                label: `${role.code} · ${role.name}`,
              }))}
            />
          </div>
          <div>
            <Typography.Text>到期时间（可选）</Typography.Text>
            <DatePicker
              showTime
              style={{ width: '100%' }}
              value={expiresAt}
              onChange={setExpiresAt}
              placeholder="留空表示长期有效"
            />
          </div>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
            只能授予你自己也具备的动作：超出自身权限的授予会被拒绝并记录审计。
          </Typography.Paragraph>
        </Space>
      </Modal>
    </Space>
  )
}

export function AdminPage() {
  return (
    <div data-testid="admin-page" style={{ padding: 'var(--space-6)' }}>
      <Space orientation="vertical" size={4} style={{ marginBottom: 16 }}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          <ShieldCheck size={22} weight="duotone" style={{ marginRight: 8 }} />
          租户治理
        </Typography.Title>
        <Typography.Text type="secondary">
          配额、用量、队列健康与授权都在当前租户内生效；读取与变更都会写入审计。
        </Typography.Text>
      </Space>
      <Tabs
        items={[
          {
            key: 'overview',
            label: '概览',
            children: (
              <Space orientation="vertical" size={16} style={{ width: '100%' }}>
                <HealthSection />
                <QuotaSection />
              </Space>
            ),
          },
          { key: 'access', label: '角色与授权', children: <GrantSection /> },
        ]}
      />
    </div>
  )
}

