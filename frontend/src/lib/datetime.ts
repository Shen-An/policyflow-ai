/** Parse API timestamps that are stored as UTC.

 * Backend historically returned naive ISO strings (`2026-07-19T08:54:47.536054`)
 * for UTC values. Browsers treat those as local time, shifting display by the
 * local offset (e.g. UTC+8 → shows 08:54 instead of 16:54). Strings already
 * carrying `Z` / offset are left alone.
 */
export function parseApiDate(value: string | null | undefined): Date | null {
  if (!value) return null
  const trimmed = value.trim()
  if (!trimmed) return null

  const hasTimezone =
    /(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(trimmed) ||
    // space-separated offset forms are uncommon but keep safe
    /[+-]\d{2}:\d{2}$/.test(trimmed)

  const normalized = hasTimezone
    ? trimmed
    : /T/.test(trimmed)
      ? `${trimmed}Z`
      : `${trimmed.replace(' ', 'T')}Z`

  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatDateTime(
  value: string | null | undefined,
  options?: Intl.DateTimeFormatOptions,
  fallback = '',
): string {
  const date = parseApiDate(value)
  if (!date) return fallback
  return new Intl.DateTimeFormat(
    'zh-CN',
    options ?? {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    },
  ).format(date)
}

export function formatDateOnly(value: string | null | undefined, fallback = ''): string {
  return formatDateTime(
    value,
    { year: 'numeric', month: '2-digit', day: '2-digit' },
    fallback,
  )
}

export function formatTimeOnly(value: string | null | undefined, fallback = ''): string {
  return formatDateTime(
    value,
    { hour: '2-digit', minute: '2-digit' },
    fallback,
  )
}

export function formatRelativeTime(value: string | null | undefined, fallback = ''): string {
  const date = parseApiDate(value)
  if (!date) return fallback
  const diffMs = Date.now() - date.getTime()
  const minutes = Math.floor(diffMs / 60_000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days} 天前`
  return formatDateTime(value, { month: '2-digit', day: '2-digit' }, fallback)
}

export function formatHistoryTime(value: string | null | undefined, fallback = ''): string {
  const date = parseApiDate(value)
  if (!date) return fallback
  const now = new Date()
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()
  if (sameDay) {
    return formatTimeOnly(value, fallback)
  }
  return formatDateTime(value, { month: '2-digit', day: '2-digit' }, fallback)
}
