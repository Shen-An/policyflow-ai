import { createContext, useContext } from 'react'
import type { RunEvent } from '../../../electron/shared/types'
import type { RunPhase } from '../../desktop/use-run-events'

// Non-component module for the reimbursement workflow: shared types, the React context,
// and the consumer hook. Kept separate from the Provider component so the component file
// only exports components (react-refresh friendly).

export type ChangeSetItem = {
  path: string
  operation: string
  beforeHash: string | null
  afterHash: string | null
  diff: string
  preview: string
}

export type ChangeSet = {
  id: string
  state: string
  summary: string
  sideEffectClass: string
  items: ChangeSetItem[]
}

export type ApprovalFile = { path: string; sha256: string }

export type ApprovalTarget = {
  approvalId: string
  action: string
  destination: string
  actionDigest: string
  status: string
  expiresAt: string | null
  sideEffects: string[]
  files: ApprovalFile[]
}

export type Submission = { submissionId: string; receipt: string; destination: string }

export type DecisionError = 'permission' | 'conflict' | 'error' | null

export type WorkflowContextValue = {
  active: boolean
  runId: string | null
  phase: RunPhase
  reconnecting: boolean
  events: RunEvent[]
  changeSet: ChangeSet | null
  approval: ApprovalTarget | null
  submission: Submission | null
  rejected: boolean
  failure: { code: string; message: string } | null
  deciding: boolean
  decisionError: DecisionError
  startWorkflow: (scenario?: string) => Promise<string | null>
  decide: (decision: 'approve' | 'reject') => Promise<void>
  reset: () => void
}

export const WorkflowContext = createContext<WorkflowContextValue | null>(null)

export function useWorkflow(): WorkflowContextValue {
  const ctx = useContext(WorkflowContext)
  if (!ctx) throw new Error('useWorkflow must be used within a WorkflowProvider')
  return ctx
}
