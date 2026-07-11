import * as Dialog from '@radix-ui/react-dialog'
import { Activity, Braces, Clipboard, Play, RefreshCw, Search, X } from 'lucide-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { Skill } from '../../api/skills'
import { Button } from '../../components/ui/button'
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
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'short',
    timeStyle: 'medium',
  }).format(new Date(value))
}

function statusClass(value: string): string {
  if (value === 'success' || value === 'enabled') return 'bg-emerald-50 text-emerald-700'
  if (value === 'failed' || value === 'disabled') return 'bg-red-50 text-red-700'
  return 'bg-slate-100 text-slate-700'
}

export function SkillsPage() {
  return (
    <section>
      <div>
        <h2 className="text-2xl font-semibold">Skill 管理</h2>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          管理已登记 Skill，并查看经过后端递归脱敏的 Tool 调用日志。
        </p>
      </div>
      <div className="mt-6 space-y-8">
        <SkillRegistry />
        <ToolLogSection />
      </div>
    </section>
  )
}

function SkillRegistry() {
  const query = useSkillsQuery()
  const toggle = useSetSkillEnabledMutation()
  const [runningSkill, setRunningSkill] = useState<Skill | null>(null)

  async function changeStatus(skill: Skill) {
    const enabled = !skill.enabled
    const confirmed = window.confirm(
      `确认${enabled ? '启用' : '禁用'} Skill“${skill.name}”吗？该操作会写入审计日志。`,
    )
    if (!confirmed) return
    await toggle.mutateAsync({ name: skill.name, enabled })
  }

  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-white shadow-sm">
      <div className="border-b border-[var(--color-border)] p-5">
        <h3 className="text-lg font-semibold">Skill 注册表</h3>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          仅标记为“已实现”的 Skill 可手动运行；参数由后端 Schema 校验。
        </p>
      </div>
      {query.isPending ? (
        <div role="status" className="flex min-h-48 items-center justify-center gap-2">
          <RefreshCw className="size-4 animate-spin motion-reduce:animate-none" />
          正在加载 Skill…
        </div>
      ) : query.isError ? (
        <div role="alert" className="m-5 rounded-lg bg-red-50 p-4">
          <p className="font-semibold">Skill 加载失败</p>
          <p className="mt-1 text-sm">{query.error.message}</p>
          <Button className="mt-3" onClick={() => void query.refetch()}>重新加载</Button>
        </div>
      ) : query.data.length === 0 ? (
        <div className="grid min-h-48 place-items-center p-6 text-center">
          <p>尚未登记 Skill。</p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full text-left text-sm">
            <thead className="bg-slate-50 text-xs text-[var(--color-text-secondary)]">
              <tr>
                {['名称 / 描述', '版本', '风险', '实现状态', '启用状态', '配置', '操作'].map(
                  (heading) => <th key={heading} className="px-4 py-3">{heading}</th>,
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--color-border)]">
              {query.data.map((skill) => (
                <tr key={skill.name}>
                  <td className="max-w-sm px-4 py-3">
                    <p className="font-semibold">{skill.name}</p>
                    <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
                      {skill.description}
                    </p>
                  </td>
                  <td className="px-4 py-3">{skill.version}</td>
                  <td className="px-4 py-3">{skill.riskLevel}</td>
                  <td className="px-4 py-3">
                    <span className={`rounded-full px-2 py-1 text-xs font-semibold ${
                      skill.implemented ? 'bg-blue-50 text-blue-700' : 'bg-slate-100 text-slate-600'
                    }`}>
                      {skill.implemented ? '已实现' : '未实现'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`rounded-full px-2 py-1 text-xs font-semibold ${
                      statusClass(skill.enabled ? 'enabled' : 'disabled')
                    }`}>
                      {skill.enabled ? '已启用' : '已禁用'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <details>
                      <summary className="cursor-pointer text-xs font-semibold">查看摘要</summary>
                      <pre className="mt-2 max-w-xs overflow-auto rounded bg-slate-50 p-2 text-xs">
                        {JSON.stringify(skill.configSummary, null, 2)}
                      </pre>
                    </details>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-2">
                      <Button
                        className="min-h-8 bg-white py-1 text-xs text-[var(--color-text-primary)] ring-1 ring-[var(--color-border)] hover:bg-slate-50"
                        disabled={toggle.isPending}
                        onClick={() => void changeStatus(skill)}
                      >
                        {skill.enabled ? '禁用' : '启用'}
                      </Button>
                      <Button
                        className="min-h-8 py-1 text-xs"
                        disabled={!skill.runnable}
                        title={!skill.implemented ? '后端尚未实现该 Skill' : undefined}
                        onClick={() => setRunningSkill(skill)}
                      >
                        <Play className="size-3" />手动运行
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {toggle.isError ? (
        <p role="alert" className="border-t border-[var(--color-border)] p-4 text-sm text-[var(--color-danger)]">
          {toggle.error.message}
        </p>
      ) : null}
      {runningSkill ? (
        <SkillRunDialog
          key={runningSkill.name}
          skill={runningSkill}
          open
          onOpenChange={(open) => { if (!open) setRunningSkill(null) }}
        />
      ) : null}
    </div>
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
  const [payload, setPayload] = useState(() => JSON.stringify(defaultSkillInput(skill), null, 2))
  const [parseError, setParseError] = useState('')

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setParseError('')
    let input: unknown
    try {
      input = JSON.parse(payload)
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
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-slate-950/50" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[calc(100%-32px)] max-w-3xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl bg-white p-6 shadow-xl">
          <Dialog.Title className="text-lg font-semibold">运行 {skill.name}</Dialog.Title>
          <Dialog.Description className="mt-1 text-sm text-[var(--color-text-secondary)]">
            仅提交符合后端输入 Schema 的 JSON，不执行任何前端代码。
          </Dialog.Description>
          <Dialog.Close aria-label="关闭运行对话框" className="absolute right-4 top-4">
            <X className="size-5" />
          </Dialog.Close>
          <form onSubmit={(event) => void submit(event)} className="mt-5 grid gap-5 lg:grid-cols-2">
            <div>
              <label className="text-sm font-semibold" htmlFor="skill-run-input">运行参数</label>
              <textarea
                id="skill-run-input"
                value={payload}
                onChange={(event) => setPayload(event.target.value)}
                rows={14}
                spellCheck={false}
                className="mt-2 w-full rounded-md border border-[var(--color-border)] p-3 font-mono text-xs"
              />
              {parseError ? <p role="alert" className="mt-2 text-sm text-[var(--color-danger)]">{parseError}</p> : null}
              {mutation.isError ? <p role="alert" className="mt-2 text-sm text-[var(--color-danger)]">{mutation.error.message}</p> : null}
              <Button type="submit" className="mt-3" disabled={mutation.isPending}>
                <Play className="size-4" />{mutation.isPending ? '正在运行…' : '确认运行'}
              </Button>
            </div>
            <div className="space-y-4">
              <details open className="rounded-lg border border-[var(--color-border)]">
                <summary className="cursor-pointer p-3 font-semibold">
                  <span className="inline-flex items-center gap-2"><Braces className="size-4" />输入 Schema</span>
                </summary>
                <pre className="max-h-64 overflow-auto border-t border-[var(--color-border)] p-3 text-xs">
                  {JSON.stringify(skill.inputSchema, null, 2)}
                </pre>
              </details>
              {mutation.data ? (
                <div role="status" className="rounded-lg border border-emerald-200 bg-emerald-50 p-4">
                  <h3 className="font-semibold text-emerald-800">运行完成</h3>
                  <p className="mt-2 break-all text-xs">Audit ID：{mutation.data.auditId}</p>
                  <p className="mt-1 break-all text-xs">Request ID：{mutation.data.requestId ?? '无'}</p>
                  <pre className="mt-3 max-h-64 overflow-auto rounded bg-white p-3 text-xs">
                    {JSON.stringify(mutation.data.output, null, 2)}
                  </pre>
                </div>
              ) : null}
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function ToolLogSection() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [selectedId, setSelectedId] = useState('')
  const page = positiveInt(searchParams.get('tool_page'), 1)
  const pageSize = Math.min(positiveInt(searchParams.get('tool_page_size'), 20), 100)
  const toolName = searchParams.get('tool_name') ?? ''
  const status = searchParams.get('tool_status') ?? ''
  const filters = { page, pageSize, toolName: toolName || undefined, status: status || undefined }
  const logs = useToolLogsQuery(filters)
  const tools = useToolsQuery()
  const totalPages = Math.max(1, Math.ceil((logs.data?.total ?? 0) / pageSize))

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(key, value)
    else next.delete(key)
    next.set('tool_page', '1')
    setSearchParams(next, { replace: true })
  }

  function goToPage(nextPage: number) {
    const next = new URLSearchParams(searchParams)
    next.set('tool_page', String(nextPage))
    setSearchParams(next)
  }

  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-white shadow-sm">
      <div className="border-b border-[var(--color-border)] p-5">
        <h3 className="flex items-center gap-2 text-lg font-semibold">
          <Activity className="size-5" />Tool 调用日志
        </h3>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          参数、结果及错误在持久化前由后端脱敏；详情默认折叠。
        </p>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <label className="text-sm font-semibold">
            Tool
            <select
              value={toolName}
              onChange={(event) => setFilter('tool_name', event.target.value)}
              className="mt-2 min-h-10 w-full rounded-md border border-[var(--color-border)] px-3 font-normal"
            >
              <option value="">全部 Tool</option>
              {tools.data?.map((tool) => <option key={tool.name} value={tool.name}>{tool.name}</option>)}
            </select>
          </label>
          <label className="text-sm font-semibold">
            状态
            <select
              value={status}
              onChange={(event) => setFilter('tool_status', event.target.value)}
              className="mt-2 min-h-10 w-full rounded-md border border-[var(--color-border)] px-3 font-normal"
            >
              <option value="">全部状态</option>
              <option value="success">success</option>
              <option value="failed">failed</option>
            </select>
          </label>
        </div>
      </div>
      {logs.isPending ? (
        <div role="status" className="grid min-h-48 place-items-center">正在加载 Tool 日志…</div>
      ) : logs.isError ? (
        <div role="alert" className="m-5 rounded-lg bg-red-50 p-4">
          <p className="font-semibold">Tool 日志加载失败</p>
          <p className="mt-1 text-sm">{logs.error.message}</p>
          <Button className="mt-3" onClick={() => void logs.refetch()}>重新加载</Button>
        </div>
      ) : logs.data.items.length === 0 ? (
        <div className="grid min-h-48 place-items-center p-6 text-center">
          <p>没有符合条件的 Tool 调用日志。</p>
        </div>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead className="bg-slate-50 text-xs text-[var(--color-text-secondary)]">
                <tr>
                  {['时间', 'Tool / Agent', '调用者', '状态', '耗时', '关联请求', '详情'].map(
                    (heading) => <th key={heading} className="px-4 py-3">{heading}</th>,
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {logs.data.items.map((log) => (
                  <tr key={log.id}>
                    <td className="whitespace-nowrap px-4 py-3">{formatDate(log.createdAt)}</td>
                    <td className="px-4 py-3">
                      <p className="font-semibold">{log.toolName}</p>
                      <p className="text-xs text-[var(--color-text-secondary)]">{log.agentName}</p>
                    </td>
                    <td className="px-4 py-3">{log.userId ?? '系统'}</td>
                    <td className="px-4 py-3">
                      <span className={`rounded-full px-2 py-1 text-xs font-semibold ${statusClass(log.status)}`}>
                        {log.status}
                      </span>
                    </td>
                    <td className="px-4 py-3">{log.latencyMs} ms</td>
                    <td className="max-w-xs px-4 py-3 text-xs">
                      <p className="break-all">Request：{log.requestId ?? '无'}</p>
                      <p className="mt-1 break-all">Conversation：{log.conversationId ?? '无'}</p>
                    </td>
                    <td className="px-4 py-3">
                      <Button className="min-h-8 py-1 text-xs" onClick={() => setSelectedId(log.id)}>
                        <Search className="size-3" />查看
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between border-t border-[var(--color-border)] p-4">
            <p className="text-sm">共 {logs.data.total} 条，第 {page} / {totalPages} 页</p>
            <div className="flex gap-2">
              <Button disabled={page <= 1} onClick={() => goToPage(page - 1)}>上一页</Button>
              <Button disabled={page >= totalPages} onClick={() => goToPage(page + 1)}>下一页</Button>
            </div>
          </div>
        </>
      )}
      <ToolLogDialog id={selectedId} onOpenChange={(open) => { if (!open) setSelectedId('') }} />
    </div>
  )
}

function ToolLogDialog({ id, onOpenChange }: { id: string; onOpenChange: (open: boolean) => void }) {
  const query = useToolLogQuery(id)
  return (
    <Dialog.Root open={Boolean(id)} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-slate-950/50" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[calc(100%-32px)] max-w-3xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl bg-white p-6 shadow-xl">
          <Dialog.Title className="text-lg font-semibold">Tool 日志详情</Dialog.Title>
          <Dialog.Description className="mt-1 text-sm text-[var(--color-text-secondary)]">
            以下内容由后端递归脱敏后返回。
          </Dialog.Description>
          <Dialog.Close aria-label="关闭 Tool 日志详情" className="absolute right-4 top-4">
            <X className="size-5" />
          </Dialog.Close>
          {query.isPending ? (
            <p role="status" className="mt-5">正在加载详情…</p>
          ) : query.isError ? (
            <p role="alert" className="mt-5 text-[var(--color-danger)]">{query.error.message}</p>
          ) : query.data ? (
            <div className="mt-5 space-y-4 text-sm">
              <dl className="grid gap-3 sm:grid-cols-2">
                <div><dt className="font-semibold">Tool</dt><dd>{query.data.toolName}</dd></div>
                <div><dt className="font-semibold">状态</dt><dd>{query.data.status}</dd></div>
                <div><dt className="font-semibold">调用者</dt><dd>{query.data.userId ?? '系统'}</dd></div>
                <div><dt className="font-semibold">耗时</dt><dd>{query.data.latencyMs} ms</dd></div>
              </dl>
              <div>
                <div className="flex items-center justify-between">
                  <h3 className="font-semibold">Request ID</h3>
                  {query.data.requestId ? (
                    <Button
                      className="min-h-8 py-1 text-xs"
                      onClick={() => void navigator.clipboard?.writeText(query.data.requestId ?? '')}
                    >
                      <Clipboard className="size-3" />复制
                    </Button>
                  ) : null}
                </div>
                <p className="mt-1 break-all">{query.data.requestId ?? '无'}</p>
              </div>
              {query.data.errorMessage ? (
                <p role="alert" className="rounded-md bg-red-50 p-3 text-[var(--color-danger)]">
                  {query.data.errorMessage}
                </p>
              ) : null}
              <JsonDetails title="脱敏输入参数" value={query.data.inputSummary} />
              <JsonDetails title="脱敏输出结果" value={query.data.outputSummary} />
            </div>
          ) : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function JsonDetails({ title, value }: { title: string; value: Record<string, unknown> }) {
  return (
    <details className="rounded-lg border border-[var(--color-border)]">
      <summary className="cursor-pointer p-3 font-semibold">{title}</summary>
      <pre className="max-h-80 overflow-auto border-t border-[var(--color-border)] p-3 text-xs">
        {JSON.stringify(value, null, 2)}
      </pre>
    </details>
  )
}
