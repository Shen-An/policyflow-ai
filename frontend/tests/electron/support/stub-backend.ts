import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http'

// Deterministic stub of the enterprise backend, used ONLY by the Electron security
// E2E suite. The capability boundary under test (main/preload/renderer) is real; this
// fixture just gives the main-side proxy something predictable to talk to so we can
// observe token injection, cancellation, redaction and renderer-crash behaviour.

export type StubState = {
  authHeaderWasBearer: boolean
  approvalReceived: boolean
  approvalAborted: boolean
  approvalCommitted: boolean
  sseOpened: number
  sseOpen: number
  runs: Record<string, { run_id: string; status: string; kind: string; created_at: string }>
}

export type StubBackend = {
  port: number
  state: StubState
  close: () => Promise<void>
}

function send(res: ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body)
  res.writeHead(status, { 'Content-Type': 'application/json' })
  res.end(payload)
}

async function readBody(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = []
  for await (const chunk of req) chunks.push(chunk as Buffer)
  if (chunks.length === 0) return undefined
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'))
  } catch {
    return undefined
  }
}

export function startStubBackend(port: number): Promise<StubBackend> {
  const state: StubState = {
    authHeaderWasBearer: false,
    approvalReceived: false,
    approvalAborted: false,
    approvalCommitted: false,
    sseOpened: 0,
    sseOpen: 0,
    runs: {},
  }

  const server: Server = createServer((req, res) => {
    const url = new URL(req.url ?? '/', `http://127.0.0.1:${port}`)
    const { pathname } = url
    const method = req.method ?? 'GET'
    const runMatch = /^\/api\/v2\/runs\/([^/]+)$/u.exec(pathname)
    const cancelMatch = /^\/api\/v2\/runs\/([^/]+)\/cancel$/u.exec(pathname)
    const eventsMatch = /^\/api\/v2\/runs\/([^/]+)\/events$/u.exec(pathname)
    const approvalMatch = /^\/api\/v2\/runs\/([^/]+)\/approvals\/([^/]+)$/u.exec(pathname)

    // --- test control -----------------------------------------------------
    if (pathname === '/__test/state') {
      send(res, 200, state)
      return
    }

    // --- auth -------------------------------------------------------------
    if (pathname === '/api/auth/login' && method === 'POST') {
      void readBody(req).then(() =>
        send(res, 200, {
          access_token: 'stub-access-token-value',
          token_type: 'bearer',
          expires_in: 3600,
          user: { id: 'u1', username: 'employee', display_name: 'Employee One', roles: ['employee'] },
        }),
      )
      return
    }
    if (pathname === '/api/auth/me' && method === 'GET') {
      const auth = req.headers.authorization ?? ''
      state.authHeaderWasBearer = auth.toLowerCase().startsWith('bearer ')
      send(res, 200, {
        id: 'u1',
        username: 'employee',
        email: 'employee@example.com',
        display_name: 'Employee One',
        department: null,
        roles: ['employee'],
        status: 'active',
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      })
      return
    }

    // --- runs -------------------------------------------------------------
    if (pathname === '/api/v2/runs' && method === 'POST') {
      void readBody(req).then((body) => {
        const runId = `run-${Object.keys(state.runs).length + 1}`
        const kind = (body as { kind?: string } | undefined)?.kind ?? 'reimbursement'
        const run = { run_id: runId, status: 'running', kind, created_at: '2026-01-01T00:00:00Z' }
        state.runs[runId] = run
        send(res, 201, run)
      })
      return
    }
    if (runMatch && method === 'GET') {
      const run = state.runs[runMatch[1]]
      if (run) send(res, 200, run)
      else send(res, 404, { error: { code: 'NOT_FOUND', message: 'unknown run' } })
      return
    }
    if (cancelMatch && method === 'POST') {
      const run = state.runs[cancelMatch[1]]
      if (run) run.status = 'cancelled'
      send(res, 200, { run_id: cancelMatch[1], status: 'cancelled' })
      return
    }
    if (eventsMatch && method === 'GET') {
      state.sseOpened += 1
      state.sseOpen += 1
      res.writeHead(200, {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        Connection: 'keep-alive',
      })
      const runId = eventsMatch[1]
      let sequence = 0
      const emit = () => {
        sequence += 1
        res.write(
          `event: run_event\ndata: ${JSON.stringify({
            event_id: `evt-${sequence}`,
            run_id: runId,
            sequence,
            event_type: 'stage.update',
            stage: 'retrieval',
            status: 'running',
            payload: { note: 'stub event' },
            occurred_at: '2026-01-01T00:00:00Z',
          })}\n\n`,
        )
      }
      emit()
      emit()
      const keepAlive = setInterval(() => res.write(': keep-alive\n\n'), 500)
      const cleanup = () => {
        clearInterval(keepAlive)
        state.sseOpen = Math.max(0, state.sseOpen - 1)
      }
      req.on('close', cleanup)
      return
    }

    // --- approval (intentionally hangs; records abort vs commit) -----------
    if (approvalMatch && method === 'POST') {
      state.approvalReceived = true
      // Never respond: the request stays in-flight so a renderer crash / cancel can
      // be observed to abort it before any server-side approval is "committed". A real
      // commit would call send(...) and set approvalCommitted; this fixture never does.
      req.on('close', () => {
        if (!res.writableEnded) state.approvalAborted = true
      })
      void readBody(req)
      return
    }

    // --- materials / workspaces ------------------------------------------
    if (pathname === '/api/v2/materials' && method === 'POST') {
      void readBody(req).then(() =>
        send(res, 201, { material_id: 'mat-1', version_id: 'matv-1', status: 'declared' }),
      )
      return
    }
    if (pathname === '/api/v2/workspaces' && method === 'POST') {
      void readBody(req).then((body) =>
        send(res, 201, {
          workspace_id: 'ws-1',
          status: 'ready',
          run_id: (body as { run_id?: string } | undefined)?.run_id ?? null,
        }),
      )
      return
    }
    if (/^\/api\/v2\/workspaces\/[^/]+$/u.test(pathname) && method === 'GET') {
      send(res, 200, { workspace_id: pathname.split('/').pop(), status: 'ready', run_id: 'run-1' })
      return
    }

    send(res, 404, { error: { code: 'NOT_FOUND', message: 'unhandled stub route' } })
  })

  return new Promise((resolve) => {
    server.listen(port, '127.0.0.1', () => {
      resolve({
        port,
        state,
        close: () =>
          new Promise<void>((done) => {
            server.closeAllConnections?.()
            server.close(() => done())
          }),
      })
    })
  })
}
