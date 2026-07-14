import { Copy, X } from '@phosphor-icons/react'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message as staticMessage,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { Skill } from '../../api/skills'
import type { ToolCallLog } from '../../api/tools'
import { LoadingState } from '../../components/feedback/state-views'
import { confirmAction } from '../../lib/confirm'
import { formatDateTime } from '../../lib/datetime'
import { skillRiskLabel, toolLogStatusLabel } from '../../lib/labels'
import {
  useRunSkillMutation,
  useSetSkillEnabledMutation,
  useSkillsQuery,
  useToolLogQuery,
  useToolLogsQuery,
  useToolsQuery,
} from './queries'

function positiveInt(value: string | null, fallback: number): number {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback
}

function formatDate(value: string): string {
  return formatDateTime(value, { dateStyle: 'short', timeStyle: 'medium' }, value)
}

export function SkillsPage() {
  return (
    <div>
      <p className="page-lede" style={{ marginBottom: 14 }}>
        管理已登记 Skill，并查看经过后端递归脱敏的 Tool 调用日志。
      </p>
      <Space orientation="vertical" size={16} style={{ width: '100%' }}>
        <SkillRegistry />
        <ToolLogSection />
      </Space>
    </div>
  )
}

function SkillRegistry() {
  const query = useSkillsQuery()
  const toggle = useSetSkillEnabledMutation()
  const [runningSkill, setRunningSkill] = useState<Skill | null>(null)

  function changeStatus(skill: Skill) {
    const enabled = !skill.enabled
    confirmAction({
      title: `${enabled ? '启用' : '禁用'} Skill`,
      content: `确认${enabled ? '启用' : '禁用'} Skill“${skill.name}”吗？该操作会写入审计日志。`,
      okText: enabled ? '启用' : '禁用',
      okButtonProps: enabled ? undefined : { danger: true },
      cancelText: '取消',
      onOk: () => toggle.mutateAsync({ name: skill.name, enabled }),
    })
  }

  const columns: ColumnsType<Skill> = useMemo(
    () => [
      {
        title: '名称 / 描述',
        key: 'name',
        render: (_, skill) => (
          <div>
            <div style={{ fontWeight: 600 }}>{skill.name}</div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {skill.description}
            </Typography.Text>
          </div>
        ),
      },
      { title: '版本', dataIndex: 'version', width: 100 },
      {
        title: '风险',
        dataIndex: 'riskLevel',
        width: 100,
        render: (value: string) => skillRiskLabel[value] ?? value,
      },
      {
        title: '实现状态',
        dataIndex: 'implemented',
        width: 110,
        render: (implemented: boolean) => (
          <Tag color={implemented ? 'blue' : 'default'}>
            {implemented ? '已实现' : '未实现'}
          </Tag>
        ),
      },
      {
        title: '启用状态',
        dataIndex: 'enabled',
        width: 110,
        render: (enabled: boolean) => (
          <Tag color={enabled ? 'success' : 'error'}>{enabled ? '已启用' : '已禁用'}</Tag>
        ),
      },
      {
        title: '配置',
        key: 'config',
        width: 140,
        render: (_, skill) => (
          <details>
            <summary style={{ cursor: 'pointer', fontSize: 12, fontWeight: 600 }}>查看摘要</summary>
            <pre
              style={{
                maxWidth: 220,
                maxHeight: 160,
                overflow: 'auto',
                marginTop: 8,
                fontSize: 11,
                background: 'var(--color-surface-muted)',
                padding: 8,
                borderRadius: 6,
              }}
            >
              {JSON.stringify(skill.configSummary, null, 2)}
            </pre>
          </details>
        ),
      },
      {
        title: '操作',
        key: 'actions',
        width: 200,
        render: (_, skill) => (
          <Space wrap>
            <Button
              size="small"
              autoInsertSpace={false}
              loading={toggle.isPending}
              onClick={() => void changeStatus(skill)}
            >
              {skill.enabled ? '禁用' : '启用'}
            </Button>
            <Button
              size="small"
              type="primary"
              autoInsertSpace={false}
              disabled={!skill.runnable}
              title={!skill.implemented ? '后端尚未实现该 Skill' : undefined}
              onClick={() => setRunningSkill(skill)}
            >
              手动运行
            </Button>
          </Space>
        ),
      },
    ],
    [toggle.isPending],
  )

  return (
    <Card
      title="Skill 注册表"
      extra={
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          仅标记为“已实现”的 Skill 可手动运行；参数由后端 Schema 校验。
        </Typography.Text>
      }
    >
      {query.isError ? (
        <Alert
          type="error"
          showIcon
          title="Skill 加载失败"
          description={query.error.message}
          action={
            <Button size="small" onClick={() => void query.refetch()}>
              重新加载
            </Button>
          } />
      ) : (
        <Table
          rowKey="name"
          loading={query.isPending}
          columns={columns}
          dataSource={query.data ?? []}
          pagination={false}
          locale={{ emptyText: <Empty description="尚未登记 Skill。" /> }} />
      )}
      {toggle.isError ? (
        <Alert type="error" showIcon style={{ marginTop: 12 }} title={toggle.error.message} />
      ) : null}
      {runningSkill ? (
        <SkillRunDialog
          key={runningSkill.name}
          skill={runningSkill}
          open
          onOpenChange={(open) => {
            if (!open) setRunningSkill(null)
          }} />
      ) : null}
    </Card>
  )
}

function defaultSkillInput(skill: Skill): Record<string, unknown> {
  if (skill.name === 'summary') return { text: '' }
  if (skill.name === 'process_checklist') return { question: '' }
  if (skill.name === 'policy_compare') return { policies: [{}, {}] }
  return {}
}

function SkillRunDialog({
  skill,
  open,
  onOpenChange,
}: {
  skill: Skill
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const mutation = useRunSkillMutation()
  const [form] = Form.useForm<{ payload: string }>()
  const [parseError, setParseError] = useState('')

  async function submit(values: { payload: string }) {
    setParseError('')
    let input: unknown
    try {
      input = JSON.parse(values.payload)
    } catch {
      setParseError('请输入有效的 JSON。')
      return
    }
    if (!input || typeof input !== 'object' || Array.isArray(input)) {
      setParseError('运行参数必须是 JSON 对象。')
      return
    }
    await mutation.mutateAsync({ name: skill.name, input: input as Record<string, unknown> })
  }

  return (
    <Modal
      title={`运行 ${skill.name}`}
      open={open}
      onCancel={() => onOpenChange(false)}
      footer={null}
      width={960}
      destroyOnHidden
      closeIcon={<span aria-label="关闭运行对话框"><X size={16} weight="regular" /></span>}
    >
      <Typography.Paragraph type="secondary">
        仅提交符合后端输入 Schema 的 JSON，不执行任何前端代码。
      </Typography.Paragraph>
      <div style={{ display: 'grid', gap: 16, gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))' }}>
        <Form
          form={form}
          layout="vertical"
          requiredMark={false}
          initialValues={{ payload: JSON.stringify(defaultSkillInput(skill), null, 2) }}
          onFinish={(values) => void submit(values)}
        >
          <Form.Item label="运行参数" name="payload" rules={[{ required: true }]}>
            <Input.TextArea
              aria-label="运行参数"
              rows={14}
              spellCheck={false}
              style={{ fontFamily: 'monospace', fontSize: 12 }} />
          </Form.Item>
          {parseError ? <Alert type="error" showIcon style={{ marginBottom: 12 }} title={parseError} /> : null}
          {mutation.isError ? (
            <Alert type="error" showIcon style={{ marginBottom: 12 }} title={mutation.error.message} />
          ) : null}
          <Button type="primary" htmlType="submit" autoInsertSpace={false} loading={mutation.isPending}>
            {mutation.isPending ? '正在运行…' : '确认运行'}
          </Button>
        </Form>
        <div>
          <details open className="pf-surface-muted">
            <summary style={{ cursor: 'pointer', padding: 12, fontWeight: 600 }}>输入 Schema</summary>
            <pre
              style={{
                maxHeight: 256,
                overflow: 'auto',
                margin: 0,
                borderTop: '1px solid var(--color-border-secondary)',
                padding: 12,
                fontSize: 12,
              }}
            >
              {JSON.stringify(skill.inputSchema, null, 2)}
            </pre>
          </details>
          {mutation.data ? (
            <Alert
              type="success"
              showIcon
              style={{ marginTop: 16 }}
              title="运行完成"
              description={
                <div>
                  <p style={{ marginBottom: 4, wordBreak: 'break-all', fontSize: 12 }}>
                    Audit ID：{mutation.data.auditId}
                  </p>
                  <p style={{ marginBottom: 8, wordBreak: 'break-all', fontSize: 12 }}>
                    Request ID：{mutation.data.requestId ?? '无'}
                  </p>
                  <pre
                    style={{
                      maxHeight: 256,
                      overflow: 'auto',
                      margin: 0,
                      background: 'var(--color-surface)',
                      padding: 12,
                      borderRadius: 8,
                      fontSize: 12,
                    }}
                  >
                    {JSON.stringify(mutation.data.output, null, 2)}
                  </pre>
                </div>
              } />
          ) : null}
        </div>
      </div>
    </Modal>
  )
}

function ToolLogSection() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [selectedId, setSelectedId] = useState('')
  const page = positiveInt(searchParams.get('tool_page'), 1)
  const pageSize = Math.min(positiveInt(searchParams.get('tool_page_size'), 20), 100)
  const toolName = searchParams.get('tool_name') ?? ''
  const status = searchParams.get('tool_status') ?? ''
  const filters = {
    page,
    pageSize,
    toolName: toolName || undefined,
    status: status || undefined,
  }
  const logs = useToolLogsQuery(filters)
  const tools = useToolsQuery()

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(key, value)
    else next.delete(key)
    next.set('tool_page', '1')
    setSearchParams(next, { replace: true })
  }

  const columns: ColumnsType<ToolCallLog> = [
    {
      title: '时间',
      dataIndex: 'createdAt',
      width: 170,
      render: (value: string) => formatDate(value),
    },
    {
      title: 'Tool / Agent',
      key: 'tool',
      render: (_, log) => (
        <div>
          <div style={{ fontWeight: 600 }}>{log.toolName}</div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {log.agentName}
          </Typography.Text>
        </div>
      ),
    },
    {
      title: '调用者',
      dataIndex: 'userId',
      width: 120,
      render: (value?: string | null) => value ?? '系统',
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (value: string) => (
        <Tag color={value === 'success' ? 'success' : value === 'failed' ? 'error' : 'default'}>
          {toolLogStatusLabel[value] ?? value}
        </Tag>
      ),
    },
    {
      title: '耗时',
      dataIndex: 'latencyMs',
      width: 100,
      render: (value: number) => `${value} ms`,
    },
    {
      title: '关联请求',
      key: 'refs',
      render: (_, log) => (
        <div style={{ fontSize: 12 }}>
          <div style={{ wordBreak: 'break-all' }}>Request：{log.requestId ?? '无'}</div>
          <div style={{ wordBreak: 'break-all', marginTop: 4 }}>
            Conversation：{log.conversationId ?? '无'}
          </div>
        </div>
      ),
    },
    {
      title: '详情',
      key: 'actions',
      width: 100,
      render: (_, log) => (
        <Button size="small" autoInsertSpace={false} onClick={() => setSelectedId(log.id)}>
          查看
        </Button>
      ),
    },
  ]

  return (
    <Card title="Tool 调用日志">
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        参数、结果及错误在持久化前由后端脱敏；详情默认折叠。
      </Typography.Paragraph>
      <Space wrap style={{ marginBottom: 16 }}>
        <Select
          allowClear
          placeholder="全部 Tool"
          style={{ width: 200 }}
          value={toolName || undefined}
          onChange={(value) => setFilter('tool_name', value ?? '')}
          options={(tools.data ?? []).map((tool) => ({
            value: tool.name,
            label: tool.name,
          }))} />
        <Select
          allowClear
          placeholder="全部状态"
          style={{ width: 160 }}
          value={status || undefined}
          onChange={(value) => setFilter('tool_status', value ?? '')}
          options={[
            { value: 'success', label: 'success' },
            { value: 'failed', label: 'failed' },
          ]} />
      </Space>

      {logs.isError ? (
        <Alert
          type="error"
          showIcon
          title="Tool 日志加载失败"
          description={logs.error.message}
          action={
            <Button size="small" onClick={() => void logs.refetch()}>
              重新加载
            </Button>
          } />
      ) : (
        <Table
          rowKey="id"
          loading={logs.isPending}
          columns={columns}
          dataSource={logs.data?.items ?? []}
          locale={{ emptyText: <Empty description="没有符合条件的 Tool 调用日志。" /> }}
          pagination={{
            current: page,
            pageSize,
            total: logs.data?.total ?? 0,
            showSizeChanger: false,
            showTotal: (total) => `共 ${total} 条`,
            onChange: (nextPage) => {
              const next = new URLSearchParams(searchParams)
              next.set('tool_page', String(nextPage))
              setSearchParams(next)
            },
          }} />
      )}
      <ToolLogDialog
        id={selectedId}
        onOpenChange={(open) => {
          if (!open) setSelectedId('')
        }} />
    </Card>
  )
}

function ToolLogDialog({
  id,
  onOpenChange,
}: {
  id: string
  onOpenChange: (open: boolean) => void
}) {
  const query = useToolLogQuery(id)

  return (
    <Modal
      title="Tool 日志详情"
      open={Boolean(id)}
      onCancel={() => onOpenChange(false)}
      footer={null}
      width={800}
      destroyOnHidden
    >
      <Typography.Paragraph type="secondary">
        以下内容由后端递归脱敏后返回。
      </Typography.Paragraph>
      {query.isPending ? (
        <LoadingState message="正在加载详情…" minH="min-h-0" />
      ) : query.isError ? (
        <Alert type="error" showIcon message={query.error.message} />
      ) : query.data ? (
        <Space orientation="vertical" size={16} style={{ width: '100%' }}>
          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label="Tool">{query.data.toolName}</Descriptions.Item>
            <Descriptions.Item label="状态">{query.data.status}</Descriptions.Item>
            <Descriptions.Item label="调用者">{query.data.userId ?? '系统'}</Descriptions.Item>
            <Descriptions.Item label="耗时">{query.data.latencyMs} ms</Descriptions.Item>
          </Descriptions>
          <div>
            <Space style={{ width: '100%', justifyContent: 'space-between' }}>
              <Typography.Text strong>Request ID</Typography.Text>
              {query.data.requestId ? (
                <Button
                  size="small"
                  icon={<Copy size={16} weight="duotone" />}
                  onClick={() => {
                    void navigator.clipboard?.writeText(query.data.requestId ?? '')
                    staticMessage.success('已复制')
                  }}
                >
                  复制
                </Button>
              ) : null}
            </Space>
            <Typography.Paragraph style={{ marginTop: 8, wordBreak: 'break-all' }}>
              {query.data.requestId ?? '无'}
            </Typography.Paragraph>
          </div>
          {query.data.errorMessage ? (
            <Alert type="error" showIcon title={query.data.errorMessage} />
          ) : null}
          <details className="pf-surface-muted">
            <summary style={{ cursor: 'pointer', padding: 12, fontWeight: 600 }}>脱敏输入参数</summary>
            <pre
              style={{
                maxHeight: 320,
                overflow: 'auto',
                margin: 0,
                borderTop: '1px solid var(--color-border-secondary)',
                padding: 12,
                fontSize: 12,
              }}
            >
              {JSON.stringify(query.data.inputSummary, null, 2)}
            </pre>
          </details>
          <details className="pf-surface-muted">
            <summary style={{ cursor: 'pointer', padding: 12, fontWeight: 600 }}>脱敏输出结果</summary>
            <pre
              style={{
                maxHeight: 320,
                overflow: 'auto',
                margin: 0,
                borderTop: '1px solid var(--color-border-secondary)',
                padding: 12,
                fontSize: 12,
              }}
            >
              {JSON.stringify(query.data.outputSummary, null, 2)}
            </pre>
          </details>
        </Space>
      ) : null}
    </Modal>
  )
}
