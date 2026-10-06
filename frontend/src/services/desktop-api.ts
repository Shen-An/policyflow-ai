import type {
  ApprovalDecisionRequest,
  ApprovalResult,
  DesktopAuthUser,
  DesktopInfo,
  DesktopLoginRequest,
  DesktopSession,
  IpcResult,
  MaterialDeclareRequest,
  MaterialSummary,
  OpenExternalRequest,
  OpenExternalResult,
  PolicyflowBridge,
  RawPolicyflowBridge,
  RunEvent,
  RunStartRequest,
  RunSummary,
  Unsubscribe,
  WorkspaceQueryRequest,
  WorkspaceSelectRequest,
  WorkspaceSummary,
} from '../../electron/shared/types'

// Renderer-side typed client over the preload bridge (`window.policyflow`). Feature
// code imports these typed clients and must NEVER touch `window.policyflow` or raw IPC
// directly — this module is the single seam between the renderer and the capability
// broker in main. It unwraps the IPC result envelope into values or typed errors.

declare global {
  interface Window {
    policyflow?: RawPolicyflowBridge
  }
}

/** Error thrown when a desktop capability call fails; mirrors the sanitized main error. */
export class DesktopApiError extends Error {
  readonly code: string
  readonly retryable: boolean

  constructor(code: string, message: string, retryable: boolean) {
    super(message)
    this.name = 'DesktopApiError'
    this.code = code
    this.retryable = retryable
  }
}

function requireBridge(): RawPolicyflowBridge {
  const bridge = typeof window !== 'undefined' ? window.policyflow : undefined
  if (!bridge) {
    throw new DesktopApiError(
      'DESKTOP_BRIDGE_UNAVAILABLE',
      '桌面能力桥不可用：请在 PolicyFlow 桌面应用中运行。',
      false,
    )
  }
  return bridge
}

function unwrap<T>(result: IpcResult<T>): T {
  if (result.ok) return result.value
  throw new DesktopApiError(result.error.code, result.error.message, result.error.retryable)
}

/** True when running inside the Electron shell with the capability bridge present. */
export function isDesktopRuntime(): boolean {
  return typeof window !== 'undefined' && typeof window.policyflow === 'object' && window.policyflow !== null
}

export const desktopApi: PolicyflowBridge = {
  auth: {
    login: async (request: DesktopLoginRequest): Promise<DesktopSession> =>
      unwrap(await requireBridge().auth.login(request)),
    logout: async (): Promise<void> => unwrap(await requireBridge().auth.logout()),
    currentUser: async (): Promise<DesktopAuthUser | null> =>
      unwrap(await requireBridge().auth.currentUser()),
  },
  runs: {
    start: async (request: RunStartRequest): Promise<RunSummary> =>
      unwrap(await requireBridge().runs.start(request)),
    get: async (runId: string): Promise<RunSummary> => unwrap(await requireBridge().runs.get(runId)),
    cancel: async (runId: string): Promise<void> => unwrap(await requireBridge().runs.cancel(runId)),
    subscribeEvents: (runId: string, onEvent: (event: RunEvent) => void): Promise<Unsubscribe> =>
      requireBridge().runs.subscribeEvents(runId, onEvent),
  },
  materials: {
    declare: async (request: MaterialDeclareRequest): Promise<MaterialSummary> =>
      unwrap(await requireBridge().materials.declare(request)),
  },
  workspace: {
    select: async (request: WorkspaceSelectRequest): Promise<WorkspaceSummary> =>
      unwrap(await requireBridge().workspace.select(request)),
    query: async (request: WorkspaceQueryRequest): Promise<WorkspaceSummary> =>
      unwrap(await requireBridge().workspace.query(request)),
  },
  approvals: {
    decide: async (request: ApprovalDecisionRequest): Promise<ApprovalResult> =>
      unwrap(await requireBridge().approvals.decide(request)),
  },
  system: {
    openExternal: async (request: OpenExternalRequest): Promise<OpenExternalResult> =>
      unwrap(await requireBridge().system.openExternal(request)),
    info: async (): Promise<DesktopInfo> => unwrap(await requireBridge().system.info()),
  },
}
