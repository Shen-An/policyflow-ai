import { browser, expect } from '@wdio/globals'

import { readStubState } from './support/stub-client'

// T113 — a crashed renderer must not be able to complete a privileged action. An
// approval decision is put in flight (the stub holds it open), then the renderer is
// forcefully crashed. Main aborts the in-flight request, so no server-side approval is
// committed, while the durable run on the server is untouched and remains authoritative.

const DIGEST = 'a'.repeat(64)

describe('T113 renderer crash containment', () => {
  before(async () => {
    await browser.waitUntil(
      async () =>
        (await browser.execute(
          () => typeof (window as unknown as { policyflow?: unknown }).policyflow,
        )) === 'object',
      { timeout: 15_000, timeoutMsg: 'window.policyflow was never exposed' },
    )
  })

  it('cancels a not-yet-approved request on crash and leaves the durable run intact', async () => {
    // Authenticate and start a durable run on the (stub) server.
    await browser.execute(() =>
      (window as unknown as {
        policyflow: { auth: { login: (r: unknown) => Promise<unknown> } }
      }).policyflow.auth.login({ username: 'employee', password: 'secret-pw' }),
    )
    const started = (await browser.execute(() =>
      (window as unknown as {
        policyflow: { runs: { start: (r: unknown) => Promise<unknown> } }
      }).policyflow.runs.start({ kind: 'reimbursement', input: {} }),
    )) as { ok: boolean; value?: { runId: string } }
    expect(started.ok).toBe(true)
    const runId = started.value?.runId ?? ''
    expect(runId.length > 0).toBe(true)

    // Fire a privileged approval decision WITHOUT awaiting it — the stub holds the
    // request open so it is still in flight when the renderer dies.
    await browser.execute(
      (id, digest) => {
        const bridge = (window as unknown as {
          policyflow: { approvals: { decide: (r: unknown) => Promise<unknown> } }
        }).policyflow
        ;(window as unknown as { __approval: Promise<unknown> }).__approval = bridge.approvals.decide({
          runId: id,
          approvalId: 'appr-1',
          decision: 'approve',
          actionDigest: digest,
        })
      },
      runId,
      DIGEST,
    )

    // Wait until the backend has actually received the pending approval request.
    await browser.waitUntil(async () => (await readStubState()).approvalReceived, {
      timeout: 10_000,
      timeoutMsg: 'approval request never reached the backend',
    })
    const beforeCrash = await readStubState()
    expect(beforeCrash.approvalCommitted).toBe(false)

    // Forcefully crash the renderer from the main process.
    await browser.electron.execute((electron) => {
      const win = electron.BrowserWindow.getAllWindows()[0]
      win?.webContents.forcefullyCrashRenderer()
    })

    // Main should abort the in-flight privileged request; the backend sees the socket
    // close without ever committing an approval. All assertions read server state over
    // the network (the renderer is gone).
    await browser.waitUntil(async () => (await readStubState()).approvalAborted, {
      timeout: 10_000,
      timeoutMsg: 'crashed renderer did not cancel the in-flight approval request',
    })
    const afterCrash = await readStubState()
    expect(afterCrash.approvalReceived).toBe(true)
    expect(afterCrash.approvalAborted).toBe(true)
    expect(afterCrash.approvalCommitted).toBe(false)

    // The durable server-side run remains authoritative and unchanged by the crash.
    expect(afterCrash.runs[runId]?.status).toBe('running')
  })
})
