import type { DesktopError } from '../shared/types'

/**
 * Error raised inside the main-side proxy with an already-chosen public code. The
 * message is still scrubbed before it crosses the IPC boundary (defense in depth).
 */
export class ProxyError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly retryable: boolean,
    readonly status?: number,
  ) {
    super(message)
    this.name = 'ProxyError'
  }
}

// Patterns for things that must never reach the renderer or a log line.
const JWT_LIKE = /\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/gu
const BEARER = /bearer\s+[A-Za-z0-9._~+/=-]+/giu
const WINDOWS_PATH = /[A-Za-z]:\\[^\s"'<>|]+/gu
const FILE_URL = /file:\/\/\/[^\s"'<>]+/giu
const LONG_HEX = /\b[A-Fa-f0-9]{40,}\b/gu

export type RedactionSecrets = {
  /** The backend origin (e.g. http://127.0.0.1:8000) to mask from any message. */
  backendOrigin?: string
  /** Additional literal secrets (e.g. the current access token) to mask. */
  literals?: string[]
}

/** Scrub tokens, bearer headers, host filesystem paths and the backend origin. */
export function redactString(value: string, secrets: RedactionSecrets = {}): string {
  let out = value
  for (const literal of secrets.literals ?? []) {
    if (literal) out = out.split(literal).join('[redacted]')
  }
  if (secrets.backendOrigin) out = out.split(secrets.backendOrigin).join('[backend]')
  out = out
    .replace(BEARER, 'bearer [redacted]')
    .replace(FILE_URL, '[path]')
    .replace(WINDOWS_PATH, '[path]')
    .replace(JWT_LIKE, '[redacted]')
    .replace(LONG_HEX, '[redacted]')
  return out
}

/**
 * Convert any thrown value into a sanitized {@link DesktopError}. A known
 * {@link ProxyError} keeps its code/retryable; anything else collapses to a generic
 * internal error so stack traces, host paths and secrets can never leak downstream.
 */
export function toDesktopError(error: unknown, secrets: RedactionSecrets = {}): DesktopError {
  if (error instanceof ProxyError) {
    return {
      code: error.code,
      message: redactString(error.message, secrets),
      retryable: error.retryable,
    }
  }
  return {
    code: 'INTERNAL_ERROR',
    message: '桌面代理处理请求时发生内部错误。',
    retryable: false,
  }
}
