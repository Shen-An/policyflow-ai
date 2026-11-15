import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkflowPage } from './workflow-page'
import { WorkflowContext, type WorkflowContextValue } from './workflow-store'

function show(overrides: Partial<WorkflowContextValue>) {
  const value: WorkflowContextValue = {
    active: true, runId: 'run', phase: 'running', reconnecting: false, events: [],
    changeSet: null, approval: null, submission: null, rejected: false, failure: null,
    deciding: false, decisionError: null, startWorkflow: vi.fn(), decide: vi.fn(), reset: vi.fn(),
    ...overrides,
  }
  render(<WorkflowContext.Provider value={value}><WorkflowPage /></WorkflowContext.Provider>)
}

describe('workflow terminal results', () => {
  it('shows the actual refusal when no draft was produced', () => {
    show({ phase: 'succeeded', events: [{ eventId: 'd1', runId: 'run', eventType: 'run.succeeded',
      sequence: 1, occurredAt: null, status: 'succeeded', stage: '', payload: { answer: '缺少报销制度依据。', evidence_gate: 'insufficient_evidence' } }] })
    expect(screen.getByText('缺少报销制度依据。')).toBeVisible()
    expect(screen.queryByText('正在整理报销材料…')).not.toBeInTheDocument()
  })

  it('shows cancellation and removes the pending approval action', () => {
    show({ phase: 'cancelled', changeSet: { id: 'cs', state: 'ready', summary: 'draft',
      sideEffectClass: 'mock_external_submission', items: [] },
      approval: { approvalId: 'a', version: 1, action: 'submit', destination: 'mock',
        actionDigest: 'a'.repeat(64), status: 'pending', expiresAt: null, sideEffects: [], files: [] } })
    expect(screen.getByText('流程已取消')).toBeVisible()
    expect(screen.queryByText('草稿 · 待批准')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '批准并提交' })).not.toBeInTheDocument()
  })

  it('labels a mock receipt in the result', () => {
    show({ phase: 'succeeded', changeSet: { id: 'cs', state: 'ready', summary: 'draft',
      sideEffectClass: 'mock_external_submission', items: [] },
      submission: { status: 'mock', submissionId: 's', receipt: 'mock-1', destination: 'mock-expense-system' } })
    expect(screen.getByTestId('submission-result')).toHaveTextContent('模拟提交完成')
  })
})
