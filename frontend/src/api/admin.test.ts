import { describe, expect, it, vi } from 'vitest'
import {
  createGrant,
  listGrants,
  retryReconciliation,
  revokeGrant,
  type AdminGrant,
} from './admin'
import { apiClient } from './client'

const rawGrant = {
  grant_id: 'g1',
  user_id: 'u1',
  role_id: 'r1',
  scope: 'tenant',
  valid_from: '2026-10-01T00:00:00Z',
  expires_at: null,
  revoked_at: null,
  revocation_reason: null,
  version: 1,
}

describe('admin api client', () => {
  it('filters grants by member_id, never by user_id', async () => {
    const request = vi.spyOn(apiClient, 'request')
    request.mockResolvedValueOnce({
      items: [rawGrant],
      total: 1,
      limit: 20,
      offset: 0,
    })

    const page = await listGrants({ limit: 20, offset: 0, memberId: 'u1' })
    expect(page.items[0]).toMatchObject<Partial<AdminGrant>>({
      grantId: 'g1',
      userId: 'u1',
      expiresAt: null,
    })

    const url = String(request.mock.calls.at(-1)?.[0])
    expect(url).toContain('member_id=u1')
    // `user_id` is reserved for identity claims and is rejected by the platform
    // guard when it does not match the token; the filter must not use it.
    expect(url).not.toContain('user_id=')
  })

  it('unwraps the grant from create/revoke responses', async () => {
    const request = vi.spyOn(apiClient, 'request')
    request.mockResolvedValueOnce({ grant: rawGrant, created: true })
    const created = await createGrant({ userId: 'u1', roleId: 'r1' })
    expect(created.created).toBe(true)
    expect(created.grant.grantId).toBe('g1')

    request.mockResolvedValueOnce({
      grant: { ...rawGrant, revoked_at: '2026-10-18T00:00:00Z', version: 2 },
      revoked: true,
    })
    const revoked = await revokeGrant('g1', 'cleanup')
    expect(revoked.revoked).toBe(true)
    expect(revoked.grant.revokedAt).toBe('2026-10-18T00:00:00Z')
  })

  it('maps the retry response previous/next state', async () => {
    const request = vi.spyOn(apiClient, 'request')
    request.mockResolvedValueOnce({
      issue_id: 'i1',
      state: 'open',
      previous_state: 'manual_required',
    })
    await expect(retryReconciliation('i1')).resolves.toEqual({
      issueId: 'i1',
      state: 'open',
      previousState: 'manual_required',
    })
  })
})
