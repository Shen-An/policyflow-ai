import {
  ApiOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Descriptions,
  Empty,
  Form,
  Input,
  Modal,
  Row,
  Space,
  Tag,
  Typography,
} from 'antd'
import { useEffect, useState } from 'react'
import type { MCPServer, MCPServerInput, MCPServerUpdate } from '../../api/mcp'
import { LoadingState } from '../../components/feedback/state-views'
import {
  useCreateMCPServerMutation,
  useMCPHealthMutation,
  useMCPServersQuery,
  useUpdateMCPServerMutation,
} from './queries'

function formatDate(value: string | null): string {
  if (!value) return '尚未检查'
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'medium',
  }).format(new Date(value))
}

function healthColor(status: string): string {
  if (status === 'healthy') return 'success'
  if (status === 'unhealthy') return 'error'
  return 'default'
}

export function IntegrationsPage() {
  const query = useMCPServersQuery()
  const health = useMCPHealthMutation()
  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState<MCPServer | null>(null)

  return (
    <div>
      <div className="page-header">
        <div>
          <h2>MCP 集成</h2>
          <p>
            配置 Mock 或外部连接元数据。当前 MVP 仅执行安全的内置 Mock 工具，不进行真实第三方写入。
          </p>
        </div>
        <Button type="primary" autoInsertSpace={false} onClick={() => setCreateOpen(true)}>
          创建 MCP
        </Button>
      </div>

      <Alert
        type="warning"
        showIcon
        icon={<WarningOutlined />}
        style={{ marginBottom: 16 }}
        title="MCP 集成说明"
        description="外部 HTTP/stdio 配置仅用于契约与健康状态展示，后端会返回“尚未配置”，不会连接或写入外部系统。Secret 不会由 API 明文回显。"
      />

      {query.isPending ? (
        <Card>
          <LoadingState message="正在加载 MCP 集成…" minH="min-h-56" />
        </Card>
      ) : query.isError ? (
        <Alert
          type="error"
          showIcon
          message="MCP 集成加载失败"
          description={query.error.message}
          action={
            <Button size="small" onClick={() => void query.refetch()}>
              重新加载
            </Button>
          }
        />
      ) : query.data.length === 0 ? (
        <Card>
          <Empty
            image={<ApiOutlined className="pf-muted-icon" style={{ fontSize: 40 }} />}
            description={
              <span>
                尚未配置 MCP 集成
                <br />
                <Typography.Text type="secondary">可先创建一个 Mock 集成进行安全联调。</Typography.Text>
              </span>
            }
          />
        </Card>
      ) : (
        <Row gutter={[16, 16]}>
          {query.data.map((server) => (
            <Col key={server.id} xs={24} xl={12}>
              <Card
                title={
                  <Space wrap>
                    <span>{server.name}</span>
                    <Tag color={server.type === 'mock' ? 'purple' : 'blue'}>
                      {server.type === 'mock' ? 'MOCK' : 'EXTERNAL'}
                    </Tag>
                    <Tag color={healthColor(server.healthStatus)}>{server.healthStatus}</Tag>
                  </Space>
                }
                extra={
                  <Space>
                    <Button size="small" autoInsertSpace={false} onClick={() => setEditing(server)}>
                      编辑
                    </Button>
                    <Button
                      size="small"
                      type="primary"
                      autoInsertSpace={false}
                      loading={health.isPending}
                      onClick={() => health.mutate(server.id)}
                    >
                      健康检查
                    </Button>
                  </Space>
                }
              >
                <Typography.Text type="secondary">
                  {server.integrationMode} · {server.enabled ? '已启用' : '已禁用'}
                </Typography.Text>
                <Descriptions size="small" column={2} style={{ marginTop: 16 }} bordered>
                  <Descriptions.Item label="Endpoint">
                    {server.endpoint ?? '无'}
                  </Descriptions.Item>
                  <Descriptions.Item label="Command">
                    {server.commandConfigured ? '已安全配置' : '未配置'}
                  </Descriptions.Item>
                  <Descriptions.Item label="最近检查">
                    {formatDate(server.lastCheckedAt)}
                  </Descriptions.Item>
                  <Descriptions.Item label="工具数量">{server.tools.length}</Descriptions.Item>
                </Descriptions>
                {server.lastErrorMessage ? (
                  <Alert
                    type="error"
                    showIcon
                    style={{ marginTop: 12 }}
                    title={
                      server.lastErrorCode
                        ? `${server.lastErrorCode}：${server.lastErrorMessage}`
                        : server.lastErrorMessage
                    }
                  />
                ) : null}
                <details className="pf-surface-muted" style={{ marginTop: 12 }}>
                  <summary style={{ cursor: 'pointer', padding: 12, fontWeight: 600 }}>
                    工具列表与脱敏配置摘要
                  </summary>
                  <div className="pf-divider-top" style={{ padding: 12 }}>
                    <Typography.Text strong>Tools</Typography.Text>
                    {server.tools.length ? (
                      <ul style={{ marginTop: 8, paddingLeft: 20 }}>
                        {server.tools.map((tool) => (
                          <li key={tool}>{tool}</li>
                        ))}
                      </ul>
                    ) : (
                      <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
                        健康检查后显示。
                      </Typography.Paragraph>
                    )}
                    <pre
                      style={{
                        marginTop: 12,
                        maxHeight: 208,
                        overflow: 'auto',
                        background: 'var(--color-surface-muted)',
                        padding: 12,
                        borderRadius: 8,
                        fontSize: 12,
                      }}
                    >
                      {JSON.stringify(server.configSummary, null, 2)}
                    </pre>
                  </div>
                </details>
              </Card>
            </Col>
          ))}
        </Row>
      )}

      {health.isError ? (
        <Alert
          type="error"
          showIcon
          style={{ marginTop: 16 }}
          title={`健康检查失败：${health.error.message}`}
        />
      ) : null}
      {health.data ? (
        <Alert
          type="success"
          showIcon
          style={{ marginTop: 16 }}
          title="健康检查完成"
          description={`状态：${health.data.healthStatus}，发现 ${health.data.tools.length} 个工具。`}
        />
      ) : null}

      <MCPServerDialog open={createOpen} onOpenChange={setCreateOpen} />
      {editing ? (
        <MCPServerDialog
          key={editing.id}
          open
          server={editing}
          onOpenChange={(open) => {
            if (!open) setEditing(null)
          }}
        />
      ) : null}
    </div>
  )
}

function MCPServerDialog({
  open,
  onOpenChange,
  server,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  server?: MCPServer
}) {
  const create = useCreateMCPServerMutation()
  const update = useUpdateMCPServerMutation()
  const [name, setName] = useState(server?.name ?? '')
  const [integrationMode, setIntegrationMode] = useState<'mock' | 'stdio' | 'http'>(
    server?.integrationMode === 'stdio' || server?.integrationMode === 'http'
      ? server.integrationMode
      : 'mock',
  )
  const [endpoint, setEndpoint] = useState(server?.endpoint ?? '')
  const [command, setCommand] = useState('')
  const [enabled, setEnabled] = useState(server?.enabled ?? false)
  const [configText, setConfigText] = useState(server ? '' : '{\n  "mode": "mock"\n}')
  const [formError, setFormError] = useState('')
  const pending = create.isPending || update.isPending
  const mutationError = create.error ?? update.error

  useEffect(() => {
    if (!open) return
    setName(server?.name ?? '')
    setIntegrationMode(
      server?.integrationMode === 'stdio' || server?.integrationMode === 'http'
        ? server.integrationMode
        : 'mock',
    )
    setEndpoint(server?.endpoint ?? '')
    setCommand('')
    setEnabled(server?.enabled ?? false)
    setConfigText(server ? '' : '{\n  "mode": "mock"\n}')
    setFormError('')
  }, [open, server])

  function validateEndpoint(value: string): boolean {
    try {
      const parsed = new URL(value)
      return (
        ['http:', 'https:'].includes(parsed.protocol) &&
        !parsed.username &&
        !parsed.password &&
        !parsed.search &&
        !parsed.hash
      )
    } catch {
      return false
    }
  }

  async function submit() {
    setFormError('')
    if (!name.trim()) {
      setFormError('请输入名称。')
      return
    }
    if (integrationMode === 'http' && !validateEndpoint(endpoint.trim())) {
      setFormError('Endpoint 必须是无凭据、无查询参数的 HTTP(S) 基础 URL。')
      return
    }
    if (integrationMode === 'stdio' && !command.trim() && !server?.commandConfigured) {
      setFormError('stdio 集成必须配置 Command。')
      return
    }

    let config: Record<string, unknown> | undefined
    if (configText.trim()) {
      try {
        const parsed: unknown = JSON.parse(configText)
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error()
        config = parsed as Record<string, unknown>
      } catch {
        setFormError('Config 必须是有效的 JSON 对象。')
        return
      }
    }

    if (server && server.enabled !== enabled) {
      const confirmed = window.confirm(
        `确认将“${server.name}”${enabled ? '启用' : '禁用'}吗？该操作会清空旧健康状态并写入审计日志。`,
      )
      if (!confirmed) return
    }

    const type = integrationMode === 'mock' ? 'mock' : 'external'
    try {
      if (server) {
        const input: MCPServerUpdate = {
          name: name.trim(),
          type,
          integrationMode,
          endpoint: integrationMode === 'http' ? endpoint.trim() : '',
          enabled,
          ...(command.trim() ? { command: command.trim() } : {}),
          ...(config ? { config } : {}),
        }
        await update.mutateAsync({ id: server.id, input })
      } else {
        const input: MCPServerInput = {
          name: name.trim(),
          type,
          integrationMode,
          endpoint: integrationMode === 'http' ? endpoint.trim() : undefined,
          command: integrationMode === 'stdio' || command.trim() ? command.trim() : undefined,
          config: config ?? {},
          enabled,
        }
        await create.mutateAsync(input)
      }
      onOpenChange(false)
    } catch {
      // mutation error is shown below
    }
  }

  return (
    <Modal
      title={server ? '编辑 MCP 集成' : '创建 MCP 集成'}
      open={open}
      onCancel={() => {
        if (!pending) onOpenChange(false)
      }}
      onOk={() => {
        void submit()
      }}
      okText="保存"
      cancelText="取消"
      confirmLoading={pending}
      okButtonProps={{ autoInsertSpace: false }}
      cancelButtonProps={{ autoInsertSpace: false }}
      destroyOnHidden
      width={720}
    >
      <Typography.Paragraph type="secondary">
        Secret 仅在本次提交中发送；保存后 API 只返回脱敏摘要。
      </Typography.Paragraph>
      <Form layout="vertical" requiredMark={false}>
        <Form.Item label="名称" required>
          <Input
            aria-label="名称"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </Form.Item>
        <Form.Item label="集成模式" required>
          <select
            aria-label="集成模式"
            value={integrationMode}
            onChange={(event) =>
              setIntegrationMode(event.target.value as 'mock' | 'stdio' | 'http')
            }
            style={{
              width: '100%',
              minHeight: 36,
              borderRadius: 8,
              border: '1px solid var(--color-border-secondary)',
              padding: '4px 11px',
            }}
          >
            <option value="mock">mock（MVP 可执行）</option>
            <option value="http">http（仅配置，不连接）</option>
            <option value="stdio">stdio（仅配置，不执行）</option>
          </select>
        </Form.Item>
        {integrationMode === 'http' ? (
          <Form.Item label="Endpoint" required>
            <Input
              aria-label="Endpoint"
              placeholder="https://mcp.example.com/api"
              value={endpoint}
              onChange={(event) => setEndpoint(event.target.value)}
            />
          </Form.Item>
        ) : null}
        {integrationMode === 'stdio' || server?.commandConfigured ? (
          <Form.Item
            label={server?.commandConfigured ? '替换 Command（留空保留现有值）' : 'Command'}
            required={integrationMode === 'stdio' && !server?.commandConfigured}
          >
            <Input.Password
              aria-label={
                server?.commandConfigured ? '替换 Command（留空保留现有值）' : 'Command'
              }
              autoComplete="new-password"
              value={command}
              onChange={(event) => setCommand(event.target.value)}
            />
          </Form.Item>
        ) : null}
        <Form.Item
          label={server ? '替换 Config JSON（留空保留现有 Secret）' : 'Config JSON'}
        >
          <Input.TextArea
            rows={7}
            spellCheck={false}
            style={{ fontFamily: 'monospace', fontSize: 12 }}
            value={configText}
            onChange={(event) => setConfigText(event.target.value)}
          />
        </Form.Item>
        {server ? (
          <details style={{ marginBottom: 16, border: '1px solid var(--color-border-secondary)', borderRadius: 8 }}>
            <summary style={{ cursor: 'pointer', padding: 12, fontWeight: 600 }}>
              当前脱敏配置摘要
            </summary>
            <pre
              style={{
                margin: 0,
                overflow: 'auto',
                borderTop: '1px solid var(--color-border-secondary)',
                padding: 12,
                fontSize: 12,
              }}
            >
              {JSON.stringify(server.configSummary, null, 2)}
            </pre>
          </details>
        ) : null}
        <Form.Item>
          <Checkbox checked={enabled} onChange={(event) => setEnabled(event.target.checked)}>
            启用集成
          </Checkbox>
        </Form.Item>
        {formError ? (
          <Alert type="error" showIcon style={{ marginBottom: 12 }} title={formError} />
        ) : null}
        {mutationError ? (
          <Alert type="error" showIcon style={{ marginBottom: 12 }} title={mutationError.message} />
        ) : null}
      </Form>
    </Modal>
  )
}
