import { spawn, type ChildProcess } from 'node:child_process'
import { once } from 'node:events'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendRoot = path.resolve(fileURLToPath(new URL('..', import.meta.url)))

async function waitForUrl(child: ChildProcess, url: string, label: string): Promise<void> {
  const deadline = Date.now() + 120_000
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`${label} exited with code ${child.exitCode}.`)
    try {
      const response = await fetch(url)
      if (response.ok) return
    } catch {
      // Server is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  throw new Error(`Timed out waiting for ${label}.`)
}

async function seedEmployee(): Promise<void> {
  const loginResponse = await fetch('http://127.0.0.1:8000/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'admin', password: 'frontend-e2e-only' }),
  })
  if (!loginResponse.ok) throw new Error('Unable to authenticate the E2E bootstrap administrator.')
  const login = await loginResponse.json() as { access_token: string }
  const departmentsResponse = await fetch('http://127.0.0.1:8000/api/departments', {
    headers: { Authorization: `Bearer ${login.access_token}` },
  })
  if (!departmentsResponse.ok) throw new Error('Unable to load E2E departments.')
  const departments = await departmentsResponse.json() as {
    items: Array<{ id: string; code: string }>
  }
  const hrDepartment = departments.items.find((department) => department.code === 'hr')
  if (!hrDepartment) throw new Error('HR department is missing from E2E seed data.')
  const createResponse = await fetch('http://127.0.0.1:8000/api/users', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${login.access_token}` },
    body: JSON.stringify({
      username: 'frontend_employee',
      email: 'frontend_employee@example.com',
      display_name: 'Frontend Employee',
      password: 'employee-password',
      department_id: hrDepartment.id,
      role_codes: ['employee'],
    }),
  })
  if (createResponse.status !== 201 && createResponse.status !== 409) {
    throw new Error(`Unable to prepare E2E employee: HTTP ${createResponse.status}.`)
  }
}

async function seedF5Data(): Promise<void> {
  const loginResponse = await fetch('http://127.0.0.1:8000/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'admin', password: 'frontend-e2e-only' }),
  })
  if (!loginResponse.ok) throw new Error('Unable to authenticate the F5 E2E administrator.')
  const login = await loginResponse.json() as { access_token: string }
  const headers = { Authorization: `Bearer ${login.access_token}` }
  const kbResponse = await fetch('http://127.0.0.1:8000/api/knowledge-bases', { headers })
  const kbs = await kbResponse.json() as { items: Array<{ id: string; code: string }> }
  const hr = kbs.items.find((item) => item.code === 'hr')
  if (!hr) throw new Error('HR knowledge base is missing for F5 E2E.')
  const form = new FormData()
  form.append('file', new Blob(['Annual leave requires manager approval. Requests must be submitted in advance.'], { type: 'text/plain' }), 'f5-source.txt')
  form.append('title', 'F5 Leave Policy')
  const upload = await fetch(`http://127.0.0.1:8000/api/knowledge-bases/${hr.id}/documents`, {
    method: 'POST',
    headers,
    body: form,
  })
  if (!upload.ok) throw new Error(`Unable to upload F5 source document: HTTP ${upload.status}.`)
  const document = await upload.json() as { document_id: string }
  const faq = await fetch('http://127.0.0.1:8000/api/faq-drafts', {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      knowledge_base_id: hr.id,
      source_document_id: document.document_id,
      count: 2,
    }),
  })
  if (!faq.ok) throw new Error(`Unable to generate F5 FAQ data: HTTP ${faq.status}.`)
}

async function seedF6Data(): Promise<void> {
  const loginResponse = await fetch('http://127.0.0.1:8000/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'admin', password: 'frontend-e2e-only' }),
  })
  if (!loginResponse.ok) throw new Error('Unable to authenticate the F6 E2E administrator.')
  const login = await loginResponse.json() as { access_token: string }
  const headers = {
    Authorization: `Bearer ${login.access_token}`,
    'Content-Type': 'application/json',
  }
  const create = await fetch('http://127.0.0.1:8000/api/mcp/servers', {
    method: 'POST',
    headers,
    body: JSON.stringify({
      name: 'frontend_f6_mock',
      type: 'mock',
      integration_mode: 'mock',
      config: {
        mode: 'mock',
        password: 'f6-seed-secret',
        nested: { authorization: 'Bearer f6-seed-token', safe: 'visible' },
      },
      enabled: true,
    }),
  })
  if (!create.ok) throw new Error(`Unable to create F6 MCP data: HTTP ${create.status}.`)
  const server = await create.json() as { id: string }
  const health = await fetch(`http://127.0.0.1:8000/api/mcp/servers/${server.id}/health-check`, {
    method: 'POST',
    headers,
  })
  if (!health.ok) throw new Error(`Unable to check F6 MCP health: HTTP ${health.status}.`)
  const call = await fetch('http://127.0.0.1:8000/api/tools/mcp.call/run', {
    method: 'POST',
    headers,
    body: JSON.stringify({
      input: {
        server_id: server.id,
        tool_name: 'mcp.email.create_draft',
        arguments: {
          subject: 'F6 seeded tool log',
          password: 'f6-tool-secret',
          nested: { api_key: 'f6-tool-api-key', safe: 'visible' },
        },
      },
    }),
  })
  if (!call.ok) throw new Error(`Unable to create F6 Tool log: HTTP ${call.status}.`)
}

async function stopChild(child: ChildProcess): Promise<void> {
  if (child.exitCode !== null) return
  child.kill()
  await Promise.race([once(child, 'exit'), new Promise((resolve) => setTimeout(resolve, 3_000))])
  if (child.exitCode === null) {
    child.kill('SIGKILL')
    await Promise.race([once(child, 'exit'), new Promise((resolve) => setTimeout(resolve, 3_000))])
  }
}

export default async function globalSetup() {
  const pythonCommand = process.platform === 'win32' ? 'python.exe' : 'python'
  const backend = spawn(pythonCommand, ['scripts/start-e2e-backend.py'], {
    cwd: frontendRoot,
    stdio: 'ignore',
    windowsHide: true,
  })
  const productionMode = process.env.POLICYFLOW_E2E_PRODUCTION === 'true'
  const frontendPort = productionMode ? 4173 : 5173
  const viteArguments = [
    path.join(frontendRoot, 'node_modules/vite/bin/vite.js'),
    ...(productionMode ? ['preview'] : []),
    '--host',
    '127.0.0.1',
  ]
  const vite = spawn(process.execPath, viteArguments, {
    cwd: frontendRoot,
    stdio: 'ignore',
    windowsHide: true,
  })

  try {
    await Promise.all([
      waitForUrl(backend, 'http://127.0.0.1:8000/health', 'real FastAPI backend'),
      waitForUrl(
        vite,
        `http://127.0.0.1:${frontendPort}`,
        productionMode ? 'production preview' : 'Vite frontend',
      ),
    ])
    await seedEmployee()
    await seedF5Data()
    await seedF6Data()
  } catch (error) {
    await Promise.all([stopChild(backend), stopChild(vite)])
    throw error
  }

  return async () => {
    await Promise.all([stopChild(backend), stopChild(vite)])
  }
}
