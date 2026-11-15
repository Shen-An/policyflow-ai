// Offline wire-contract probe using the actual desktop proxy and Python SSE encoder.
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..')
const require = createRequire(path.join(root, 'frontend/package.json'))
const { build } = require('esbuild')
const compiled = await build({
  entryPoints: [path.join(root, 'frontend/electron/main/api-proxy.ts')],
  bundle: true,
  write: false,
  format: 'esm',
  platform: 'node',
})
const { ApiProxy } = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString('base64')}`)
const python = process.env.POLICYFLOW_PYTHON || 'python'
const raw = execFileSync(python, ['-c',
  'import json; from backend.app.sse.endpoint import _frame; from sse_starlette.sse import ServerSentEvent; print(json.dumps(ServerSentEvent(**_frame("run.succeeded", {}, "1-0")).encode().decode()))',
], { cwd: root, encoding: 'utf8' })
const wire = JSON.parse(raw)

async function parse(body) {
  const proxy = new ApiProxy({
    backendBaseUrl: 'http://127.0.0.1:8000',
    vault: { getAccessToken: () => null },
    fetchImpl: async () => new Response(body, { headers: { 'content-type': 'text/event-stream' } }),
  })
  const events = []
  for await (const event of proxy.streamRunEvents('review-run', new AbortController().signal)) events.push(event)
  return events
}

const native = await parse(wire)
const normalized = await parse(wire.replaceAll('\r\n', '\n'))
assert.equal(native.length, 0)
assert.equal(normalized.length, 1)
assert.equal(normalized[0].eventType, 'unknown')
assert.equal(normalized[0].eventId, '')
console.log(JSON.stringify({ native_crlf_event_count: native.length, normalized_lf_events: normalized }, null, 2))
console.log('Confirmed: native SSE frames are dropped; LF normalization alone still loses event/id fields.')
