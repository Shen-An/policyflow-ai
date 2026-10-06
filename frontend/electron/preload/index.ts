import { contextBridge, ipcRenderer } from 'electron'
import type { IpcRendererEvent } from 'electron'

import { channelFor, runEventChannel, type InvokeOperation } from '../shared/operations'
import type { IpcResult, RawPolicyflowBridge, RunEvent, Unsubscribe } from '../shared/types'

// This preload runs in the sandboxed, context-isolated world. It exposes ONLY the
// typed capability surface below via contextBridge. It never puts `ipcRenderer`,
// `require`, `process` or any raw channel on the page. Each method invokes exactly
// one operation-specific channel; main validates origin + schema before acting.

function invoke<T>(operation: InvokeOperation, payload: unknown): Promise<IpcResult<T>> {
  return ipcRenderer.invoke(channelFor(operation), payload) as Promise<IpcResult<T>>
}

async function subscribeEvents(
  runId: string,
  onEvent: (event: RunEvent) => void,
): Promise<Unsubscribe> {
  const result = await invoke<{ subscriptionId: string }>('runs.events.subscribe', { runId })
  if (!result.ok) throw new Error(result.error.message)
  const channel = runEventChannel(result.value.subscriptionId)
  // The raw IpcRendererEvent is consumed here and never handed to the renderer; the
  // page callback receives only the sanitized, cloned event payload.
  const listener = (_event: IpcRendererEvent, data: RunEvent) => onEvent(data)
  ipcRenderer.on(channel, listener)
  return () => {
    ipcRenderer.removeListener(channel, listener)
    void invoke('runs.events.unsubscribe', { subscriptionId: result.value.subscriptionId })
  }
}

const bridge: RawPolicyflowBridge = {
  auth: {
    login: (request) => invoke('auth.login', request),
    logout: () => invoke('auth.logout', {}),
    currentUser: () => invoke('auth.currentUser', {}),
  },
  runs: {
    start: (request) => invoke('runs.start', request),
    get: (runId) => invoke('runs.get', { runId }),
    cancel: (runId) => invoke('runs.cancel', { runId }),
    subscribeEvents,
  },
  materials: {
    declare: (request) => invoke('materials.declare', request),
  },
  workspace: {
    select: (request) => invoke('workspace.select', request),
    query: (request) => invoke('workspace.query', request),
  },
  approvals: {
    decide: (request) => invoke('approvals.decide', request),
  },
  system: {
    openExternal: (request) => invoke('system.openExternal', request),
    info: () => invoke('system.info', {}),
  },
}

contextBridge.exposeInMainWorld('policyflow', bridge)
