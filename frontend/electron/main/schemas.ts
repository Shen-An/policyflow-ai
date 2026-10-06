import { z } from 'zod'

import type { InvokeOperation } from '../shared/operations'

// Per-operation request schemas. Main validates every renderer payload against the
// exact schema for that operation before doing any work. Unknown operations have no
// schema and are rejected outright (see ipc.ts). Keeping schemas here — never in the
// preload or renderer — means validation cannot be bypassed from a compromised page.

const identifier = z.string().trim().min(1).max(256)

export const loginSchema = z.object({
  username: z.string().trim().min(1).max(256),
  password: z.string().min(1).max(1024),
})

export const runStartSchema = z.object({
  kind: z.string().trim().min(1).max(128),
  input: z.record(z.string(), z.unknown()).default({}),
  knowledgeBaseIds: z.array(identifier).max(64).optional(),
  idempotencyKey: z.string().trim().min(1).max(256).optional(),
})

export const runIdSchema = z.object({ runId: identifier })

export const runEventsSubscribeSchema = z.object({ runId: identifier })

export const runEventsUnsubscribeSchema = z.object({
  subscriptionId: z.string().trim().min(1).max(256),
})

export const materialDeclareSchema = z.object({
  name: z.string().trim().min(1).max(512),
  purpose: z.string().trim().min(1).max(2048),
  contentHash: z
    .string()
    .trim()
    .regex(/^[A-Fa-f0-9]{64}$/u, 'contentHash must be a SHA-256 hex digest'),
  byteSize: z.number().int().nonnegative().max(1_000_000_000).optional(),
  mimeType: z.string().trim().min(1).max(256).optional(),
})

export const workspaceSelectSchema = z.object({
  runId: identifier,
  materialVersionIds: z.array(identifier).min(1).max(256),
})

export const workspaceQuerySchema = z.object({ workspaceId: identifier })

export const approvalDecideSchema = z.object({
  runId: identifier,
  approvalId: identifier,
  decision: z.enum(['approve', 'reject']),
  actionDigest: z
    .string()
    .trim()
    .regex(/^[A-Fa-f0-9]{64}$/u, 'actionDigest must be a SHA-256 hex digest'),
  reason: z.string().trim().max(2048).optional(),
})

// Only an http(s) absolute URL may even be considered for an external open; the
// allowlist in security.ts then decides whether it is actually permitted.
export const openExternalSchema = z.object({
  url: z
    .string()
    .trim()
    .min(1)
    .max(2048)
    .refine(
      (value) => value.startsWith('http://') || value.startsWith('https://'),
      'Only absolute http(s) URLs are accepted',
    ),
})

export const emptySchema = z.object({}).strict()

/** Maps each operation to the schema its payload must satisfy. */
export const OPERATION_SCHEMAS: Record<InvokeOperation, z.ZodTypeAny> = {
  'auth.login': loginSchema,
  'auth.logout': emptySchema,
  'auth.currentUser': emptySchema,
  'runs.start': runStartSchema,
  'runs.get': runIdSchema,
  'runs.cancel': runIdSchema,
  'runs.events.subscribe': runEventsSubscribeSchema,
  'runs.events.unsubscribe': runEventsUnsubscribeSchema,
  'materials.declare': materialDeclareSchema,
  'workspace.select': workspaceSelectSchema,
  'workspace.query': workspaceQuerySchema,
  'approvals.decide': approvalDecideSchema,
  'system.openExternal': openExternalSchema,
  'system.info': emptySchema,
}
