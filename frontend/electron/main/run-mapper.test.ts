import { describe, expect, it } from 'vitest'

import {
  toApprovalResult,
  toAuthUser,
  toMaterialSummary,
  toRunEvent,
  toRunSummary,
  toWorkspaceSummary,
} from './run-mapper'

describe('run-mapper', () => {
  it('maps a backend RunEvent (snake_case) into the typed, ordered projection', () => {
    const event = toRunEvent({
      event_id: 'evt-9',
      run_id: 'run-1',
      sequence: 9,
      event_type: 'stage.update',
      stage: 'retrieval',
      status: 'running',
      payload: { note: 'ok' },
      occurred_at: '2026-01-01T00:00:00Z',
    })
    expect(event).toEqual({
      eventId: 'evt-9',
      runId: 'run-1',
      sequence: 9,
      eventType: 'stage.update',
      stage: 'retrieval',
      status: 'running',
      payload: { note: 'ok' },
      occurredAt: '2026-01-01T00:00:00Z',
    })
  })

  it('is defensive against missing fields', () => {
    const event = toRunEvent({})
    expect(event.eventId).toBe('')
    expect(event.sequence).toBe(0)
    expect(event.eventType).toBe('unknown')
    expect(event.stage).toBe(null)
    expect(event.payload).toEqual({})
  })

  it('maps run / material / workspace / approval summaries', () => {
    expect(toRunSummary({ run_id: 'r1', status: 'running', kind: 'reimbursement', created_at: 't' })).toEqual({
      runId: 'r1',
      status: 'running',
      kind: 'reimbursement',
      createdAt: 't',
    })
    expect(toMaterialSummary({ material_id: 'm1', version_id: 'v1', status: 'declared' })).toEqual({
      materialId: 'm1',
      versionId: 'v1',
      status: 'declared',
    })
    expect(toWorkspaceSummary({ workspace_id: 'w1', status: 'ready', run_id: 'r1' })).toEqual({
      workspaceId: 'w1',
      status: 'ready',
      runId: 'r1',
    })
    expect(toApprovalResult({ approval_id: 'a1', status: 'approved' })).toEqual({
      approvalId: 'a1',
      status: 'approved',
    })
  })

  it('maps the auth user and keeps only string roles', () => {
    expect(
      toAuthUser({ id: 'u1', username: 'employee', display_name: 'Employee', roles: ['employee', 42, 'approver'] }),
    ).toEqual({ id: 'u1', username: 'employee', displayName: 'Employee', roles: ['employee', 'approver'] })
  })
})
