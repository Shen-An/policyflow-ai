import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http'

// Deterministic stub of the enterprise backend, used ONLY by the Electron E2E suites.
// The capability boundary under test (main/preload/renderer) is real; this fixture just
// gives the main-side proxy something predictable to talk to so we can observe token
// injection, cancellation, redaction and renderer-crash behaviour (Phase 7), and drive
// the full chat and reimbursement-approval workflows end to end (Phase 8 / Stage 8).
//
// Phase 7 invariants that MUST be preserved (asserted by security.e2e / ipc-contract /
// renderer-crash):
//   - unknown run ids streaming `/events` get the generic two-event stream + keep-alive
//     and `sseOpened` / `sseOpen` accounting (ipc-contract 'run-stream');
//   - `POST /runs/{id}/approvals/{aid}` HANGS for non-Stage-8 approval ids (e.g. 'appr-1')
//     and records `approvalReceived` / `approvalAborted`, never `approvalCommitted`
//     (renderer-crash). Stage 8 uses the dedicated `s8-` approval-id namespace and its
//     own state fields so it can respond without ever touching those globals.

export type StubRun = {
  run_id: string
  status: string
  kind: string
  created_at: string
  input: Record<string, unknown>
}

export type StubState = {
  authHeaderWasBearer: boolean
  approvalReceived: boolean
  approvalAborted: boolean
  approvalCommitted: boolean
  sseOpened: number
  sseOpen: number
  runs: Record<string, StubRun>
  // --- Stage 8 additions (kept separate from the Phase 7 globals above) ---------
  s8ApprovalDecisions: Array<{ runId: string; approvalId: string; decision: string; actionDigest: string }>
  s8EventStreamOpensByRun: Record<string, number>
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

function sseFrame(payload: Record<string, unknown>): string {
  return `event: run_event\ndata: ${JSON.stringify(payload)}\n\n`
}

const HEX64 = (seed: string): string => {
  // Deterministic 64-char hex digest derived from a seed (fixture only, not crypto).
  let out = ''
  let acc = 0
  for (let i = 0; i < 64; i += 1) {
    acc = (acc * 31 + seed.charCodeAt(i % seed.length) + i) % 16
    out += acc.toString(16)
  }
  return out
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
    s8ApprovalDecisions: [],
    s8EventStreamOpensByRun: {},
  }

  // Per-run hook that an OPEN file_workflow event stream registers so a later approval
  // decision can push its terminal events into that same stream. Kept off `state` since
  // functions are not JSON-serialisable for `/__test/state`.
  const workflowEmitters = new Map<string, (decision: string) => void>()
  const pendingDecisions = new Map<string, string>()

  function questionOf(run: StubRun | undefined): string {
    const q = run?.input?.question
    return typeof q === 'string' ? q : ''
  }

  function streamChat(res: ServerResponse, runId: string, run: StubRun | undefined, openCount: number): void {
    const question = questionOf(run)
    const refusal = /no-evidence|无依据|查无/u.test(question)
    const dropsFirst = /reconnect|断线/u.test(question)
    let seq = 0
    const frame = (extra: Record<string, unknown>) => {
      seq += 1
      res.write(
        sseFrame({
          event_id: `evt-${runId}-${seq}`,
          run_id: runId,
          sequence: seq,
          occurred_at: '2026-01-01T00:00:00Z',
          ...extra,
        }),
      )
    }
    const terminal = () => {
      if (run) run.status = 'succeeded'
      frame({
        event_type: 'run.completed',
        stage: 'generation',
        status: 'succeeded',
        payload: refusal
          ? {
              evidence_gate: 'insufficient_evidence',
              answer:
                '抱歉，我没有找到足以回答这个问题的制度依据，因此不能给出结论。你可以换一种问法，或在相关知识库补充文件后再试。',
              citations: [],
            }
          : {
              evidence_gate: 'supported',
              answer:
                '## 结论\n\n根据《差旅与报销管理办法》，**市内交通费**可据实报销，需附行程说明。\n\n- 单次上限 200 元\n- 需在 30 天内提交\n',
              citations: [
                { title: '差旅与报销管理办法 v3', snippet: '第 4 条 市内交通费据实报销…', source: '制度库' },
              ],
            },
      })
    }

    frame({ event_type: 'stage.update', stage: 'planning', status: 'running', payload: { label: '理解问题' } })
    frame({
      event_type: 'stage.update',
      stage: 'retrieval',
      status: 'running',
      payload: {
        label: '检索制度依据',
        evidence: [{ title: '差旅与报销管理办法 v3', snippet: '第 4 条 市内交通费据实报销…', source: '制度库' }],
      },
    })
    frame({ event_type: 'stage.update', stage: 'generation', status: 'running', payload: { label: '生成答复' } })

    if (dropsFirst && openCount < 2) {
      // Simulate an SSE drop before the terminal event. The durable run stays 'running'
      // (authoritative via GET /runs/{id}); the renderer must reconnect to receive it.
      res.end()
      return
    }
    if (dropsFirst) {
      // Reconnected open: hold briefly so the renderer's reconnecting state is observable,
      // then deliver the terminal answer. The stage frames above are replayed (the client
      // de-dupes by eventId) so only the terminal event is new.
      setTimeout(() => {
        if (res.writableEnded) return
        terminal()
        res.end()
      }, 1000)
      return
    }
    terminal()
    res.end()
  }

  function streamWorkflow(res: ServerResponse, runId: string, run: StubRun | undefined): void {
    let seq = 0
    const frame = (extra: Record<string, unknown>) => {
      seq += 1
      res.write(
        sseFrame({
          event_id: `evt-${runId}-${seq}`,
          run_id: runId,
          sequence: seq,
          occurred_at: '2026-01-01T00:00:00Z',
          ...extra,
        }),
      )
    }
    // A run may carry a demo `scenario` so each edge state (permission / conflict /
    // stale / error) is reproducible end to end. The approval id encodes it so the
    // decide handler can return the matching outcome.
    const scenarioRaw = run?.input?.scenario
    const scenario = typeof scenarioRaw === 'string' ? scenarioRaw : ''
    const scenarioToken =
      scenario === 'permission' ? 'forbidden' : scenario === 'conflict' ? 'conflict' : scenario === 'stale' ? 'stale' : ''
    const approvalId = scenarioToken ? `s8-appr-${scenarioToken}-${runId}` : `s8-appr-${runId}`
    const digest = HEX64(runId)

    frame({ event_type: 'stage.update', stage: 'provisioning', status: 'running', payload: { label: '准备工作区' } })
    frame({ event_type: 'stage.update', stage: 'processing', status: 'running', payload: { label: '整理报销材料' } })

    if (scenario === 'error') {
      if (run) run.status = 'terminal_failed'
      frame({
        event_type: 'run.failed',
        stage: 'processing',
        status: 'terminal_failed',
        payload: { code: 'WORKFLOW_FAILED', message: '生成报销材料时出错，未产生任何正式文件。' },
      })
      res.end()
      return
    }
    frame({
      event_type: 'change_set.ready',
      stage: 'changes_ready',
      status: 'running',
      payload: {
        changeSet: {
          id: `cs-${runId}`,
          state: 'draft',
          summary: '生成报销申请表与费用明细（批准前为草稿）',
          sideEffectClass: 'file.write',
          items: [
            {
              path: 'reimbursement/报销申请表.md',
              operation: 'modify',
              beforeHash: HEX64(`${runId}-a-before`),
              afterHash: HEX64(`${runId}-a-after`),
              diff: '@@ -1,3 +1,4 @@\n 申请人：张三\n-金额：\n+金额：¥1,280\n+事由：市内交通费\n',
              preview: '# 报销申请表\n\n申请人：张三\n金额：¥1,280\n事由：市内交通费\n',
            },
            {
              path: 'reimbursement/费用明细.csv',
              operation: 'create',
              beforeHash: null,
              afterHash: HEX64(`${runId}-b-after`),
              diff: '+日期,项目,金额\n+2026-01-02,出租车,¥80\n+2026-01-03,地铁,¥12\n',
              preview: '日期,项目,金额\n2026-01-02,出租车,¥80\n2026-01-03,地铁,¥12\n',
            },
          ],
        },
      },
    })
    frame({
      event_type: 'approval.requested',
      stage: 'awaiting_approval',
      status: 'waiting_approval',
      payload: {
        approval: {
          approvalId,
          action: 'submit_to_connector',
          destination: '财务系统 / 报销单据',
          actionDigest: digest,
          status: 'pending',
          expiresAt: '2026-12-31T23:59:59Z',
          sideEffects: ['向财务系统提交 1 份报销单', '生成 2 个草稿文件的正式版本'],
          files: [
            { path: 'reimbursement/报销申请表.md', sha256: HEX64(`${runId}-a-after`) },
            { path: 'reimbursement/费用明细.csv', sha256: HEX64(`${runId}-b-after`) },
          ],
        },
      },
    })

    const emitTerminal = (decision: string) => {
      frame({
        event_type: 'approval.decided',
        stage: 'awaiting_approval',
        status: decision === 'approve' ? 'approved' : 'rejected',
        payload: { approvalId, decision: decision === 'approve' ? 'approved' : 'rejected' },
      })
      if (decision === 'approve') {
        if (run) run.status = 'succeeded'
        frame({
          event_type: 'run.completed',
          stage: 'submission',
          status: 'succeeded',
          payload: {
            submission: {
              submissionId: `sub-${runId}`,
              receipt: `RCPT-${runId.toUpperCase()}`,
              destination: '财务系统 / 报销单据',
            },
          },
        })
      } else {
        if (run) run.status = 'succeeded'
        frame({
          event_type: 'run.completed',
          stage: 'closed',
          status: 'succeeded',
          payload: { rejected: true },
        })
      }
      workflowEmitters.delete(runId)
      res.end()
    }

    // If the decision already arrived before the stream opened, resolve immediately.
    const already = pendingDecisions.get(runId)
    if (already) {
      pendingDecisions.delete(runId)
      emitTerminal(already)
      return
    }
    workflowEmitters.set(runId, emitTerminal)
  }

  function streamGeneric(res: ServerResponse, runId: string): void {
    // Phase 7 behaviour (ipc-contract 'run-stream'): two events + keep-alive, closes on
    // client disconnect. Preserved verbatim for unknown / non-Stage-8 runs.
    let sequence = 0
    const emit = () => {
      sequence += 1
      res.write(
        sseFrame({
          event_id: `evt-${sequence}`,
          run_id: runId,
          sequence,
          event_type: 'stage.update',
          stage: 'retrieval',
          status: 'running',
          payload: { note: 'stub event' },
          occurred_at: '2026-01-01T00:00:00Z',
        }),
      )
    }
    emit()
    emit()
    const keepAlive = setInterval(() => res.write(': keep-alive\n\n'), 500)
    res.on('close', () => clearInterval(keepAlive))
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
    if (pathname === '/__test/reset' && method === 'POST') {
      // Allow a spec to isolate itself from cross-spec Stage-8 state without touching
      // the Phase 7 globals the crash/contract specs rely on.
      state.s8ApprovalDecisions = []
      state.s8EventStreamOpensByRun = {}
      workflowEmitters.clear()
      pendingDecisions.clear()
      send(res, 200, { ok: true })
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
        const record = (body ?? {}) as { kind?: string; input?: Record<string, unknown> }
        const runId = `run-${Object.keys(state.runs).length + 1}`
        const kind = record.kind ?? 'reimbursement'
        const run: StubRun = {
          run_id: runId,
          status: 'running',
          kind,
          created_at: '2026-01-01T00:00:00Z',
          input: record.input ?? {},
        }
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
      // Decrement exactly once when the client disconnects, matching the Phase 7
      // `req.on('close')` accounting the ipc-contract cancel test relies on.
      let accounted = true
      req.on('close', () => {
        if (accounted) {
          accounted = false
          state.sseOpen = Math.max(0, state.sseOpen - 1)
        }
      })
      res.writeHead(200, {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        Connection: 'keep-alive',
      })
      const runId = eventsMatch[1]
      const run = state.runs[runId]
      state.s8EventStreamOpensByRun[runId] = (state.s8EventStreamOpensByRun[runId] ?? 0) + 1
      const openCount = state.s8EventStreamOpensByRun[runId]
      if (run?.kind === 'chat') {
        streamChat(res, runId, run, openCount)
      } else if (run?.kind === 'file_workflow') {
        req.on('close', () => workflowEmitters.delete(runId))
        streamWorkflow(res, runId, run)
      } else {
        streamGeneric(res, runId)
      }
      return
    }

    // --- approval ---------------------------------------------------------
    if (approvalMatch && method === 'POST') {
      const runId = approvalMatch[1]
      const approvalId = approvalMatch[2]
      // Stage 8 namespace: respond (and push terminal workflow events). Everything else
      // (incl. renderer-crash 'appr-1') HANGS exactly as in Phase 7.
      if (approvalId.startsWith('s8-')) {
        void readBody(req).then((body) => {
          const record = (body ?? {}) as { decision?: string; action_digest?: string }
          const decision = record.decision === 'reject' ? 'reject' : 'approve'
          state.s8ApprovalDecisions.push({
            runId,
            approvalId,
            decision,
            actionDigest: record.action_digest ?? '',
          })
          if (approvalId.includes('forbidden')) {
            send(res, 403, { error: { code: 'FORBIDDEN', message: '无权审批该请求' } })
            return
          }
          if (approvalId.includes('stale')) {
            send(res, 409, { error: { code: 'APPROVAL_STALE', message: '材料已变更，审批摘要失效' } })
            return
          }
          if (approvalId.includes('conflict')) {
            send(res, 409, { error: { code: 'APPROVAL_CONFLICT', message: '该请求已被他人处理' } })
            return
          }
          send(res, 200, { approval_id: approvalId, status: decision === 'approve' ? 'approved' : 'rejected' })
          const emitter = workflowEmitters.get(runId)
          if (emitter) emitter(decision)
          else pendingDecisions.set(runId, decision)
        })
        return
      }
      // Phase 7 hang path (renderer-crash T113): never respond.
      state.approvalReceived = true
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
