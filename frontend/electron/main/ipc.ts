import { randomUUID } from 'node:crypto'

import { ipcMain, shell } from 'electron'
import type { IpcMainInvokeEvent, WebContents } from 'electron'

import {
  INVOKE_OPERATIONS,
  channelFor,
  runEventChannel,
  type InvokeOperation,
} from '../shared/operations'
import type { DesktopInfo, IpcResult } from '../shared/types'
import { ApiProxy } from './api-proxy'
import { isExternalLinkAllowed } from './security'
import { isTrustedSenderUrl, type SenderOriginOptions } from './origin'
import { OPERATION_SCHEMAS } from './schemas'

export type IpcDependencies = {
  proxy: ApiProxy
  origin: SenderOriginOptions
  appVersion: string
}

/** Tracks in-flight AbortControllers per renderer so a crash aborts them all. */
class InflightRegistry {
  private readonly byWebContents = new Map<number, Set<AbortController>>()

  track(webContentsId: number, controller: AbortController): void {
    let set = this.byWebContents.get(webContentsId)
    if (!set) {
      set = new Set()
      this.byWebContents.set(webContentsId, set)
    }
    set.add(controller)
  }

  untrack(webContentsId: number, controller: AbortController): void {
    this.byWebContents.get(webContentsId)?.delete(controller)
  }

  abortForWebContents(webContentsId: number): void {
    for (const controller of this.byWebContents.get(webContentsId) ?? []) controller.abort()
    this.byWebContents.delete(webContentsId)
  }

  abortEverything(): void {
    for (const set of this.byWebContents.values()) for (const controller of set) controller.abort()
    this.byWebContents.clear()
  }
}

/**
 * Register one schema-validated handler per operation-specific channel. There is no
 * generic channel: every handler first checks the sender frame origin, then validates
 * the payload against that operation's schema, then dispatches to the proxy. Errors
 * are redacted. Unknown channels have no handler and simply do not exist.
 */
export function registerIpcHandlers(deps: IpcDependencies): () => void {
  const inflight = new InflightRegistry()
  const subscriptions = new Map<string, { controller: AbortController; webContentsId: number }>()
  const watched = new Set<number>()

  const attachCrashHandler = (sender: WebContents): void => {
    if (watched.has(sender.id)) return
    watched.add(sender.id)
    const onGone = () => {
      // A destroyed/crashed renderer must not keep privileged requests alive: abort
      // every in-flight proxy call and event stream for it. The durable server-side
      // run is untouched — the server remains authoritative — so no approval or
      // submission is ever produced on behalf of a renderer that is already gone.
      inflight.abortForWebContents(sender.id)
      for (const [id, sub] of subscriptions) {
        if (sub.webContentsId === sender.id) {
          sub.controller.abort()
          subscriptions.delete(id)
        }
      }
      watched.delete(sender.id)
    }
    sender.once('destroyed', onGone)
    sender.once('render-process-gone', onGone)
  }

  const withInflight = async <T>(
    event: IpcMainInvokeEvent,
    run: (signal: AbortSignal) => Promise<T>,
  ): Promise<T> => {
    attachCrashHandler(event.sender)
    const controller = new AbortController()
    inflight.track(event.sender.id, controller)
    try {
      return await run(controller.signal)
    } finally {
      inflight.untrack(event.sender.id, controller)
    }
  }

  const startSubscription = (event: IpcMainInvokeEvent, runId: string): { subscriptionId: string } => {
    attachCrashHandler(event.sender)
    const subscriptionId = randomUUID()
    const controller = new AbortController()
    const sender = event.sender
    subscriptions.set(subscriptionId, { controller, webContentsId: sender.id })
    inflight.track(sender.id, controller)
    void (async () => {
      try {
        for await (const runEvent of deps.proxy.streamRunEvents(runId, controller.signal)) {
          if (sender.isDestroyed()) break
          sender.send(runEventChannel(subscriptionId), runEvent)
        }
      } catch {
        // Stream errors are non-fatal to the boundary; the renderer can resubscribe.
      } finally {
        inflight.untrack(sender.id, controller)
        subscriptions.delete(subscriptionId)
      }
    })()
    return { subscriptionId }
  }

  const stopSubscription = (subscriptionId: string): void => {
    const sub = subscriptions.get(subscriptionId)
    if (!sub) return
    sub.controller.abort()
    inflight.untrack(sub.webContentsId, sub.controller)
    subscriptions.delete(subscriptionId)
  }

  const handlers: Record<InvokeOperation, (event: IpcMainInvokeEvent, payload: unknown) => Promise<unknown>> = {
    'auth.login': (event, payload) =>
      withInflight(event, () => deps.proxy.login(payload as { username: string; password: string })),
    'auth.logout': () => deps.proxy.logout(),
    'auth.currentUser': () => deps.proxy.currentUser(),
    'runs.start': (event, payload) =>
      withInflight(event, (signal) => deps.proxy.startRun(payload as never, signal)),
    'runs.get': (event, payload) =>
      withInflight(event, (signal) => deps.proxy.getRun((payload as { runId: string }).runId, signal)),
    'runs.cancel': (_event, payload) => deps.proxy.cancelRun((payload as { runId: string }).runId),
    'runs.events.subscribe': (event, payload) =>
      Promise.resolve(startSubscription(event, (payload as { runId: string }).runId)),
    'runs.events.unsubscribe': (_event, payload) => {
      stopSubscription((payload as { subscriptionId: string }).subscriptionId)
      return Promise.resolve(undefined)
    },
    'materials.declare': (event, payload) =>
      withInflight(event, (signal) => deps.proxy.declareMaterial(payload as never, signal)),
    'workspace.select': (event, payload) =>
      withInflight(event, (signal) => deps.proxy.selectWorkspace(payload as never, signal)),
    'workspace.query': (event, payload) =>
      withInflight(event, (signal) => deps.proxy.queryWorkspace(payload as never, signal)),
    'approvals.decide': (event, payload) =>
      withInflight(event, (signal) => deps.proxy.decideApproval(payload as never, signal)),
    'system.openExternal': (_event, payload) => {
      const { url } = payload as { url: string }
      if (!isExternalLinkAllowed(url)) return Promise.resolve({ opened: false })
      void shell.openExternal(url)
      return Promise.resolve({ opened: true })
    },
    'system.info': () =>
      Promise.resolve<DesktopInfo>({
        platform: process.platform,
        appVersion: deps.appVersion,
        hasNodeAccess: false,
      }),
  }

  for (const operation of INVOKE_OPERATIONS) {
    ipcMain.handle(
      channelFor(operation),
      async (event: IpcMainInvokeEvent, rawPayload: unknown): Promise<IpcResult<unknown>> => {
        if (!isTrustedSenderUrl(event.senderFrame?.url, deps.origin)) {
          return { ok: false, error: { code: 'FORBIDDEN_ORIGIN', message: '调用来源未被信任。', retryable: false } }
        }
        const parsed = OPERATION_SCHEMAS[operation].safeParse(rawPayload ?? {})
        if (!parsed.success) {
          return { ok: false, error: { code: 'INVALID_PAYLOAD', message: '请求参数未通过校验。', retryable: false } }
        }
        try {
          const value = await handlers[operation](event, parsed.data)
          return { ok: true, value }
        } catch (error) {
          return { ok: false, error: deps.proxy.redactError(error) }
        }
      },
    )
  }

  return () => {
    for (const operation of INVOKE_OPERATIONS) ipcMain.removeHandler(channelFor(operation))
    for (const sub of subscriptions.values()) sub.controller.abort()
    subscriptions.clear()
    inflight.abortEverything()
  }
}
