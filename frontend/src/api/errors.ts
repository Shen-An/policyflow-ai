export type AppErrorKind =
  | 'auth' | 'permission' | 'validation' | 'not-found' | 'conflict'
  | 'network' | 'timeout' | 'server' | 'unknown'

export type AppErrorOptions = {
  kind: AppErrorKind
  code: string
  message: string
  details?: unknown
  status?: number
  requestId?: string
  retryable: boolean
}

export class AppError extends Error {
  readonly kind: AppErrorKind
  readonly code: string
  readonly details?: unknown
  readonly status?: number
  readonly requestId?: string
  readonly retryable: boolean

  constructor(options: AppErrorOptions) {
    super(options.message)
    this.name = 'AppError'
    this.kind = options.kind
    this.code = options.code
    this.details = options.details
    this.status = options.status
    this.requestId = options.requestId
    this.retryable = options.retryable
  }
}
