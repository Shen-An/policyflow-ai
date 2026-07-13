import { env } from '../lib/env'
import { AppError } from './errors'
import { normalizeError, normalizeSuccess } from './response-normalizer'

export type RequestOptions = RequestInit & {
  timeoutMs?: number
  skipUnauthorizedHandler?: boolean
}

type ApiClientOptions = {
  baseUrl?: string
  timeoutMs?: number
  getAccessToken?: () => string | null
  onUnauthorized?: () => void
  fetcher?: typeof fetch
}

async function readPayload(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined
  const text = await response.text()
  if (!text) return undefined
  const contentType = response.headers.get('content-type') ?? ''
  if (contentType.includes('application/json')) {
    try { return JSON.parse(text) as unknown } catch { return text }
  }
  return text
}

export class ApiClient {
  private readonly baseUrl: string
  private readonly timeoutMs: number
  private accessTokenProvider: () => string | null
  private unauthorizedHandler?: () => void
  private readonly fetcher?: typeof fetch

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? env.apiBaseUrl).replace(/\/$/u, '')
    this.timeoutMs = options.timeoutMs ?? env.requestTimeoutMs
    this.accessTokenProvider = options.getAccessToken ?? (() => null)
    this.unauthorizedHandler = options.onUnauthorized
    this.fetcher = options.fetcher
  }

  setAccessTokenProvider(provider: () => string | null): void {
    this.accessTokenProvider = provider
  }

  setUnauthorizedHandler(handler?: () => void): void {
    this.unauthorizedHandler = handler
  }

  private resolveUrl(path: string): string {
    if (this.baseUrl) return `${this.baseUrl}${path}`
    if (typeof window !== 'undefined') return new URL(path, window.location.origin).toString()
    return path
  }

  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const controller = new AbortController()
    const timeoutMs = options.timeoutMs ?? this.timeoutMs
    const timeout = globalThis.setTimeout(() => controller.abort('timeout'), timeoutMs)
    const externalSignal = options.signal
    const abortFromExternalSignal = () => controller.abort(externalSignal?.reason)
    externalSignal?.addEventListener('abort', abortFromExternalSignal, { once: true })

    const headers = new Headers(options.headers)
    const token = this.accessTokenProvider()
    if (token) headers.set('Authorization', `Bearer ${token}`)
    if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json')
    }
    if (!headers.has('Accept')) headers.set('Accept', 'application/json')

    const { skipUnauthorizedHandler: _skip, timeoutMs: _timeout, ...requestInit } = options
    void _skip
    void _timeout

    try {
      const fetcher = this.fetcher ?? globalThis.fetch
      const response = await fetcher(this.resolveUrl(path), {
        ...requestInit,
        headers,
        signal: controller.signal,
      })
      const payload = await readPayload(response)
      const requestId = response.headers.get('x-request-id') ?? undefined
      if (!response.ok) {
        if (response.status === 401 && !options.skipUnauthorizedHandler) {
          this.unauthorizedHandler?.()
        }
        throw normalizeError(response.status, payload, requestId)
      }
      return normalizeSuccess(payload as T)
    } catch (error) {
      if (error instanceof AppError) throw error
      if (controller.signal.aborted && controller.signal.reason === 'timeout') {
        throw new AppError({
          kind: 'timeout',
          code: 'REQUEST_TIMEOUT',
          message: `请求超时（${timeoutMs / 1000} 秒内未完成）。制度问答可能因检索/模型生成较慢，请稍后重试；若频繁超时，请检查模型服务与网络。`,
          retryable: true,
        })
      }
      if (controller.signal.aborted) {
        throw new AppError({ kind: 'network', code: 'REQUEST_ABORTED', message: '请求已取消。', retryable: false })
      }
      throw new AppError({ kind: 'network', code: 'NETWORK_ERROR', message: '网络连接失败，请检查网络后重试。', details: error, retryable: true })
    } finally {
      globalThis.clearTimeout(timeout)
      externalSignal?.removeEventListener('abort', abortFromExternalSignal)
    }
  }
}

export const apiClient = new ApiClient()
