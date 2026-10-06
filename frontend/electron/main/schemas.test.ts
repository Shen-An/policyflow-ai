import { describe, expect, it } from 'vitest'

import { INVOKE_OPERATIONS, channelFor, isKnownOperationChannel, isRunEventChannel } from '../shared/operations'
import { OPERATION_SCHEMAS } from './schemas'

const HEX64 = 'a'.repeat(64)

describe('operation registry', () => {
  it('exposes a dedicated schema for every operation and no others', () => {
    expect(Object.keys(OPERATION_SCHEMAS).sort()).toEqual([...INVOKE_OPERATIONS].sort())
  })

  it('namespaces operation channels and recognises only known ones', () => {
    for (const operation of INVOKE_OPERATIONS) {
      const channel = channelFor(operation)
      expect(channel.startsWith('policyflow:')).toBe(true)
      expect(isKnownOperationChannel(channel)).toBe(true)
    }
    expect(isKnownOperationChannel('policyflow:__raw')).toBe(false)
    expect(isKnownOperationChannel('some.other.channel')).toBe(false)
    expect(isRunEventChannel('policyflow:runs.events#abc')).toBe(true)
    expect(isRunEventChannel(channelFor('runs.start'))).toBe(false)
  })
})

describe('OPERATION_SCHEMAS validation', () => {
  it('accepts a valid login and rejects empties', () => {
    expect(OPERATION_SCHEMAS['auth.login'].safeParse({ username: 'a', password: 'b' }).success).toBe(true)
    expect(OPERATION_SCHEMAS['auth.login'].safeParse({ username: '', password: '' }).success).toBe(false)
    expect(OPERATION_SCHEMAS['auth.login'].safeParse({ username: 'a' }).success).toBe(false)
  })

  it('requires a sha-256 hex digest for material content and approval digests', () => {
    const material = OPERATION_SCHEMAS['materials.declare']
    expect(material.safeParse({ name: 'n', purpose: 'p', contentHash: HEX64 }).success).toBe(true)
    expect(material.safeParse({ name: 'n', purpose: 'p', contentHash: 'nope' }).success).toBe(false)

    const approval = OPERATION_SCHEMAS['approvals.decide']
    expect(
      approval.safeParse({ runId: 'r', approvalId: 'a', decision: 'approve', actionDigest: HEX64 }).success,
    ).toBe(true)
    expect(
      approval.safeParse({ runId: 'r', approvalId: 'a', decision: 'maybe', actionDigest: HEX64 }).success,
    ).toBe(false)
    expect(
      approval.safeParse({ runId: 'r', approvalId: 'a', decision: 'approve', actionDigest: 'short' }).success,
    ).toBe(false)
  })

  it('accepts only absolute http(s) URLs for external open', () => {
    const open = OPERATION_SCHEMAS['system.openExternal']
    expect(open.safeParse({ url: 'https://help.policyflow.example' }).success).toBe(true)
    expect(open.safeParse({ url: 'file:///etc/passwd' }).success).toBe(false)
    expect(open.safeParse({ url: 'javascript:alert(1)' }).success).toBe(false)
  })

  it('requires at least one material version for a workspace selection', () => {
    const select = OPERATION_SCHEMAS['workspace.select']
    expect(select.safeParse({ runId: 'r', materialVersionIds: ['v1'] }).success).toBe(true)
    expect(select.safeParse({ runId: 'r', materialVersionIds: [] }).success).toBe(false)
  })

  it('rejects extra properties on parameterless operations', () => {
    expect(OPERATION_SCHEMAS['auth.currentUser'].safeParse({}).success).toBe(true)
    expect(OPERATION_SCHEMAS['auth.currentUser'].safeParse({ sneaky: true }).success).toBe(false)
  })
})
