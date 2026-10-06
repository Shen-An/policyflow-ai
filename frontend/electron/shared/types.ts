// Typed contract for the desktop capability surface. These types are shared by:
//   - the preload bridge (electron/preload) — forwards calls, type-only;
//   - the main handlers (electron/main) — validate + fulfil;
//   - the renderer client (src/services/desktop-api.ts) — typed consumer.
//
// This is a distinct, minimal boundary contract. It intentionally carries NO
// bearer token, refresh token, host path or raw infrastructure handle: the
// renderer receives identity and sanitized business state only.

export type DesktopRole = string

export type DesktopAuthUser = {
  id: string
  username: string
  displayName: string
  roles: DesktopRole[]
}

export type DesktopLoginRequest = {
  username: string
  password: string
}

/** Login result exposed to the renderer — identity + expiry, never the token. */
export type DesktopSession = {
  user: DesktopAuthUser
  /** Epoch milliseconds at which the main-held access token expires. */
  expiresAt: number
}

export type RunStartRequest = {
  kind: string
  input: Record<string, unknown>
  knowledgeBaseIds?: string[]
  idempotencyKey?: string
}

export type RunSummary = {
  runId: string
  status: string
  kind: string | null
  createdAt: string | null
}

/**
 * Typed projection of a backend RunEvent (append-only, ordered by `sequence`).
 * Payload is already sanitized server-side; main maps field names and forwards.
 */
export type RunEvent = {
  eventId: string
  runId: string
  sequence: number
  eventType: string
  stage: string | null
  status: string
  payload: Record<string, unknown>
  occurredAt: string | null
}

export type MaterialDeclareRequest = {
  name: string
  purpose: string
  contentHash: string
  byteSize?: number
  mimeType?: string
}

export type MaterialSummary = {
  materialId: string
  versionId: string | null
  status: string
}

export type WorkspaceSelectRequest = {
  runId: string
  materialVersionIds: string[]
}

export type WorkspaceSummary = {
  workspaceId: string
  status: string
  runId: string | null
}

export type WorkspaceQueryRequest = {
  workspaceId: string
}

export type ApprovalDecision = 'approve' | 'reject'

export type ApprovalDecisionRequest = {
  runId: string
  approvalId: string
  decision: ApprovalDecision
  /** Digest the reviewer is deciding on; a mismatch must be rejected server-side. */
  actionDigest: string
  reason?: string
}

export type ApprovalResult = {
  approvalId: string
  status: string
}

export type OpenExternalRequest = {
  url: string
}

export type OpenExternalResult = {
  /** True only when the URL passed the allowlist and was handed to the OS browser. */
  opened: boolean
}

export type DesktopInfo = {
  platform: string
  appVersion: string
  /** Always false — the renderer must never have Node/file/token capabilities. */
  hasNodeAccess: false
}

/** Sanitized error shape returned across the boundary — no stack, host path or secret. */
export type DesktopError = {
  code: string
  message: string
  retryable: boolean
}

/**
 * Every IPC operation returns a result envelope rather than throwing, so a
 * structured {@link DesktopError} (code + retryable) survives the IPC boundary as a
 * plain cloneable object. The renderer client unwraps it into a typed rejection.
 */
export type IpcSuccess<T> = { ok: true; value: T }
export type IpcFailure = { ok: false; error: DesktopError }
export type IpcResult<T> = IpcSuccess<T> | IpcFailure

/** Unsubscribe handle returned by a run-event subscription. */
export type Unsubscribe = () => void

/**
 * The complete, minimal set of capabilities exposed on `window.policyflow`.
 * Each method maps to exactly one operation-specific IPC channel in main.
 */
export type PolicyflowBridge = {
  auth: {
    login: (request: DesktopLoginRequest) => Promise<DesktopSession>
    logout: () => Promise<void>
    currentUser: () => Promise<DesktopAuthUser | null>
  }
  runs: {
    start: (request: RunStartRequest) => Promise<RunSummary>
    get: (runId: string) => Promise<RunSummary>
    cancel: (runId: string) => Promise<void>
    subscribeEvents: (
      runId: string,
      onEvent: (event: RunEvent) => void,
    ) => Promise<Unsubscribe>
  }
  materials: {
    declare: (request: MaterialDeclareRequest) => Promise<MaterialSummary>
  }
  workspace: {
    select: (request: WorkspaceSelectRequest) => Promise<WorkspaceSummary>
    query: (request: WorkspaceQueryRequest) => Promise<WorkspaceSummary>
  }
  approvals: {
    decide: (request: ApprovalDecisionRequest) => Promise<ApprovalResult>
  }
  system: {
    openExternal: (request: OpenExternalRequest) => Promise<OpenExternalResult>
    info: () => Promise<DesktopInfo>
  }
}

/**
 * The surface actually exposed on `window.policyflow` by the preload. Data
 * operations return an {@link IpcResult} envelope (so structured errors survive the
 * boundary); the renderer client in src/services/desktop-api.ts unwraps them into
 * the clean {@link PolicyflowBridge}. Event subscription resolves directly to an
 * unsubscribe handle and rejects on failure.
 */
export type RawPolicyflowBridge = {
  auth: {
    login: (request: DesktopLoginRequest) => Promise<IpcResult<DesktopSession>>
    logout: () => Promise<IpcResult<void>>
    currentUser: () => Promise<IpcResult<DesktopAuthUser | null>>
  }
  runs: {
    start: (request: RunStartRequest) => Promise<IpcResult<RunSummary>>
    get: (runId: string) => Promise<IpcResult<RunSummary>>
    cancel: (runId: string) => Promise<IpcResult<void>>
    subscribeEvents: (
      runId: string,
      onEvent: (event: RunEvent) => void,
    ) => Promise<Unsubscribe>
  }
  materials: {
    declare: (request: MaterialDeclareRequest) => Promise<IpcResult<MaterialSummary>>
  }
  workspace: {
    select: (request: WorkspaceSelectRequest) => Promise<IpcResult<WorkspaceSummary>>
    query: (request: WorkspaceQueryRequest) => Promise<IpcResult<WorkspaceSummary>>
  }
  approvals: {
    decide: (request: ApprovalDecisionRequest) => Promise<IpcResult<ApprovalResult>>
  }
  system: {
    openExternal: (request: OpenExternalRequest) => Promise<IpcResult<OpenExternalResult>>
    info: () => Promise<IpcResult<DesktopInfo>>
  }
}
