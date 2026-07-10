import { env } from '../lib/env'
import { AppError } from './errors'
import { normalizeError, normalizeSuccess } from './response-normalizer'

type RequestOptions = RequestInit & { timeoutMs?: number }
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
  private readonly getAccessToken: () => string | null
  private readonly onUnauthorized?: () => void
  private readonly fetcher: typeof fetch

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? env.apiBaseUrl).replace(/\/$/u, '')
    this.timeoutMs = options.timeoutMs ?? env.requestTimeoutMs
    this.getAccessToken = options.getAccessToken ?? (() => null)
    this.onUnauthorized = options.onUnauthorized
    this.fetcher = options.fetcher ?? globalThis.fetch.bind(globalThis)
  }

  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const controller = new AbortController()
    const timeoutMs = options.timeoutMs ?? this.timeoutMs
    const timeout = globalThis.setTimeout(() => controller.abort('timeout'), timeoutMs)
    const externalSignal = options.signal
    const abortFromExternalSignal = () => controller.abort(externalSignal?.reason)
    externalSignal?.addEventListener('abort', abortFromExternalSignal, { once: true })

    const headers = new Headers(options.headers)
    const token = this.getAccessToken()
    if (token) headers.set('Authorization', `Bearer ${token}`)
    if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json')
    }
    if (!headers.has('Accept')) headers.set('Accept', 'application/json')

    try {
      const response = await this.fetcher(`${this.baseUrl}${path}`, { ...options, headers, signal: controller.signal })
      const payload = await readPayload(response)
      const requestId = response.headers.get('x-request-id') ?? undefined
      if (!response.ok) {
        if (response.status === 401) this.onUnauthorized?.()
        throw normalizeError(response.status, payload, requestId)
      }
      return normalizeSuccess(payload as T)
    } catch (error) {
      if (error instanceof AppError) throw error
      if (controller.signal.aborted && controller.signal.reason === 'timeout') {
        throw new AppError({ kind: 'timeout', code: 'REQUEST_TIMEOUT', message: '请求超时，请稍后重试。', retryable: true })
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
