import { isSameLocalRenderer } from './hardening'

export type SenderOriginOptions = {
  /** URL the controlled renderer document resolves to (file:// in production). */
  rendererUrl: string
  /** Dev server origin, allowed only in development (e.g. http://127.0.0.1:5173). */
  devServerUrl?: string | null
}

/**
 * Validate that an IPC call originates from the controlled local renderer.
 *
 * Every main handler calls this with `event.senderFrame.url` before doing work, so
 * a frame loaded from any other origin (a smuggled remote page, an opened popup,
 * about:blank with an injected document) cannot invoke privileged operations.
 */
export function isTrustedSenderUrl(
  url: string | null | undefined,
  options: SenderOriginOptions,
): boolean {
  if (!url) return false
  if (isSameLocalRenderer(url, options.rendererUrl)) return true
  if (options.devServerUrl) {
    try {
      return new URL(url).origin === new URL(options.devServerUrl).origin
    } catch {
      return false
    }
  }
  return false
}
