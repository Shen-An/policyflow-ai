import type {
  ApprovalResult,
  DesktopAuthUser,
  MaterialSummary,
  RunEvent,
  RunSummary,
  WorkspaceSummary,
} from '../shared/types'

// Defensive mappers from backend v2 JSON (snake_case, loosely typed) into the typed
// desktop contract. Each reads only known fields and never forwards raw host state.

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}

function str(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function strOr(value: unknown, fallback: string): string {
  return typeof value === 'string' ? value : fallback
}

function num(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

export function toRunSummary(raw: unknown): RunSummary {
  const record = asRecord(raw)
  return {
    runId: strOr(record.run_id ?? record.id, ''),
    status: strOr(record.status, 'unknown'),
    kind: str(record.kind ?? record.run_kind),
    createdAt: str(record.created_at),
  }
}

/** Typed projection of an append-only backend RunEvent, ordered by `sequence`. */
export function toRunEvent(raw: unknown): RunEvent {
  const record = asRecord(raw)
  return {
    eventId: strOr(record.event_id ?? record.id, ''),
    runId: strOr(record.run_id, ''),
    sequence: num(record.sequence),
    eventType: strOr(record.event_type ?? record.type, 'unknown'),
    stage: str(record.stage),
    status: strOr(record.status, 'unknown'),
    payload: asRecord(record.payload ?? record.data),
    occurredAt: str(record.occurred_at ?? record.created_at),
  }
}

export function toMaterialSummary(raw: unknown): MaterialSummary {
  const record = asRecord(raw)
  return {
    materialId: strOr(record.material_id ?? record.id, ''),
    versionId: str(record.version_id ?? record.material_version_id),
    status: strOr(record.status, 'declared'),
  }
}

export function toWorkspaceSummary(raw: unknown): WorkspaceSummary {
  const record = asRecord(raw)
  return {
    workspaceId: strOr(record.workspace_id ?? record.id, ''),
    status: strOr(record.status, 'unknown'),
    runId: str(record.run_id),
  }
}

export function toApprovalResult(raw: unknown): ApprovalResult {
  const record = asRecord(raw)
  return {
    approvalId: strOr(record.approval_id ?? record.id, ''),
    status: strOr(record.status, 'unknown'),
  }
}

function toRoleCodes(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((role): role is string => typeof role === 'string') : []
}

export function toAuthUser(raw: unknown): DesktopAuthUser {
  const record = asRecord(raw)
  return {
    id: strOr(record.id, ''),
    username: strOr(record.username, ''),
    displayName: strOr(record.display_name ?? record.displayName, strOr(record.username, '')),
    roles: toRoleCodes(record.roles),
  }
}
