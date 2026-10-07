import { useState } from 'react'
import { CaretDown, CaretRight, CheckCircle, CircleNotch, Warning } from '@phosphor-icons/react'
import type { RunEvent } from '../../../../electron/shared/types'

// T130 — a quiet, compact staged timeline fed by ordered run events. It consumes the
// append-only `stage.update` / terminal events and shows a calm chip row by default;
// the step-by-step detail stays collapsed until the user asks for it. Nothing here
// shouts: thinking should feel like a quiet status line, not a wall of logs.

export type ThinkingStage = {
  key: string
  label: string
  status: 'running' | 'done' | 'failed'
}

const STAGE_LABELS: Record<string, string> = {
  planning: '理解问题',
  retrieval: '检索依据',
  generation: '生成答复',
  provisioning: '准备工作区',
  processing: '处理材料',
  changes_ready: '生成草稿',
  awaiting_approval: '等待审批',
  submission: '提交结果',
}

function labelFor(event: RunEvent): string {
  const fromPayload = event.payload?.label
  if (typeof fromPayload === 'string' && fromPayload) return fromPayload
  if (event.stage && STAGE_LABELS[event.stage]) return STAGE_LABELS[event.stage]
  return event.stage ?? event.eventType
}

/** Collapse ordered events into one chip per stage, latest status wins. */
function deriveStages(events: RunEvent[]): ThinkingStage[] {
  const order: string[] = []
  const byKey = new Map<string, ThinkingStage>()
  let terminalFailed = false
  for (const event of events) {
    if (event.eventType === 'run.failed') terminalFailed = true
    if (event.eventType !== 'stage.update' && !event.stage) continue
    const key = event.stage ?? event.eventType
    if (!byKey.has(key)) order.push(key)
    const isTerminalCompleted = event.eventType === 'run.completed'
    byKey.set(key, {
      key,
      label: labelFor(event),
      status: event.status === 'running' && !isTerminalCompleted ? 'running' : 'done',
    })
  }
  const stages = order.map((key) => byKey.get(key)!).filter(Boolean)
  // Once the run has produced any terminal/non-running event, earlier running chips settle.
  const anyTerminal = events.some(
    (e) => e.eventType === 'run.completed' || e.eventType === 'run.failed',
  )
  return stages.map((stage, index) =>
    anyTerminal && index < stages.length
      ? { ...stage, status: terminalFailed && index === stages.length - 1 ? 'failed' : 'done' }
      : stage,
  )
}

function StatusIcon({ status }: { status: ThinkingStage['status'] }) {
  if (status === 'running') {
    return <CircleNotch size={14} weight="bold" className="ds-spin" aria-hidden style={{ color: 'var(--ds-accent)' }} />
  }
  if (status === 'failed') {
    return <Warning size={14} weight="fill" aria-hidden style={{ color: 'var(--ds-danger)' }} />
  }
  return <CheckCircle size={14} weight="fill" aria-hidden style={{ color: 'var(--ds-success)' }} />
}

export function ThinkingProcess({
  events,
  reconnecting = false,
}: {
  events: RunEvent[]
  reconnecting?: boolean
}) {
  const [open, setOpen] = useState(false)
  const stages = deriveStages(events)
  if (stages.length === 0 && !reconnecting) return null

  const active = stages.some((s) => s.status === 'running') || reconnecting
  const summary = reconnecting ? '正在重新连接…' : active ? '正在处理…' : '处理完成'

  return (
    <section
      data-testid="thinking-process"
      aria-label="思考过程"
      style={{
        border: '1px solid var(--ds-border)',
        borderRadius: 'var(--ds-radius-md)',
        background: 'var(--ds-card-muted)',
        padding: 'var(--ds-space-2) var(--ds-space-3)',
        margin: '0 0 var(--ds-space-3)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--ds-space-2)', flexWrap: 'wrap' }}>
        <button
          type="button"
          data-testid="thinking-toggle"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="ds-focusable"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 4,
            border: 'none',
            background: 'transparent',
            cursor: 'pointer',
            color: 'var(--ds-text-secondary)',
            fontSize: 'var(--ds-text-sm)',
            fontWeight: 600,
            padding: '2px 4px',
            borderRadius: 'var(--ds-radius-sm)',
          }}
        >
          {open ? <CaretDown size={12} aria-hidden /> : <CaretRight size={12} aria-hidden />}
          {summary}
        </button>
        {/* Compact chip row — always visible, quiet. */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          {stages.map((stage) => (
            <span
              key={stage.key}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
                fontSize: 'var(--ds-text-xs)',
                color: 'var(--ds-text-secondary)',
                background: 'var(--ds-card)',
                border: '1px solid var(--ds-border)',
                borderRadius: 'var(--ds-radius-pill)',
                padding: '2px 8px',
              }}
            >
              <StatusIcon status={stage.status} />
              {stage.label}
            </span>
          ))}
        </div>
      </div>

      {open ? (
        <ol
          data-testid="thinking-details"
          style={{
            margin: 'var(--ds-space-3) 0 var(--ds-space-1)',
            paddingLeft: 'var(--ds-space-5)',
            display: 'flex',
            flexDirection: 'column',
            gap: 'var(--ds-space-1)',
          }}
        >
          {stages.map((stage) => (
            <li key={stage.key} style={{ fontSize: 'var(--ds-text-sm)', color: 'var(--ds-text-secondary)' }}>
              {stage.label}
              <span style={{ marginLeft: 8, color: 'var(--ds-text-muted)' }}>
                {stage.status === 'running' ? '进行中' : stage.status === 'failed' ? '失败' : '完成'}
              </span>
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  )
}
