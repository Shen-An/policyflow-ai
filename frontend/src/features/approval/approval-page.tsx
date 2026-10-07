import { CalendarX, FileText, Scales, Target } from '@phosphor-icons/react'
import { ConflictState, EmptyState, ErrorState, PermissionState } from '../../design-system/states'
import { useWorkflow, type ApprovalTarget } from '../workspace/workflow-store'

// T135 — the approval surface. Shows exactly what is being approved: the target
// (action + destination), the precise files and content hashes, declared side effects,
// and the expiry — then approve / reject with explicit stale / permission / conflict
// feedback. Used both inside the integrated workflow page and on the /approval route.

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: 'var(--ds-space-3)', padding: '8px 0', borderBottom: '1px solid var(--ds-divider)' }}>
      <div style={{ width: 92, flexShrink: 0, color: 'var(--ds-text-secondary)', fontSize: 'var(--ds-text-sm)' }}>
        {label}
      </div>
      <div style={{ flex: 1, minWidth: 0, color: 'var(--ds-text)', fontSize: 'var(--ds-text-sm)' }}>{children}</div>
    </div>
  )
}

export function ApprovalPanel({ approval }: { approval: ApprovalTarget }) {
  const { decide, deciding, decisionError, submission, rejected, reset } = useWorkflow()

  // Decision feedback takes over the panel: each has a clear reason + next step.
  if (decisionError === 'permission') {
    return (
      <PermissionState
        description="你没有审批这笔报销到该目标系统的权限。请联系有审批权限的同事或管理员。"
        action={{ label: '返回工作区', onClick: reset }}
      />
    )
  }
  if (decisionError === 'conflict') {
    return (
      <ConflictState
        description="该审批请求已失效或已被他人处理（材料或摘要已变化）。请基于最新材料重新发起。"
        action={{ label: '重新开始', onClick: reset }}
      />
    )
  }
  if (decisionError === 'error') {
    return <ErrorState description="提交审批决定时出错，请重试。" onRetry={() => void decide('approve')} />
  }

  return (
    <section
      data-testid="approval-panel"
      aria-label="审批请求"
      className="ds-card"
      style={{ padding: 'var(--ds-space-4)' }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 'var(--ds-space-3)' }}>
        <Scales size={18} weight="duotone" aria-hidden style={{ color: 'var(--ds-accent)' }} />
        <h3 style={{ margin: 0, fontSize: 'var(--ds-text-md)', fontWeight: 650 }}>审批这笔操作</h3>
      </div>

      <Row label="目标">
        <span data-testid="approval-target" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <Target size={14} aria-hidden /> {approval.destination}
        </span>
      </Row>
      <Row label="涉及文件">
        <ul data-testid="approval-files" style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 4 }}>
          {approval.files.map((file) => (
            <li key={file.path} style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
              <FileText size={13} aria-hidden style={{ color: 'var(--ds-text-muted)' }} />
              <span style={{ fontWeight: 600 }}>{file.path}</span>
              <code
                title={file.sha256}
                style={{
                  fontFamily: 'var(--ds-font-mono)',
                  fontSize: 'var(--ds-text-xs)',
                  color: 'var(--ds-text-muted)',
                }}
              >
                {file.sha256.slice(0, 12)}…
              </code>
            </li>
          ))}
        </ul>
      </Row>
      <Row label="影响">
        <ul data-testid="approval-side-effects" style={{ margin: 0, paddingLeft: 18 }}>
          {approval.sideEffects.map((effect) => (
            <li key={effect}>{effect}</li>
          ))}
        </ul>
      </Row>
      <Row label="有效期">
        <span data-testid="approval-expiry" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <CalendarX size={14} aria-hidden />
          {approval.expiresAt ? new Date(approval.expiresAt).toLocaleString('zh-CN') : '长期有效'}
        </span>
      </Row>
      <Row label="摘要">
        <code style={{ fontFamily: 'var(--ds-font-mono)', fontSize: 'var(--ds-text-xs)', color: 'var(--ds-text-muted)', wordBreak: 'break-all' }}>
          {approval.actionDigest}
        </code>
      </Row>

      <div style={{ display: 'flex', gap: 'var(--ds-space-2)', marginTop: 'var(--ds-space-4)' }}>
        <button
          type="button"
          data-testid="approval-approve"
          disabled={deciding || Boolean(submission) || rejected}
          onClick={() => void decide('approve')}
          className="ds-focusable"
          style={{
            border: 'none',
            background: 'var(--ds-accent)',
            color: '#fff',
            borderRadius: 'var(--ds-radius-md)',
            padding: '9px 18px',
            fontWeight: 600,
            cursor: deciding ? 'not-allowed' : 'pointer',
          }}
        >
          批准并提交
        </button>
        <button
          type="button"
          data-testid="approval-reject"
          disabled={deciding || Boolean(submission) || rejected}
          onClick={() => void decide('reject')}
          className="ds-focusable"
          style={{
            border: '1px solid var(--ds-border-strong)',
            background: 'var(--ds-card)',
            color: 'var(--ds-text)',
            borderRadius: 'var(--ds-radius-md)',
            padding: '9px 18px',
            fontWeight: 600,
            cursor: deciding ? 'not-allowed' : 'pointer',
          }}
        >
          驳回
        </button>
      </div>
    </section>
  )
}

/** Route surface for the "待我审批" nav item. */
export function ApprovalPage() {
  const { approval, active } = useWorkflow()
  return (
    <div data-testid="approval-page">
      {active && approval ? (
        <ApprovalPanel approval={approval} />
      ) : (
        <EmptyState
          title="暂无待你审批的事项"
          description="报销等需要审批的操作会出现在这里。你可以从“报销工作区”发起一个流程。"
        />
      )}
    </div>
  )
}
