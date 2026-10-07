import { useCallback, useMemo, useState, type PropsWithChildren } from 'react'
import { DesktopApiError, desktopApi } from '../../services/desktop-api'
import { useRunEvents } from '../../desktop/use-run-events'
import type { RunEvent } from '../../../electron/shared/types'
import {
  WorkflowContext,
  type ApprovalTarget,
  type ChangeSet,
  type DecisionError,
  type Submission,
  type WorkflowContextValue,
} from './workflow-store'

// The reimbursement workflow Provider. One active run is driven here and consumed by the
// workspace entry, the integrated workflow page and the approval page — the change set,
// approval target, and submission result all arrive as ordered run-event payloads (the
// capability bridge exposes no separate change-set/approval read), and the decision goes
// back through approvals.decide.

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}
function str(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}
function strOrNull(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function findLastPayload(events: RunEvent[], eventType: string): Record<string, unknown> | null {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    if (events[i].eventType === eventType) return events[i].payload ?? {}
  }
  return null
}

function deriveChangeSet(events: RunEvent[]): ChangeSet | null {
  const payload = findLastPayload(events, 'change_set.ready')
  const cs = payload ? record(payload.changeSet) : null
  if (!cs || Object.keys(cs).length === 0) return null
  const items = Array.isArray(cs.items) ? cs.items : []
  return {
    id: str(cs.id),
    state: str(cs.state, 'draft'),
    summary: str(cs.summary),
    sideEffectClass: str(cs.sideEffectClass),
    items: items.map((raw) => {
      const r = record(raw)
      return {
        path: str(r.path),
        operation: str(r.operation, 'modify'),
        beforeHash: strOrNull(r.beforeHash),
        afterHash: strOrNull(r.afterHash),
        diff: str(r.diff),
        preview: str(r.preview),
      }
    }),
  }
}

function deriveApproval(events: RunEvent[]): ApprovalTarget | null {
  const payload = findLastPayload(events, 'approval.requested')
  const a = payload ? record(payload.approval) : null
  if (!a || Object.keys(a).length === 0) return null
  return {
    approvalId: str(a.approvalId),
    action: str(a.action),
    destination: str(a.destination),
    actionDigest: str(a.actionDigest),
    status: str(a.status, 'pending'),
    expiresAt: strOrNull(a.expiresAt),
    sideEffects: Array.isArray(a.sideEffects) ? a.sideEffects.map((s) => str(s)) : [],
    files: Array.isArray(a.files)
      ? a.files.map((raw) => {
          const r = record(raw)
          return { path: str(r.path), sha256: str(r.sha256) }
        })
      : [],
  }
}

function deriveSubmission(events: RunEvent[]): Submission | null {
  const payload = findLastPayload(events, 'run.completed')
  const s = payload ? record(payload.submission) : null
  if (!s || Object.keys(s).length === 0) return null
  return {
    submissionId: str(s.submissionId),
    receipt: str(s.receipt),
    destination: str(s.destination),
  }
}

function deriveRejected(events: RunEvent[]): boolean {
  const completed = findLastPayload(events, 'run.completed')
  if (completed && completed.rejected === true) return true
  const decided = findLastPayload(events, 'approval.decided')
  return Boolean(decided && str(decided.decision) === 'rejected')
}

function deriveFailure(events: RunEvent[]): { code: string; message: string } | null {
  const payload = findLastPayload(events, 'run.failed')
  if (!payload) return null
  return { code: str(payload.code, 'WORKFLOW_FAILED'), message: str(payload.message, '任务失败。') }
}

/** The frozen proxy collapses backend errors to UPSTREAM_ERROR with the HTTP status in
 *  the message text; recover it so we can distinguish permission (403) from conflict (409). */
function statusFromError(error: DesktopApiError): number | null {
  const match = /（(\d{3})）|\((\d{3})\)/u.exec(error.message)
  return match ? Number(match[1] ?? match[2]) : null
}

export function WorkflowProvider({ children }: PropsWithChildren) {
  const run = useRunEvents()
  const [deciding, setDeciding] = useState(false)
  const [decisionError, setDecisionError] = useState<DecisionError>(null)

  const changeSet = useMemo(() => deriveChangeSet(run.events), [run.events])
  const approval = useMemo(() => deriveApproval(run.events), [run.events])
  const submission = useMemo(() => deriveSubmission(run.events), [run.events])
  const rejected = useMemo(() => deriveRejected(run.events), [run.events])
  const failure = useMemo(() => deriveFailure(run.events), [run.events])

  const startWorkflow = useCallback(
    async (scenario?: string): Promise<string | null> => {
      setDecisionError(null)
      setDeciding(false)
      return run.start({ kind: 'file_workflow', input: scenario ? { scenario } : {} })
    },
    [run],
  )

  const decide = useCallback(
    async (decision: 'approve' | 'reject') => {
      if (!approval || !run.runId) return
      setDeciding(true)
      setDecisionError(null)
      try {
        await desktopApi.approvals.decide({
          runId: run.runId,
          approvalId: approval.approvalId,
          decision,
          actionDigest: approval.actionDigest,
        })
        // On success the terminal events (approval.decided + run.completed) arrive on the
        // open stream and drive the result UI.
      } catch (error) {
        if (error instanceof DesktopApiError) {
          const status = statusFromError(error)
          setDecisionError(status === 403 ? 'permission' : status === 409 ? 'conflict' : 'error')
        } else {
          setDecisionError('error')
        }
      } finally {
        setDeciding(false)
      }
    },
    [approval, run.runId],
  )

  const value: WorkflowContextValue = {
    active: run.phase !== 'idle',
    runId: run.runId,
    phase: run.phase,
    reconnecting: run.reconnecting,
    events: run.events,
    changeSet,
    approval,
    submission,
    rejected,
    failure,
    deciding,
    decisionError,
    startWorkflow,
    decide,
    reset: run.reset,
  }

  return <WorkflowContext.Provider value={value}>{children}</WorkflowContext.Provider>
}
