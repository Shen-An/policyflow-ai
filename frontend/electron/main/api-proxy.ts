import type { CredentialVault } from './credentials'
import { ProxyError, toDesktopError, type RedactionSecrets } from './redaction'
import {
  toApprovalResult,
  toAuthUser,
  toMaterialSummary,
  toRunEvent,
  toRunSummary,
  toWorkspaceSummary,
} from './run-mapper'
import type {
  ApprovalDecisionRequest,
  ApprovalResult,
  DesktopAuthUser,
  DesktopError,
  DesktopSession,
  MaterialDeclareRequest,
  MaterialSummary,
  RunEvent,
  RunStartRequest,
  RunSummary,
  WorkspaceQueryRequest,
  WorkspaceSelectRequest,
  WorkspaceSummary,
} from '../shared/types'

export type ApiProxyOptions = {
  /** Backend origin, e.g. http://127.0.0.1:8000. No path, no credentials. */
  backendBaseUrl: string
  vault: CredentialVault
  /** Injectable fetch for tests; defaults to the global fetch. */
  fetchImpl?: typeof fetch
}

type RequestOptions = {
  body?: unknown
  signal?: AbortSignal
  auth?: boolean
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}

function str(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function num(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function isAbort(error: unknown, signal?: AbortSignal): boolean {
  return Boolean(signal?.aborted) || (error instanceof Error && error.name === 'AbortError')
}

/**
 * The only component that performs authenticated network I/O. The renderer never
 * holds a token or calls the backend directly; it invokes typed IPC operations that
 * route through this proxy, which injects the main-held access token, supports
 * cancellation, maps responses into the typed contract, and redacts every error.
 */
export class ApiProxy {
  private readonly baseUrl: string
  private readonly backendOrigin: string
  private readonly vault: CredentialVault
  private readonly fetchImpl: typeof fetch

  constructor(options: ApiProxyOptions) {
    this.baseUrl = options.backendBaseUrl.replace(/\/$/u, '')
    this.backendOrigin = (() => {
      try {
        return new URL(this.baseUrl).origin
      } catch {
        return this.baseUrl
      }
    })()
    this.vault = options.vault
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch
  }

  /** Secrets masked from any error message that crosses the boundary. */
  private redactionSecrets(): RedactionSecrets {
    const token = this.vault.getAccessToken()
    return { backendOrigin: this.backendOrigin, literals: token ? [token] : [] }
  }

  /** Final sanitization applied by the IPC layer before returning an error. */
  redactError(error: unknown): DesktopError {
    return toDesktopError(error, this.redactionSecrets())
  }

  private async requestJson(method: string, path: string, options: RequestOptions = {}): Promise<unknown> {
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (options.body !== undefined) headers['Content-Type'] = 'application/json'
    if (options.auth !== false) {
      const token = this.vault.getAccessToken()
      if (token) headers.Authorization = `Bearer ${token}`
    }

    let response: Response
    try {
      response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        method,
        headers,
        body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
        signal: options.signal,
      })
    } catch (error) {
      if (isAbort(error, options.signal)) {
        throw new ProxyError('REQUEST_CANCELLED', '请求已取消。', false)
      }
      throw new ProxyError('NETWORK_ERROR', '无法连接后端服务，请稍后重试。', true)
    }

    const text = await response.text().catch(() => '')
    let data: unknown
    if (text) {
      try {
        data = JSON.parse(text)
      } catch {
        data = text
      }
    }

    if (!response.ok) {
      if (response.status === 401) {
        this.vault.clearAccessToken()
        throw new ProxyError('UNAUTHORIZED', '登录状态已失效，请重新登录。', false, 401)
      }
      throw new ProxyError('UPSTREAM_ERROR', `后端返回错误（${response.status}）。`, response.status >= 500, response.status)
    }
    return data
  }

  // --- auth ---------------------------------------------------------------

  async login(request: { username: string; password: string }): Promise<DesktopSession> {
    const data = await this.requestJson('POST', '/api/auth/login', { body: request, auth: false })
    const record = asRecord(data)
    const accessToken = str(record.access_token)
    if (!accessToken) throw new ProxyError('LOGIN_FAILED', '登录响应缺少凭证。', false)
    const expiresAt = Date.now() + (num(record.expires_in) || 3600) * 1000
    this.vault.setAccessToken(accessToken, expiresAt)
    // Persist the session secret encrypted at rest. Best-effort: if the OS keychain
    // is unavailable we stay memory-only rather than fail the login.
    try {
      this.vault.saveRefreshToken(str(record.refresh_token) ?? accessToken)
    } catch {
      // secure storage unavailable — session remains in-memory for this run only
    }
    return { user: toAuthUser(record.user), expiresAt }
  }

  async logout(): Promise<void> {
    this.vault.clear()
  }

  async currentUser(): Promise<DesktopAuthUser | null> {
    if (!this.vault.getAccessToken()) return null
    try {
      return toAuthUser(await this.requestJson('GET', '/api/auth/me'))
    } catch (error) {
      if (error instanceof ProxyError && error.code === 'UNAUTHORIZED') return null
      throw error
    }
  }

  // --- runs ---------------------------------------------------------------

  async startRun(request: RunStartRequest, signal?: AbortSignal): Promise<RunSummary> {
    const body = {
      kind: request.kind,
      input: request.input,
      knowledge_base_ids: request.knowledgeBaseIds,
      idempotency_key: request.idempotencyKey,
    }
    return toRunSummary(await this.requestJson('POST', '/api/v2/runs', { body, signal }))
  }

  async getRun(runId: string, signal?: AbortSignal): Promise<RunSummary> {
    return toRunSummary(
      await this.requestJson('GET', `/api/v2/runs/${encodeURIComponent(runId)}`, { signal }),
    )
  }

  async cancelRun(runId: string): Promise<void> {
    await this.requestJson('POST', `/api/v2/runs/${encodeURIComponent(runId)}/cancel`, { body: {} })
  }

  /**
   * Stream typed run events over SSE. The generator ends when the server closes the
   * stream or `signal` aborts; the caller (IPC) forwards each event to the renderer
   * on a per-subscription channel and can cancel by aborting the signal.
   */
  async *streamRunEvents(runId: string, signal: AbortSignal): AsyncGenerator<RunEvent> {
    const headers: Record<string, string> = { Accept: 'text/event-stream' }
    const token = this.vault.getAccessToken()
    if (token) headers.Authorization = `Bearer ${token}`

    let response: Response
    try {
      response = await this.fetchImpl(
        `${this.baseUrl}/api/v2/runs/${encodeURIComponent(runId)}/events`,
        { method: 'GET', headers, signal },
      )
    } catch (error) {
      if (isAbort(error, signal)) return
      throw new ProxyError('NETWORK_ERROR', '无法连接事件流，请稍后重试。', true)
    }
    if (!response.ok || !response.body) {
      throw new ProxyError('UPSTREAM_ERROR', `事件流打开失败（${response.status}）。`, response.status >= 500, response.status)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const blocks = buffer.split('\n\n')
        buffer = blocks.pop() ?? ''
        for (const block of blocks) {
          const event = this.parseSseBlock(block)
          if (event) yield event
        }
      }
    } catch (error) {
      if (!isAbort(error, signal)) throw new ProxyError('NETWORK_ERROR', '事件流中断。', true)
    } finally {
      try {
        await reader.cancel()
      } catch {
        // reader already closed
      }
    }
  }

  private parseSseBlock(block: string): RunEvent | null {
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
    }
    if (dataLines.length === 0) return null
    try {
      return toRunEvent(JSON.parse(dataLines.join('\n')))
    } catch {
      return null
    }
  }

  // --- materials / workspace / approvals ----------------------------------

  async declareMaterial(request: MaterialDeclareRequest, signal?: AbortSignal): Promise<MaterialSummary> {
    const body = {
      name: request.name,
      purpose: request.purpose,
      content_hash: request.contentHash,
      byte_size: request.byteSize,
      mime_type: request.mimeType,
    }
    return toMaterialSummary(await this.requestJson('POST', '/api/v2/materials', { body, signal }))
  }

  async selectWorkspace(request: WorkspaceSelectRequest, signal?: AbortSignal): Promise<WorkspaceSummary> {
    const body = { run_id: request.runId, material_version_ids: request.materialVersionIds }
    return toWorkspaceSummary(await this.requestJson('POST', '/api/v2/workspaces', { body, signal }))
  }

  async queryWorkspace(request: WorkspaceQueryRequest, signal?: AbortSignal): Promise<WorkspaceSummary> {
    return toWorkspaceSummary(
      await this.requestJson('GET', `/api/v2/workspaces/${encodeURIComponent(request.workspaceId)}`, {
        signal,
      }),
    )
  }

  async decideApproval(request: ApprovalDecisionRequest, signal?: AbortSignal): Promise<ApprovalResult> {
    const body = {
      decision: request.decision,
      action_digest: request.actionDigest,
      reason: request.reason,
    }
    const path = `/api/v2/runs/${encodeURIComponent(request.runId)}/approvals/${encodeURIComponent(request.approvalId)}`
    return toApprovalResult(await this.requestJson('POST', path, { body, signal }))
  }
}
