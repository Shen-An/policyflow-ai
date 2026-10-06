// Single source of truth for the operation-specific IPC channels that bridge the
// hardened renderer to the Electron main process.
//
// There is deliberately NO generic/raw channel: every capability is its own named
// operation so main can validate sender origin + a per-operation schema and reject
// everything else. The renderer never receives `ipcRenderer`; it can only reach
// these named operations through the typed `window.policyflow` surface (preload).

export const IPC_CHANNEL_PREFIX = 'policyflow:' as const

/** Request/response operations exposed to the renderer via `ipcRenderer.invoke`. */
export const INVOKE_OPERATIONS = [
  'auth.login',
  'auth.logout',
  'auth.currentUser',
  'runs.start',
  'runs.get',
  'runs.cancel',
  'runs.events.subscribe',
  'runs.events.unsubscribe',
  'materials.declare',
  'workspace.select',
  'workspace.query',
  'approvals.decide',
  'system.openExternal',
  'system.info',
] as const

export type InvokeOperation = (typeof INVOKE_OPERATIONS)[number]

export function channelFor(operation: InvokeOperation): string {
  return `${IPC_CHANNEL_PREFIX}${operation}`
}

const KNOWN_CHANNELS = new Set<string>(INVOKE_OPERATIONS.map(channelFor))

/** True only for a channel that maps to a registered operation handler. */
export function isKnownOperationChannel(channel: string): boolean {
  return KNOWN_CHANNELS.has(channel)
}

// Run events are streamed from main to the renderer on a per-subscription channel
// so a renderer callback never receives a raw `IpcRendererEvent`. The subscription
// id is minted by main; the renderer only ever observes the sanitized event payload.
export const RUN_EVENT_CHANNEL_PREFIX = `${IPC_CHANNEL_PREFIX}runs.events#` as const

export function runEventChannel(subscriptionId: string): string {
  return `${RUN_EVENT_CHANNEL_PREFIX}${subscriptionId}`
}

export function isRunEventChannel(channel: string): boolean {
  return channel.startsWith(RUN_EVENT_CHANNEL_PREFIX)
}
