import * as Dialog from '@radix-ui/react-dialog'
import { Activity, Cable, Pencil, Plus, ShieldAlert, X } from 'lucide-react'
import { useState } from 'react'
import type { MCPServer, MCPServerInput, MCPServerUpdate } from '../../api/mcp'
import { Button } from '../../components/ui/button'
import { Alert } from '../../components/feedback/alert'
import { EmptyState, LoadingState } from '../../components/feedback/state-views'
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

function healthClass(status: string): string {
  if (status === 'healthy') return 'bg-emerald-50 text-emerald-700'
  if (status === 'unhealthy') return 'bg-red-50 text-red-700'
  return 'bg-slate-100 text-slate-700'
}

export function IntegrationsPage() {
  const query = useMCPServersQuery()
  const health = useMCPHealthMutation()
  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState<MCPServer | null>(null)

  return (
    <section>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-2xl font-semibold">MCP 集成</h2>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
            配置 Mock 或外部连接元数据。当前 MVP 仅执行安全的内置 Mock 工具，不进行真实第三方写入。
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}><Plus className="size-4" />创建 MCP</Button>
      </div>

      <div className="mt-4 flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
        <ShieldAlert className="mt-0.5 size-5 shrink-0" />
        <p>
          外部 HTTP/stdio 配置仅用于契约与健康状态展示，后端会返回“尚未配置”，不会连接或写入外部系统。
          Secret 不会由 API 明文回显。
        </p>
      </div>

      {query.isPending ? (
        <div className="mt-6 rounded-xl border border-[var(--color-border)] bg-white">
          <LoadingState message="正在加载 MCP 集成…" minH="min-h-56" />
        </div>
      ) : query.isError ? (
        <div className="mt-6">
          <Alert tone="danger" title="MCP 集成加载失败" action={<Button onClick={() => void query.refetch()}>重新加载</Button>}>
            <p>{query.error.message}</p>
          </Alert>
        </div>
      ) : query.data.length === 0 ? (
        <div className="mt-6 rounded-xl border border-dashed border-[var(--color-border)] bg-white">
          <EmptyState
            icon={<Cable aria-hidden="true" className="size-8" />}
            title="尚未配置 MCP 集成"
            hint="可先创建一个 Mock 集成进行安全联调。"
            minH="min-h-56"
          />
        </div>
      ) : (
        <div className="mt-6 grid gap-4 xl:grid-cols-2">
          {query.data.map((server) => (
            <article key={server.id} className="rounded-xl border border-[var(--color-border)] bg-white p-5 shadow-sm">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-lg font-semibold">{server.name}</h3>
                    <span className={`rounded-full px-2 py-1 text-xs font-semibold ${
                      server.type === 'mock' ? 'bg-violet-50 text-violet-700' : 'bg-blue-50 text-blue-700'
                    }`}>
                      {server.type === 'mock' ? 'MOCK' : 'EXTERNAL'}
                    </span>
                    <span className={`rounded-full px-2 py-1 text-xs font-semibold ${healthClass(server.healthStatus)}`}>
                      {server.healthStatus}
                    </span>
                  </div>
                  <p className="mt-2 text-sm text-[var(--color-text-secondary)]">
                    {server.integrationMode} · {server.enabled ? '已启用' : '已禁用'}
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button
                    variant="secondary"
                    className="min-h-9 px-3 py-1 text-xs"
                    onClick={() => setEditing(server)}
                  >
                    <Pencil className="size-3" />编辑
                  </Button>
                  <Button
                    className="min-h-9 px-3 py-1"
                    disabled={health.isPending}
                    onClick={() => health.mutate(server.id)}
                  >
                    <Activity className="size-3" />健康检查
                  </Button>
                </div>
              </div>
              <dl className="mt-5 grid gap-3 text-sm sm:grid-cols-2">
                <div><dt className="font-semibold">Endpoint</dt><dd className="mt-1 break-all">{server.endpoint ?? '无'}</dd></div>
                <div><dt className="font-semibold">Command</dt><dd className="mt-1">{server.commandConfigured ? '已安全配置' : '未配置'}</dd></div>
                <div><dt className="font-semibold">最近检查</dt><dd className="mt-1">{formatDate(server.lastCheckedAt)}</dd></div>
                <div><dt className="font-semibold">工具数量</dt><dd className="mt-1">{server.tools.length}</dd></div>
              </dl>
              {server.lastErrorMessage ? (
                <Alert tone="danger" className="mt-4">
                  {server.lastErrorCode ? `${server.lastErrorCode}：` : ''}{server.lastErrorMessage}
                </Alert>
              ) : null}
              <details className="mt-4 rounded-lg border border-[var(--color-border)]">
                <summary className="cursor-pointer p-3 font-semibold">工具列表与脱敏配置摘要</summary>
                <div className="space-y-3 border-t border-[var(--color-border)] p-3 text-xs">
                  <div>
                    <p className="font-semibold">Tools</p>
                    {server.tools.length ? (
                      <ul className="mt-1 list-inside list-disc">{server.tools.map((tool) => <li key={tool}>{tool}</li>)}</ul>
                    ) : <p className="mt-1">健康检查后显示。</p>}
                  </div>
                  <pre className="max-h-52 overflow-auto rounded bg-slate-50 p-3">
                    {JSON.stringify(server.configSummary, null, 2)}
                  </pre>
                </div>
              </details>
            </article>
          ))}
        </div>
      )}

      {health.isError ? (
        <Alert tone="danger" className="mt-4">健康检查失败：{health.error.message}</Alert>
      ) : null}
      {health.data ? (
        <p role="status" className="mt-4 rounded-md bg-emerald-50 p-3 text-sm text-emerald-800">
          健康检查完成：{health.data.healthStatus}，发现 {health.data.tools.length} 个工具。
        </p>
      ) : null}
      <MCPServerDialog open={createOpen} onOpenChange={setCreateOpen} />
      {editing ? (
        <MCPServerDialog
          key={editing.id}
          open
          server={editing}
          onOpenChange={(open) => { if (!open) setEditing(null) }}
        />
      ) : null}
    </section>
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

  function validateEndpoint(): boolean {
    if (integrationMode !== 'http') return true
    try {
      const parsed = new URL(endpoint)
      return ['http:', 'https:'].includes(parsed.protocol)
        && !parsed.username && !parsed.password && !parsed.search && !parsed.hash
    } catch {
      return false
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setFormError('')
    if (!name.trim()) {
      setFormError('请输入名称。')
      return
    }
    if (integrationMode === 'http' && !validateEndpoint()) {
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
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-slate-950/50" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[calc(100%-32px)] max-w-2xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl bg-white p-6 shadow-xl">
          <Dialog.Title className="text-lg font-semibold">{server ? '编辑 MCP 集成' : '创建 MCP 集成'}</Dialog.Title>
          <Dialog.Description className="mt-1 text-sm text-[var(--color-text-secondary)]">
            Secret 仅在本次提交中发送；保存后 API 只返回脱敏摘要。
          </Dialog.Description>
          <Dialog.Close aria-label="关闭 MCP 对话框" className="absolute right-4 top-4"><X className="size-5" /></Dialog.Close>
          <form onSubmit={(event) => void submit(event)} className="mt-5 space-y-4">
            <Field label="名称" value={name} onChange={setName} required />
            <label className="block text-sm font-semibold">
              集成模式
              <select
                value={integrationMode}
                onChange={(event) => setIntegrationMode(event.target.value as 'mock' | 'stdio' | 'http')}
                className="mt-2 min-h-10 w-full rounded-md border border-[var(--color-border)] px-3 font-normal"
              >
                <option value="mock">mock（MVP 可执行）</option>
                <option value="http">http（仅配置，不连接）</option>
                <option value="stdio">stdio（仅配置，不执行）</option>
              </select>
            </label>
            {integrationMode === 'http' ? (
              <Field label="Endpoint" value={endpoint} onChange={setEndpoint} placeholder="https://mcp.example.com/api" required />
            ) : null}
            {integrationMode === 'stdio' || server?.commandConfigured ? (
              <Field
                label={server?.commandConfigured ? '替换 Command（留空保留现有值）' : 'Command'}
                value={command}
                onChange={setCommand}
                type="password"
                autoComplete="new-password"
                required={integrationMode === 'stdio' && !server?.commandConfigured}
              />
            ) : null}
            <label className="block text-sm font-semibold">
              {server ? '替换 Config JSON（留空保留现有 Secret）' : 'Config JSON'}
              <textarea
                value={configText}
                onChange={(event) => setConfigText(event.target.value)}
                rows={7}
                spellCheck={false}
                className="mt-2 w-full rounded-md border border-[var(--color-border)] p-3 font-mono text-xs font-normal"
              />
            </label>
            {server ? (
              <details className="rounded-md border border-[var(--color-border)]">
                <summary className="cursor-pointer p-3 text-sm font-semibold">当前脱敏配置摘要</summary>
                <pre className="overflow-auto border-t p-3 text-xs">{JSON.stringify(server.configSummary, null, 2)}</pre>
              </details>
            ) : null}
            <label className="flex items-center gap-2 text-sm font-semibold">
              <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
              启用集成
            </label>
            {formError ? <Alert tone="danger">{formError}</Alert> : null}
            {mutationError ? <Alert tone="danger">{mutationError.message}</Alert> : null}
            <div className="flex justify-end gap-3">
              <Dialog.Close asChild>
                <Button variant="secondary">取消</Button>
              </Dialog.Close>
              <Button type="submit" disabled={pending}>{pending ? '正在保存…' : '保存'}</Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function Field({
  label,
  value,
  onChange,
  required,
  placeholder,
  type = 'text',
  autoComplete,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  required?: boolean
  placeholder?: string
  type?: string
  autoComplete?: string
}) {
  return (
    <label className="block text-sm font-semibold">
      {label}
      <input
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        required={required}
        placeholder={placeholder}
        autoComplete={autoComplete}
        className="mt-2 min-h-10 w-full rounded-md border border-[var(--color-border)] px-3 font-normal"
      />
    </label>
  )
}
