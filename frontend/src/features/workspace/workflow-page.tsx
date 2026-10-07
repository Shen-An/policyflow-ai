import { useState } from 'react'
import { CheckCircle, File, FileCsv, FileMd, FileText } from '@phosphor-icons/react'
import { ErrorState, LoadingState } from '../../design-system/states'
import { ThinkingProcess } from '../chat/components/thinking-process'
import { ApprovalPanel } from '../approval/approval-page'
import { useWorkflow, type ChangeSetItem } from './workflow-store'

// T136 — the integrated reimbursement workflow: file tree + preview + version + diff on
// the left/right, the approval target + decision, and the final submission result. Every
// generated file stays a DRAFT until the request is approved; the draft badge makes that
// explicit so nothing looks "already filed" before a human signs off.

function iconFor(path: string) {
  if (path.endsWith('.md')) return <FileMd size={15} weight="duotone" aria-hidden />
  if (path.endsWith('.csv')) return <FileCsv size={15} weight="duotone" aria-hidden />
  if (path.endsWith('.txt')) return <FileText size={15} weight="duotone" aria-hidden />
  return <File size={15} weight="duotone" aria-hidden />
}

function FileTree({
  items,
  selected,
  onSelect,
}: {
  items: ChangeSetItem[]
  selected: string
  onSelect: (path: string) => void
}) {
  return (
    <nav data-testid="file-tree" aria-label="生成的文件" style={{ display: 'grid', gap: 2 }}>
      {items.map((item) => {
        const active = item.path === selected
        return (
          <button
            key={item.path}
            type="button"
            data-testid="file-node"
            aria-current={active ? 'true' : undefined}
            onClick={() => onSelect(item.path)}
            className="ds-focusable"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              border: 'none',
              background: active ? 'var(--ds-sidebar-active)' : 'transparent',
              color: active ? 'var(--ds-accent-text)' : 'var(--ds-text)',
              borderRadius: 'var(--ds-radius-sm)',
              padding: '7px 10px',
              cursor: 'pointer',
              textAlign: 'left',
              fontSize: 'var(--ds-text-sm)',
              width: '100%',
            }}
          >
            {iconFor(item.path)}
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {item.path}
            </span>
          </button>
        )
      })}
    </nav>
  )
}

export function WorkflowPage() {
  const workflow = useWorkflow()
  const { changeSet, approval, submission, rejected, failure, reconnecting, events, startWorkflow } = workflow
  const [selected, setSelected] = useState('')

  const items = changeSet?.items ?? []
  // Derive the effective selection in render (no effect): fall back to the first file
  // whenever the current selection is absent from the latest change set.
  const effectiveSelected = items.some((i) => i.path === selected) ? selected : items[0]?.path ?? ''
  const current = items.find((i) => i.path === effectiveSelected) ?? items[0] ?? null

  if (failure) {
    return (
      <div data-testid="workflow-page">
        <ErrorState
          title="流程未能完成"
          description={failure.message}
          onRetry={() => void startWorkflow()}
        />
      </div>
    )
  }

  if (!changeSet) {
    return (
      <div data-testid="workflow-page">
        <ThinkingProcess events={events} reconnecting={reconnecting} />
        <LoadingState title="正在整理报销材料…" description="稍候，正在生成草稿文件并准备审批信息。" />
      </div>
    )
  }

  return (
    <div data-testid="workflow-page" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--ds-space-4)' }}>
      <ThinkingProcess events={events} reconnecting={reconnecting} />

      <div style={{ display: 'grid', gridTemplateColumns: '260px 1fr', gap: 'var(--ds-space-4)', alignItems: 'start' }}>
        <div className="ds-card" style={{ padding: 'var(--ds-space-3)' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
            <span style={{ fontSize: 'var(--ds-text-sm)', fontWeight: 650 }}>生成的文件</span>
            {!submission && !rejected ? (
              <span
                data-testid="draft-badge"
                style={{
                  fontSize: 'var(--ds-text-xs)',
                  color: 'var(--ds-warning)',
                  background: 'var(--ds-warning-soft)',
                  border: '1px solid var(--ds-warning)',
                  borderRadius: 'var(--ds-radius-pill)',
                  padding: '1px 8px',
                  fontWeight: 600,
                }}
              >
                草稿 · 待批准
              </span>
            ) : null}
          </div>
          <FileTree items={items} selected={effectiveSelected} onSelect={setSelected} />
          <p style={{ marginTop: 10, marginBottom: 0, fontSize: 'var(--ds-text-xs)', color: 'var(--ds-text-muted)' }}>
            {changeSet.summary}
          </p>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--ds-space-3)' }}>
          {current ? (
            <>
              <div className="ds-card" style={{ padding: 'var(--ds-space-3)' }}>
                <div style={{ fontSize: 'var(--ds-text-sm)', fontWeight: 650, marginBottom: 6 }}>预览</div>
                <pre
                  data-testid="file-preview"
                  style={{
                    margin: 0,
                    whiteSpace: 'pre-wrap',
                    fontFamily: 'var(--ds-font-mono)',
                    fontSize: 'var(--ds-text-sm)',
                    color: 'var(--ds-text)',
                    background: 'var(--ds-card-muted)',
                    borderRadius: 'var(--ds-radius-sm)',
                    padding: 'var(--ds-space-3)',
                  }}
                >
                  {current.preview || '（空文件）'}
                </pre>
              </div>

              <div className="ds-card" style={{ padding: 'var(--ds-space-3)' }}>
                <div style={{ fontSize: 'var(--ds-text-sm)', fontWeight: 650, marginBottom: 6 }}>版本</div>
                <div data-testid="file-version" style={{ fontSize: 'var(--ds-text-sm)', color: 'var(--ds-text-secondary)' }}>
                  <span style={{ fontWeight: 600, color: 'var(--ds-text)' }}>
                    {current.operation === 'create' ? '新建' : current.operation === 'modify' ? '修改' : current.operation}
                  </span>
                  <span style={{ marginLeft: 10, fontFamily: 'var(--ds-font-mono)', fontSize: 'var(--ds-text-xs)' }}>
                    {current.beforeHash ? `${current.beforeHash.slice(0, 10)}…` : '（无原版本）'} →{' '}
                    {current.afterHash ? `${current.afterHash.slice(0, 10)}…` : '—'}
                  </span>
                </div>
              </div>

              <div className="ds-card" style={{ padding: 'var(--ds-space-3)' }}>
                <div style={{ fontSize: 'var(--ds-text-sm)', fontWeight: 650, marginBottom: 6 }}>改动对比</div>
                <pre
                  data-testid="file-diff"
                  style={{
                    margin: 0,
                    whiteSpace: 'pre-wrap',
                    fontFamily: 'var(--ds-font-mono)',
                    fontSize: 'var(--ds-text-sm)',
                    background: 'var(--ds-card-muted)',
                    borderRadius: 'var(--ds-radius-sm)',
                    padding: 'var(--ds-space-3)',
                  }}
                >
                  {current.diff || '（无差异）'}
                </pre>
              </div>
            </>
          ) : null}
        </div>
      </div>

      {submission ? (
        <section
          data-testid="submission-result"
          role="status"
          className="ds-card"
          style={{ padding: 'var(--ds-space-4)', display: 'flex', alignItems: 'center', gap: 10 }}
        >
          <CheckCircle size={22} weight="fill" aria-hidden style={{ color: 'var(--ds-success)' }} />
          <div>
            <div style={{ fontWeight: 650 }}>已提交到 {submission.destination}</div>
            <div style={{ fontSize: 'var(--ds-text-sm)', color: 'var(--ds-text-secondary)' }}>
              回执编号：{submission.receipt}
            </div>
          </div>
        </section>
      ) : rejected ? (
        <section
          data-testid="workflow-rejected"
          role="status"
          className="ds-card"
          style={{ padding: 'var(--ds-space-4)' }}
        >
          <div style={{ fontWeight: 650 }}>已驳回</div>
          <div style={{ fontSize: 'var(--ds-text-sm)', color: 'var(--ds-text-secondary)' }}>
            草稿文件未提交，保持为草稿。你可以修改材料后重新发起。
          </div>
        </section>
      ) : approval ? (
        <ApprovalPanel approval={approval} />
      ) : null}
    </div>
  )
}
