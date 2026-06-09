import { AppError, type AppErrorKind } from './errors'

export type ApiEnvelope<T> = {
  success: true
  data: T
  message?: string
  request_id?: string
}

type ErrorPayload = {
  error?: { code?: unknown; message?: unknown; details?: unknown; request_id?: unknown }
  detail?: unknown
  request_id?: unknown
}

function isEnvelope<T>(payload: T | ApiEnvelope<T>): payload is ApiEnvelope<T> {
  return typeof payload === 'object' && payload !== null && 'success' in payload &&
    payload.success === true && 'data' in payload
}

export function normalizeSuccess<T>(payload: T | ApiEnvelope<T>): T {
  return isEnvelope(payload) ? payload.data : payload
}

function kindForStatus(status: number): AppErrorKind {
  if (status === 401) return 'auth'
  if (status === 403) return 'permission'
  if (status === 404) return 'not-found'
  if (status === 409) return 'conflict'
  if (status === 400 || status === 422) return 'validation'
  if (status >= 500) return 'server'
  return 'unknown'
}

function defaultMessage(status: number): string {
  if (status === 401) return '登录状态已过期，请重新登录。'
  if (status === 403) return '你没有访问此功能的权限。若认为这是错误，请联系系统管理员。'
  if (status === 404) return '请求的资源不存在，请检查后重试。'
  if (status === 409) return '提交内容与现有数据冲突，请检查后重试。'
  if (status === 422) return '提交内容未通过校验，请检查标记字段。'
  if (status >= 500) return '服务暂时不可用，请稍后重试。'
  return '请求未能完成，请检查后重试。'
}

function formatValidationDetails(details: unknown): string | null {
  if (!Array.isArray(details) || details.length === 0) return null
  const parts: string[] = []
  for (const item of details) {
    if (!item || typeof item !== 'object') continue
    const row = item as { loc?: unknown; msg?: unknown; type?: unknown }
    const loc = Array.isArray(row.loc)
      ? row.loc.filter((part) => part !== 'body').join('.')
      : ''
    const msg = typeof row.msg === 'string' ? row.msg : 'validation error'
    parts.push(loc ? `${loc}: ${msg}` : msg)
  }
  return parts.length ? parts.join('；') : null
}

function humanizeValidationMessage(raw: string, details: unknown): string {
  const detailText = formatValidationDetails(details)
  const lower = raw.toLowerCase()

  if (
    lower.includes('at least one case or retrieval item') ||
    lower.includes('retrieval eval requires retrieval_item_ids')
  ) {
    return '请先勾选至少一个「检索用例」。只勾评估类型、不选用例会校验失败。'
  }
  if (lower.includes('rag_answer and ragas eval require case_ids')) {
    return '勾选了「回答」或「RAGAS」时，必须同时勾选至少一个「回答用例」。若只做 Hit@K/MRR，请只勾「检索」。'
  }
  if (lower.includes('compare_strategies requires retrieval')) {
    return '多策略对比需要勾选「检索」评估类型。'
  }
  if (raw === 'Request validation failed' || lower.includes('validation')) {
    return detailText
      ? `请求校验失败：${detailText}`
      : '请求校验失败。若你在跑检索评估，请确认已勾选检索用例。'
  }
  return detailText ? `${raw}（${detailText}）` : raw
}

export function normalizeError(status: number, payload: unknown, responseRequestId?: string): AppError {
  const candidate = typeof payload === 'object' && payload !== null ? payload as ErrorPayload : undefined
  const nested = candidate?.error
  const code = typeof nested?.code === 'string' ? nested.code : `HTTP_${status}`
  const details = nested?.details ?? candidate?.detail
  const rawMessage = typeof nested?.message === 'string' ? nested.message : defaultMessage(status)
  const message =
    status === 422 || code === 'VALIDATION_ERROR'
      ? humanizeValidationMessage(rawMessage, details)
      : rawMessage
  const payloadRequestId = typeof nested?.request_id === 'string'
    ? nested.request_id
    : typeof candidate?.request_id === 'string' ? candidate.request_id : undefined

  return new AppError({
    kind: kindForStatus(status),
    code,
    message,
    details,
    status,
    requestId: responseRequestId ?? payloadRequestId,
    retryable: status === 429 || status >= 500,
  })
}
