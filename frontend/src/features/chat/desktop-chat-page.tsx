import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowClockwise,
  Copy,
  FileText,
  PaperPlaneRight,
  PencilSimple,
  Sparkle,
} from '@phosphor-icons/react'
import { MarkdownContent } from '../../components/markdown/markdown-content'
import { EmptyState, OfflineState } from '../../design-system/states'
import { useRunEvents } from '../../desktop/use-run-events'
import type { RunEvent } from '../../../electron/shared/types'
import { ThinkingProcess } from './components/thinking-process'

type Citation = { title: string; snippet: string; source: string }

type ChatAnswer = { answer: string; gate: string | null; citations: Citation[]; failed: boolean }

// answer === null means the turn is still in-flight; its answer is derived live from the
// current run's events during render (no setState-in-effect). When the next question is
// asked, the previous in-flight turn is snapshotted synchronously in the ask handler.
type ChatTurn = { id: string; question: string; answer: ChatAnswer | null }

const EXAMPLES = ['市内交通费可以报销吗？', '差旅住宿标准是多少？', '加班餐补如何申请？']

function toCitations(value: unknown): Citation[] {
  if (!Array.isArray(value)) return []
  return value.map((raw) => {
    const r = (raw ?? {}) as Record<string, unknown>
    return {
      title: typeof r.title === 'string' ? r.title : '制度文件',
      snippet: typeof r.snippet === 'string' ? r.snippet : '',
      source: typeof r.source === 'string' ? r.source : '',
    }
  })
}

/** The terminal answer for a run, or null if it has not completed yet. */
function answerFromEvents(events: RunEvent[]): ChatAnswer | null {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const event = events[i]
    if (event.eventType === 'run.completed' || event.eventType === 'run.failed') {
      const p = event.payload ?? {}
      const failed = event.eventType === 'run.failed'
      return {
        answer:
          typeof p.answer === 'string' ? p.answer : failed ? '很抱歉，这次没能完成。请稍后重试。' : '',
        gate: typeof p.evidence_gate === 'string' ? (p.evidence_gate as string) : null,
        citations: toCitations(p.citations),
        failed,
      }
    }
  }
  return null
}

/** Live evidence for the in-flight turn: the latest event carrying evidence/citations. */
function liveEvidence(events: RunEvent[]): Citation[] {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const p = events[i].payload
    if (Array.isArray(p?.evidence)) return toCitations(p.evidence)
    if (Array.isArray(p?.citations)) return toCitations(p.citations)
  }
  return []
}

function EvidencePanel({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) return null
  return (
    <div
      data-testid="chat-evidence"
      style={{
        border: '1px solid var(--ds-border)',
        borderRadius: 'var(--ds-radius-md)',
        background: 'var(--ds-card)',
        padding: 'var(--ds-space-3)',
        margin: '0 0 var(--ds-space-3)',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 'var(--ds-text-sm)',
          fontWeight: 600,
          color: 'var(--ds-text-secondary)',
          marginBottom: 'var(--ds-space-2)',
        }}
      >
        <FileText size={15} weight="duotone" aria-hidden /> 依据来源（{citations.length}）
      </div>
      <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 'var(--ds-space-2)' }}>
        {citations.map((c, i) => (
          <li key={`${c.title}-${i}`} style={{ fontSize: 'var(--ds-text-sm)', color: 'var(--ds-text-secondary)' }}>
            <span style={{ fontWeight: 600, color: 'var(--ds-text)' }}>{c.title}</span>
            {c.source ? <span style={{ color: 'var(--ds-text-muted)' }}>· {c.source}</span> : null}
            {c.snippet ? <div style={{ color: 'var(--ds-text-muted)' }}>{c.snippet}</div> : null}
          </li>
        ))}
      </ul>
    </div>
  )
}

async function copyText(text: string): Promise<void> {
  try {
    await navigator.clipboard?.writeText(text)
  } catch {
    // Clipboard may be unavailable under the app:// scheme; copy is best-effort.
  }
}

function AnswerBlock({ answer }: { answer: ChatAnswer }) {
  if (answer.gate === 'insufficient_evidence') {
    return (
      <div
        data-testid="chat-refusal"
        role="status"
        style={{
          border: '1px solid var(--ds-warning)',
          background: 'var(--ds-warning-soft)',
          color: 'var(--ds-text)',
          borderRadius: 'var(--ds-radius-md)',
          padding: 'var(--ds-space-3)',
        }}
      >
        {answer.answer || '没有找到足够的制度依据，无法给出结论。'}
      </div>
    )
  }
  return (
    <div data-testid="assistant-message" className="ds-card" style={{ padding: 'var(--ds-space-4)' }}>
      <MarkdownContent content={answer.answer} />
      <div style={{ marginTop: 'var(--ds-space-2)' }}>
        <button
          type="button"
          data-testid="answer-copy"
          aria-label="复制答案"
          onClick={() => void copyText(answer.answer)}
          className="ds-focusable ds-ghost-btn"
        >
          <Copy size={14} aria-hidden /> 复制答案
        </button>
      </div>
    </div>
  )
}

export function DesktopChatPage() {
  const run = useRunEvents()
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [draft, setDraft] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  // Scrolling the latest turn into view is a DOM side effect (not state), so it is a
  // legitimate effect — open/refresh and every new event land at the bottom.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [turns, run.events])

  const busy = run.phase === 'starting' || run.phase === 'running' || run.phase === 'reconnecting'

  const ask = useCallback(
    (question: string) => {
      const text = question.trim()
      if (!text || busy) return
      setTurns((prev) => {
        const next = [...prev]
        // Snapshot the previous in-flight turn before its run is reset by the next start.
        if (next.length > 0 && next[next.length - 1].answer === null) {
          next[next.length - 1] = {
            ...next[next.length - 1],
            answer: answerFromEvents(run.events) ?? {
              answer: '（未返回结果）',
              gate: null,
              citations: [],
              failed: true,
            },
          }
        }
        next.push({ id: `${Date.now()}`, question: text, answer: null })
        return next
      })
      setDraft('')
      void run.start({ kind: 'chat', input: { question: text } })
    },
    [busy, run],
  )

  const seedComposer = useCallback((text: string) => {
    setDraft(text)
    inputRef.current?.focus()
  }, [])

  const activeEvidence = useMemo(() => liveEvidence(run.events), [run.events])
  const liveAnswer = answerFromEvents(run.events)
  const lastIndex = turns.length - 1

  return (
    <div data-testid="chat-page" style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
        {turns.length === 0 ? (
          <EmptyState
            title="开始一个制度问答"
            description="用大白话问就行。例如下面这些常见问题，点一下即可填入。"
            icon={<Sparkle size={24} weight="duotone" />}
          >
            <div data-testid="chat-empty" style={{ display: 'grid', gap: 'var(--ds-space-2)', marginTop: 'var(--ds-space-3)' }}>
              {EXAMPLES.map((example) => (
                <button
                  key={example}
                  type="button"
                  data-testid="chat-example"
                  onClick={() => seedComposer(example)}
                  className="ds-focusable"
                  style={{
                    border: '1px solid var(--ds-border)',
                    background: 'var(--ds-card)',
                    color: 'var(--ds-text)',
                    borderRadius: 'var(--ds-radius-md)',
                    padding: '10px 14px',
                    cursor: 'pointer',
                    fontSize: 'var(--ds-text-base)',
                    textAlign: 'left',
                  }}
                >
                  {example}
                </button>
              ))}
            </div>
          </EmptyState>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--ds-space-5)', maxWidth: 760, margin: '0 auto' }}>
            {turns.map((turn, index) => {
              const isLast = index === lastIndex
              const answer = turn.answer ?? (isLast ? liveAnswer : null)
              const evidence = answer ? answer.citations : isLast ? activeEvidence : []
              return (
                <div key={turn.id} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--ds-space-2)' }}>
                  <div style={{ alignSelf: 'flex-end', maxWidth: '82%' }}>
                    <div
                      data-testid="user-message"
                      style={{
                        background: 'var(--ds-accent-soft)',
                        border: '1px solid var(--ds-accent-border)',
                        color: 'var(--ds-text)',
                        borderRadius: 'var(--ds-radius-lg)',
                        padding: '8px 12px',
                        whiteSpace: 'pre-wrap',
                      }}
                    >
                      {turn.question}
                    </div>
                    <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end', marginTop: 2 }}>
                      <button
                        type="button"
                        data-testid="user-copy"
                        aria-label="复制我的消息"
                        onClick={() => void copyText(turn.question)}
                        className="ds-focusable ds-ghost-btn"
                      >
                        <Copy size={14} aria-hidden /> 复制
                      </button>
                      <button
                        type="button"
                        data-testid="user-edit"
                        aria-label="编辑并重新发送"
                        onClick={() => seedComposer(turn.question)}
                        className="ds-focusable ds-ghost-btn"
                      >
                        <PencilSimple size={14} aria-hidden /> 编辑
                      </button>
                    </div>
                  </div>

                  {isLast ? <ThinkingProcess events={run.events} reconnecting={run.reconnecting} /> : null}
                  {isLast && run.reconnecting ? (
                    <div
                      data-testid="chat-reconnecting"
                      role="status"
                      aria-live="polite"
                      style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 'var(--ds-text-sm)', color: 'var(--ds-warning)' }}
                    >
                      <ArrowClockwise size={14} className="ds-spin" aria-hidden /> 连接中断，正在重新连接并恢复结果…
                    </div>
                  ) : null}

                  <EvidencePanel citations={evidence} />
                  {answer ? <AnswerBlock answer={answer} /> : null}
                </div>
              )
            })}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {!run.reconnecting && run.phase === 'error' ? (
        <OfflineState title="连接出错" description={run.error ?? '事件流中断，请重试。'} variant="inline" />
      ) : null}

      <div
        style={{
          borderTop: '1px solid var(--ds-border)',
          background: 'var(--ds-card)',
          padding: 'var(--ds-space-3)',
          display: 'flex',
          gap: 'var(--ds-space-2)',
          alignItems: 'flex-end',
        }}
      >
        <label htmlFor="chat-composer" style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>
          输入你的问题
        </label>
        <textarea
          id="chat-composer"
          data-testid="chat-input"
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              ask(draft)
            }
          }}
          placeholder="输入你的问题，回车发送（Shift+Enter 换行）"
          rows={2}
          className="ds-focusable"
          style={{
            flex: 1,
            resize: 'none',
            border: '1px solid var(--ds-border)',
            borderRadius: 'var(--ds-radius-md)',
            padding: '10px 12px',
            fontSize: 'var(--ds-text-base)',
            fontFamily: 'inherit',
            color: 'var(--ds-text)',
            background: 'var(--ds-card)',
          }}
        />
        <button
          type="button"
          data-testid="chat-send"
          onClick={() => ask(draft)}
          disabled={busy || draft.trim().length === 0}
          className="ds-focusable"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            border: 'none',
            background: busy || draft.trim().length === 0 ? 'var(--ds-border-strong)' : 'var(--ds-accent)',
            color: '#fff',
            borderRadius: 'var(--ds-radius-md)',
            padding: '10px 16px',
            cursor: busy || draft.trim().length === 0 ? 'not-allowed' : 'pointer',
            fontSize: 'var(--ds-text-base)',
            fontWeight: 600,
          }}
        >
          <PaperPlaneRight size={16} aria-hidden /> 发送
        </button>
      </div>
    </div>
  )
}
