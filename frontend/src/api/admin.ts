import { apiClient } from './client'

/** Tenant governance surface (`/api/v2/admin`). */

export type AdminRole = {
  roleId: string
  code: string
  name: string
  description: string | null
  actions: string[]
  activeGrants: number
  version: number
}

export type AdminGrant = {
  grantId: string
  userId: string
  roleId: string
  scope: string
  validFrom: string | null
  expiresAt: string | null
  revokedAt: string | null
  revocationReason: string | null
  version: number
}

export type Paged<T> = { items: T[]; total: number; limit: number; offset: number }

export type QuotaPolicy = {
  policyId: string
  scope: string
  tenantId: string | null
  workload: string
  requestsPerWindow: number | null
  windowSeconds: number | null
  tokensPerWindow: number | null
  maxConcurrency: number | null
  queueAdmissionLimit: number | null
  version: number
}

export type QuotaUsage = {
  workload: string
  requests: number
  reservedTokens: number
  actualTokens: number
}

export type QuotaSummary = {
  policies: QuotaPolicy[]
  usage: QuotaUsage[]
  liveLeases: number
}

export type HealthSummary = {
  jobs: Record<string, number>
  oldestQueuedAt: string | null
  documents: Record<string, number>
  submissions: Record<string, number>
  workspaces: Record<string, number>
  retrieval: {
    retrievableManifests: number
    manifestsWithCountDrift: number
    activeMaterials: number
  }
  reconciliation: {
    openIssues: Array<{ issueKind: string; severity: string; count: number }>
  }
}

export type AdminRun = {
  runId: string
  kind: string
  status: string
  evidenceGate: string | null
  userId: string | null
  conversationId: string | null
  toolCallCount: number
  createdAt: string | null
  startedAt: string | null
  finishedAt: string | null
  version: number
}

export type TimelineEntry = {
  source: 'audit' | 'run_event'
  eventId: string
  occurredAt: string | null
  action?: string
  outcome?: string
  reasonCode?: string | null
  actorRef?: string | null
  eventType?: string
  stage?: string | null
  publicStatus?: string | null
  metadata: Record<string, unknown>
  redactions: string[]
}

export type RunTimeline = { run: AdminRun; items: TimelineEntry[]; total: number }

export type RecoveryResult = {
  runId: string
  jobId: string
  previousState: string
  state: string
  attempts: number
  maxAttempts: number
  version: number
}

export type ReconciliationIssue = {
  issueId: string
  storePair: string
  issueKind: string
  resourceKind: string | null
  resourceId: string | null
  versionId: string | null
  observedFingerprint: string | null
  expectedFingerprint: string | null
  severity: string
  state: string
  attempts: number
  maxAttempts: number
  nextAttemptAt: string | null
  lastErrorCode: string | null
  version: number
}

export type CapacityRun = {
  capacityRunId: string
  commitSha: string
  scenario: string
  suiteVersion: string
  llmMode: string
  startedAt: string | null
  durationSeconds: number | null
  rawArtifactUri: string | null
  rawArtifactSha256: string | null
  summaryMetrics: Record<string, unknown>
  verdict: string | null
  knownLimits: string | null
}

// -- raw shapes --------------------------------------------------------------

type RawRole = {
  role_id: string
  code: string
  name: string
  description: string | null
  actions: string[]
  active_grants: number
  version: number
}

type RawGrant = {
  grant_id: string
  user_id: string
  role_id: string
  scope: string
  valid_from: string | null
  expires_at: string | null
  revoked_at: string | null
  revocation_reason: string | null
  version: number
}

type RawPage<T> = { items: T[]; total: number; limit: number; offset: number }

type RawQuota = {
  policy_id: string
  scope: string
  tenant_id: string | null
  workload: string
  requests_per_window: number | null
  window_seconds: number | null
  tokens_per_window: number | null
  max_concurrency: number | null
  queue_admission_limit: number | null
  version: number
}

type RawUsage = {
  workload: string
  requests: number
  reserved_tokens: number
  actual_tokens: number
}

type RawHealth = {
  jobs: Record<string, number>
  oldest_queued_at: string | null
  documents: Record<string, number>
  submissions: Record<string, number>
  workspaces: Record<string, number>
  retrieval: {
    retrievable_manifests: number
    manifests_with_count_drift: number
    active_materials: number
  }
  reconciliation: {
    open_issues: Array<{ issue_kind: string; severity: string; count: number }>
  }
}

type RawRun = {
  run_id: string
  kind: string
  status: string
  evidence_gate: string | null
  user_id: string | null
  conversation_id: string | null
  tool_call_count: number
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  version: number
}

type RawTimeline = {
  source: 'audit' | 'run_event'
  event_id: string
  occurred_at: string | null
  action?: string
  outcome?: string
  reason_code?: string | null
  actor_ref?: string | null
  event_type?: string
  stage?: string | null
  public_status?: string | null
  metadata: Record<string, unknown>
  redactions: string[]
}

type RawIssue = {
  issue_id: string
  store_pair: string
  issue_kind: string
  resource_kind: string | null
  resource_id: string | null
  version_id: string | null
  observed_fingerprint: string | null
  expected_fingerprint: string | null
  severity: string
  state: string
  attempts: number
  max_attempts: number
  next_attempt_at: string | null
  last_error_code: string | null
  version: number
}

type RawCapacity = {
  capacity_run_id: string
  commit_sha: string
  scenario: string
  suite_version: string
  llm_mode: string
  started_at: string | null
  duration_seconds: number | null
  raw_artifact_uri: string | null
  raw_artifact_sha256: string | null
  summary_metrics: Record<string, unknown>
  verdict: string | null
  known_limits: string | null
}

// -- mappers -----------------------------------------------------------------

const toRole = (raw: RawRole): AdminRole => ({
  roleId: raw.role_id,
  code: raw.code,
  name: raw.name,
  description: raw.description,
  actions: raw.actions,
  activeGrants: raw.active_grants,
  version: raw.version,
})

const toGrant = (raw: RawGrant): AdminGrant => ({
  grantId: raw.grant_id,
  userId: raw.user_id,
  roleId: raw.role_id,
  scope: raw.scope,
  validFrom: raw.valid_from,
  expiresAt: raw.expires_at,
  revokedAt: raw.revoked_at,
  revocationReason: raw.revocation_reason,
  version: raw.version,
})

function toPage<T, R>(raw: RawPage<R>, map: (item: R) => T): Paged<T> {
  return {
    items: raw.items.map(map),
    total: raw.total,
    limit: raw.limit,
    offset: raw.offset,
  }
}

const toQuota = (raw: RawQuota): QuotaPolicy => ({
  policyId: raw.policy_id,
  scope: raw.scope,
  tenantId: raw.tenant_id,
  workload: raw.workload,
  requestsPerWindow: raw.requests_per_window,
  windowSeconds: raw.window_seconds,
  tokensPerWindow: raw.tokens_per_window,
  maxConcurrency: raw.max_concurrency,
  queueAdmissionLimit: raw.queue_admission_limit,
  version: raw.version,
})

const toUsage = (raw: RawUsage): QuotaUsage => ({
  workload: raw.workload,
  requests: raw.requests,
  reservedTokens: raw.reserved_tokens,
  actualTokens: raw.actual_tokens,
})

const toHealth = (raw: RawHealth): HealthSummary => ({
  jobs: raw.jobs,
  oldestQueuedAt: raw.oldest_queued_at,
  documents: raw.documents,
  submissions: raw.submissions,
  workspaces: raw.workspaces,
  retrieval: {
    retrievableManifests: raw.retrieval.retrievable_manifests,
    manifestsWithCountDrift: raw.retrieval.manifests_with_count_drift,
    activeMaterials: raw.retrieval.active_materials,
  },
  reconciliation: {
    openIssues: raw.reconciliation.open_issues.map((issue) => ({
      issueKind: issue.issue_kind,
      severity: issue.severity,
      count: issue.count,
    })),
  },
})

const toRun = (raw: RawRun): AdminRun => ({
  runId: raw.run_id,
  kind: raw.kind,
  status: raw.status,
  evidenceGate: raw.evidence_gate,
  userId: raw.user_id,
  conversationId: raw.conversation_id,
  toolCallCount: raw.tool_call_count,
  createdAt: raw.created_at,
  startedAt: raw.started_at,
  finishedAt: raw.finished_at,
  version: raw.version,
})

const toTimelineEntry = (raw: RawTimeline): TimelineEntry => ({
  source: raw.source,
  eventId: raw.event_id,
  occurredAt: raw.occurred_at,
  action: raw.action,
  outcome: raw.outcome,
  reasonCode: raw.reason_code ?? null,
  actorRef: raw.actor_ref ?? null,
  eventType: raw.event_type,
  stage: raw.stage ?? null,
  publicStatus: raw.public_status ?? null,
  metadata: raw.metadata,
  redactions: raw.redactions,
})

const toIssue = (raw: RawIssue): ReconciliationIssue => ({
  issueId: raw.issue_id,
  storePair: raw.store_pair,
  issueKind: raw.issue_kind,
  resourceKind: raw.resource_kind,
  resourceId: raw.resource_id,
  versionId: raw.version_id,
  observedFingerprint: raw.observed_fingerprint,
  expectedFingerprint: raw.expected_fingerprint,
  severity: raw.severity,
  state: raw.state,
  attempts: raw.attempts,
  maxAttempts: raw.max_attempts,
  nextAttemptAt: raw.next_attempt_at,
  lastErrorCode: raw.last_error_code,
  version: raw.version,
})

const toCapacity = (raw: RawCapacity): CapacityRun => ({
  capacityRunId: raw.capacity_run_id,
  commitSha: raw.commit_sha,
  scenario: raw.scenario,
  suiteVersion: raw.suite_version,
  llmMode: raw.llm_mode,
  startedAt: raw.started_at,
  durationSeconds: raw.duration_seconds,
  rawArtifactUri: raw.raw_artifact_uri,
  rawArtifactSha256: raw.raw_artifact_sha256,
  summaryMetrics: raw.summary_metrics,
  verdict: raw.verdict,
  knownLimits: raw.known_limits,
})

// -- endpoints ---------------------------------------------------------------

const BASE = '/api/v2/admin'

function pageQuery(limit: number, offset: number, extra: Record<string, string | undefined> = {}) {
  const search = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  for (const [key, value] of Object.entries(extra)) {
    if (value) search.set(key, value)
  }
  return search.toString()
}

export async function listRoles(signal?: AbortSignal): Promise<AdminRole[]> {
  const raw = await apiClient.request<{ items: RawRole[] }>(`${BASE}/roles`, { signal })
  return raw.items.map(toRole)
}

export async function listGrants(
  params: { limit: number; offset: number; memberId?: string },
  signal?: AbortSignal,
): Promise<Paged<AdminGrant>> {
  const query = pageQuery(params.limit, params.offset, { member_id: params.memberId })
  const raw = await apiClient.request<RawPage<RawGrant>>(`${BASE}/grants?${query}`, { signal })
  return toPage(raw, toGrant)
}

export async function createGrant(
  body: {
    userId: string
    roleId?: string
    roleCode?: string
    validFrom?: string
    expiresAt?: string
  },
  signal?: AbortSignal,
): Promise<{ grant: AdminGrant; created: boolean }> {
  const raw = await apiClient.request<{ grant: RawGrant; created: boolean }>(`${BASE}/grants`, {
    method: 'POST',
    body: JSON.stringify({
      user_id: body.userId,
      role_id: body.roleId ?? null,
      role_code: body.roleCode ?? null,
      valid_from: body.validFrom ?? null,
      expires_at: body.expiresAt ?? null,
    }),
    signal,
  })
  return { grant: toGrant(raw.grant), created: raw.created }
}

export async function revokeGrant(
  grantId: string,
  reason?: string,
  signal?: AbortSignal,
): Promise<{ grant: AdminGrant; revoked: boolean }> {
  const raw = await apiClient.request<{ grant: RawGrant; revoked: boolean }>(
    `${BASE}/grants/${encodeURIComponent(grantId)}/revoke`,
    {
      method: 'POST',
      body: JSON.stringify({ reason: reason ?? null }),
      signal,
    },
  )
  return { grant: toGrant(raw.grant), revoked: raw.revoked }
}

export async function getQuota(signal?: AbortSignal): Promise<QuotaSummary> {
  const raw = await apiClient.request<{
    policies: RawQuota[]
    usage: RawUsage[]
    live_leases: number
  }>(`${BASE}/quota`, { signal })
  return {
    policies: raw.policies.map(toQuota),
    usage: raw.usage.map(toUsage),
    liveLeases: raw.live_leases,
  }
}

export async function getHealth(signal?: AbortSignal): Promise<HealthSummary> {
  return toHealth(await apiClient.request<RawHealth>(`${BASE}/health`, { signal }))
}

export async function listRuns(
  params: { limit: number; offset: number; status?: string; kind?: string },
  signal?: AbortSignal,
): Promise<Paged<AdminRun>> {
  const query = pageQuery(params.limit, params.offset, {
    status: params.status,
    kind: params.kind,
  })
  const raw = await apiClient.request<RawPage<RawRun>>(`${BASE}/runs?${query}`, { signal })
  return toPage(raw, toRun)
}

export async function getRunTimeline(runId: string, signal?: AbortSignal): Promise<RunTimeline> {
  const raw = await apiClient.request<{
    run: RawRun
    items: RawTimeline[]
    total: number
  }>(`${BASE}/runs/${encodeURIComponent(runId)}/audit`, { signal })
  return {
    run: toRun(raw.run),
    items: raw.items.map(toTimelineEntry),
    total: raw.total,
  }
}

export async function recoverRun(
  runId: string,
  extendAttempts = false,
  signal?: AbortSignal,
): Promise<RecoveryResult> {
  const raw = await apiClient.request<{
    run_id: string
    job_id: string
    previous_state: string
    state: string
    attempts: number
    max_attempts: number
    version: number
  }>(`${BASE}/runs/${encodeURIComponent(runId)}/recover`, {
    method: 'POST',
    body: JSON.stringify({ extend_attempts: extendAttempts }),
    signal,
  })
  return {
    runId: raw.run_id,
    jobId: raw.job_id,
    previousState: raw.previous_state,
    state: raw.state,
    attempts: raw.attempts,
    maxAttempts: raw.max_attempts,
    version: raw.version,
  }
}

export async function listReconciliation(
  params: { limit: number; offset: number; state?: string; issueKind?: string },
  signal?: AbortSignal,
): Promise<Paged<ReconciliationIssue>> {
  const query = pageQuery(params.limit, params.offset, {
    state: params.state,
    issue_kind: params.issueKind,
  })
  const raw = await apiClient.request<RawPage<RawIssue>>(
    `${BASE}/reconciliation?${query}`,
    { signal },
  )
  return toPage(raw, toIssue)
}

export async function retryReconciliation(
  issueId: string,
  reason?: string,
  signal?: AbortSignal,
): Promise<{ issueId: string; state: string; previousState: string }> {
  const raw = await apiClient.request<{
    issue_id: string
    state: string
    previous_state: string
  }>(`${BASE}/reconciliation/${encodeURIComponent(issueId)}/retry`, {
    method: 'POST',
    body: JSON.stringify({ reason: reason ?? null }),
    signal,
  })
  return { issueId: raw.issue_id, state: raw.state, previousState: raw.previous_state }
}

export async function resolveReconciliation(
  issueId: string,
  resolution: string,
  signal?: AbortSignal,
): Promise<{ issueId: string; state: string; previousState: string; resolution: string }> {
  const raw = await apiClient.request<{
    issue_id: string
    state: string
    previous_state: string
    resolution: string
  }>(`${BASE}/reconciliation/${encodeURIComponent(issueId)}/resolve`, {
    method: 'POST',
    body: JSON.stringify({ resolution }),
    signal,
  })
  return {
    issueId: raw.issue_id,
    state: raw.state,
    previousState: raw.previous_state,
    resolution: raw.resolution,
  }
}

export async function listCapacity(
  params: { limit: number; offset: number },
  signal?: AbortSignal,
): Promise<Paged<CapacityRun>> {
  const query = pageQuery(params.limit, params.offset)
  const raw = await apiClient.request<RawPage<RawCapacity>>(`${BASE}/capacity?${query}`, {
    signal,
  })
  return toPage(raw, toCapacity)
}
