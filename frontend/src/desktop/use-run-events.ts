import { useCallback, useEffect, useRef, useState } from 'react'
import { DesktopApiError, desktopApi } from '../services/desktop-api'
import type { RunEvent, RunStartRequest } from '../../electron/shared/types'

// Shared desktop run driver (used by chat and the file workflow). It starts a durable
// run through the capability bridge, subscribes to its ordered event stream, and — since
// the main process sends no explicit "stream closed" signal when the backend SSE ends —
// detects a mid-run disconnect by idle-timeout + a durable status check, then re-subscribes
// by bumping an attempt counter (the subscription lives in an effect). Events are
// de-duplicated by eventId so a replayed prefix on reconnect is harmless.

export type RunPhase =
  | 'idle'
  | 'starting'
  | 'running'
  | 'reconnecting'
  | 'succeeded'
  | 'failed'
  | 'error'

const TERMINAL_EVENT_TYPES = new Set(['run.completed', 'run.failed'])
const TERMINAL_RUN_STATUS = new Set(['succeeded', 'failed', 'terminal_failed', 'cancelled', 'timed_out'])
const IDLE_RECONNECT_MS = 1500
const MAX_RECONNECTS = 4

export type UseRunEvents = {
  runId: string | null
  events: RunEvent[]
  phase: RunPhase
  error: string | null
  reconnecting: boolean
  start: (request: RunStartRequest) => Promise<string | null>
  reset: () => void
}

export function useRunEvents(): UseRunEvents {
  const [runId, setRunId] = useState<string | null>(null)
  const [events, setEvents] = useState<RunEvent[]>([])
  const [phase, setPhase] = useState<RunPhase>('idle')
  const [error, setError] = useState<string | null>(null)
  // Bumping `attempt` for the same runId forces the subscription effect to re-run,
  // i.e. transparently re-open the event stream after a detected disconnect.
  const [attempt, setAttempt] = useState(0)

  const seenRef = useRef<Set<string>>(new Set())
  const terminalRef = useRef(false)
  const lastStatusRef = useRef<string | null>(null)
  const reconnectsRef = useRef(0)

  const resetTracking = () => {
    seenRef.current = new Set()
    terminalRef.current = false
    lastStatusRef.current = null
    reconnectsRef.current = 0
  }

  useEffect(() => {
    if (!runId) return
    let cancelled = false
    let unsubscribe: (() => void) | null = null
    let idleTimer: ReturnType<typeof setTimeout> | null = null

    const clearIdle = () => {
      if (idleTimer) {
        clearTimeout(idleTimer)
        idleTimer = null
      }
    }
    const scheduleIdle = () => {
      clearIdle()
      idleTimer = setTimeout(() => void onIdle(), IDLE_RECONNECT_MS)
    }
    const onIdle = async () => {
      if (cancelled || terminalRef.current) return
      // A run parked on an approval holds its stream open with no events; that silence
      // is expected, not a disconnect.
      if (lastStatusRef.current === 'waiting_approval') return
      if (reconnectsRef.current >= MAX_RECONNECTS) {
        setPhase('error')
        setError('连接多次中断，仍未收到结果。请稍后重试。')
        return
      }
      try {
        const run = await desktopApi.runs.get(runId)
        if (cancelled) return
        if (TERMINAL_RUN_STATUS.has(run.status)) {
          // Durable run already finished; re-subscribe once to replay the terminal event.
          reconnectsRef.current += 1
          setPhase('reconnecting')
          setAttempt((a) => a + 1)
          return
        }
        reconnectsRef.current += 1
        setPhase('reconnecting')
        setAttempt((a) => a + 1)
      } catch {
        if (cancelled) return
        reconnectsRef.current += 1
        setPhase('reconnecting')
        setAttempt((a) => a + 1)
      }
    }
    const onEvent = (event: RunEvent) => {
      if (cancelled) return
      if (event.eventId && seenRef.current.has(event.eventId)) {
        scheduleIdle()
        return
      }
      if (event.eventId) seenRef.current.add(event.eventId)
      lastStatusRef.current = event.status
      setEvents((prev) => [...prev, event])
      if (TERMINAL_EVENT_TYPES.has(event.eventType)) {
        terminalRef.current = true
        clearIdle()
        setPhase(event.eventType === 'run.failed' ? 'failed' : 'succeeded')
        return
      }
      setPhase('running')
      scheduleIdle()
    }

    void (async () => {
      try {
        const unsub = await desktopApi.runs.subscribeEvents(runId, onEvent)
        if (cancelled) {
          unsub()
          return
        }
        unsubscribe = unsub
        scheduleIdle()
      } catch (err) {
        if (cancelled) return
        setPhase('error')
        setError(err instanceof DesktopApiError ? err.message : '无法打开事件流。')
      }
    })()

    return () => {
      cancelled = true
      clearIdle()
      if (unsubscribe) unsubscribe()
    }
  }, [runId, attempt])

  const start = useCallback(async (request: RunStartRequest): Promise<string | null> => {
    resetTracking()
    setEvents([])
    setError(null)
    setPhase('starting')
    setAttempt(0)
    setRunId(null)
    try {
      const summary = await desktopApi.runs.start(request)
      setRunId(summary.runId)
      setPhase('running')
      return summary.runId
    } catch (err) {
      setPhase('error')
      setError(err instanceof DesktopApiError ? err.message : '无法启动任务。')
      return null
    }
  }, [])

  const reset = useCallback(() => {
    resetTracking()
    setRunId(null)
    setEvents([])
    setError(null)
    setPhase('idle')
    setAttempt(0)
  }, [])

  return {
    runId,
    events,
    phase,
    error,
    reconnecting: phase === 'reconnecting',
    start,
    reset,
  }
}
