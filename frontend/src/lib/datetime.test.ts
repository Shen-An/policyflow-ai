import { describe, expect, it } from 'vitest'
import {
  formatHistoryTime,
  formatRelativeTime,
  parseApiDate,
} from './datetime'

describe('parseApiDate', () => {
  it('treats naive ISO timestamps as UTC', () => {
    const date = parseApiDate('2026-07-19T08:54:47.536054')
    expect(date).not.toBeNull()
    expect(date!.toISOString()).toBe('2026-07-19T08:54:47.536Z')
  })

  it('preserves explicit Z / offset timestamps', () => {
    expect(parseApiDate('2026-07-19T08:54:47.536054Z')!.toISOString()).toBe(
      '2026-07-19T08:54:47.536Z',
    )
    expect(parseApiDate('2026-07-19T16:54:47+08:00')!.toISOString()).toBe(
      '2026-07-19T08:54:47.000Z',
    )
  })

  it('returns null for empty / invalid values', () => {
    expect(parseApiDate(null)).toBeNull()
    expect(parseApiDate('')).toBeNull()
    expect(parseApiDate('not-a-date')).toBeNull()
  })
})

describe('format helpers', () => {
  it('formats same-day history time in local timezone', () => {
    const now = new Date()
    const utcHour = String(now.getUTCHours()).padStart(2, '0')
    const utcMinute = String(now.getUTCMinutes()).padStart(2, '0')
    const utcDate = `${now.getUTCFullYear()}-${String(now.getUTCMonth() + 1).padStart(2, '0')}-${String(now.getUTCDate()).padStart(2, '0')}`
    const value = `${utcDate}T${utcHour}:${utcMinute}:00`
    const formatted = formatHistoryTime(value)
    // Local display should match Date constructed as UTC
    const expected = new Intl.DateTimeFormat('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(`${value}Z`))
    expect(formatted).toBe(expected)
  })

  it('returns relative labels for recent times', () => {
    const recent = new Date(Date.now() - 2 * 60_000).toISOString().replace('Z', '')
    expect(formatRelativeTime(recent)).toBe('2 分钟前')
  })
})
