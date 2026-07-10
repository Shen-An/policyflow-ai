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
  const vite = spawn(
    process.execPath,
    [path.join(frontendRoot, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1'],
    { cwd: frontendRoot, stdio: 'ignore', windowsHide: true },
  )

  try {
    await Promise.all([
      waitForUrl(backend, 'http://127.0.0.1:8000/health', 'real FastAPI backend'),
      waitForUrl(vite, 'http://127.0.0.1:5173', 'Vite frontend'),
    ])
  } catch (error) {
    await Promise.all([stopChild(backend), stopChild(vite)])
    throw error
  }

  return async () => {
    await Promise.all([stopChild(backend), stopChild(vite)])
  }
}
