import { describe, expect, it } from 'vitest'
import { evalRunPollingInterval } from './queries'

describe('evalRunPollingInterval', () => {
  it('polls pending/running and stops for every terminal status', () => {
    expect(evalRunPollingInterval('pending')).toBe(2_000)
    expect(evalRunPollingInterval('running')).toBe(2_000)
    expect(evalRunPollingInterval('success')).toBe(false)
    expect(evalRunPollingInterval('failed')).toBe(false)
    expect(evalRunPollingInterval('skipped')).toBe(false)
  })
})
