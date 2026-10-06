import { execSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const projectRoot = path.dirname(fileURLToPath(import.meta.url))
const mainEntry = path.join(projectRoot, 'dist-electron', 'main', 'index.cjs')
const rendererIndex = path.join(projectRoot, 'dist', 'index.html')

// On Windows the service's appEntryPoint mode launches node_modules/.bin/electron.CMD,
// a batch shim whose process exits immediately so ChromeDriver reports Chrome as
// crashed. Launch the real electron.exe directly and pass the bundled main as an arg;
// on other platforms the .bin symlink works, so the normal appEntryPoint path is used.
const electronServiceOptions =
  process.platform === 'win32'
    ? {
        appBinaryPath: path.join(projectRoot, 'node_modules', 'electron', 'dist', 'electron.exe'),
        appArgs: [`--app=${mainEntry}`],
      }
    : { appEntryPoint: mainEntry }

// Point the app at the deterministic stub backend started in onPrepare. The security
// boundary under test is real; only the backend it proxies to is a test fixture.
const STUB_PORT = Number(process.env.POLICYFLOW_STUB_PORT ?? 59117)
process.env.POLICYFLOW_API_BASE_URL = `http://127.0.0.1:${STUB_PORT}`
delete process.env.ELECTRON_RENDERER_URL

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let stub: { close: () => Promise<void>; state: any } | undefined

export const config: WebdriverIO.Config = {
  runner: 'local',
  tsConfigPath: path.join(projectRoot, 'tsconfig.wdio.json'),

  specs: [path.join(projectRoot, 'tests', 'electron', '*.e2e.ts')],
  suites: {
    security: [
      path.join(projectRoot, 'tests', 'electron', 'security.e2e.ts'),
      path.join(projectRoot, 'tests', 'electron', 'ipc-contract.e2e.ts'),
      path.join(projectRoot, 'tests', 'electron', 'navigation.e2e.ts'),
      path.join(projectRoot, 'tests', 'electron', 'renderer-crash.e2e.ts'),
    ],
  },

  maxInstances: 1,
  capabilities: [
    {
      browserName: 'electron',
      'wdio:electronServiceOptions': electronServiceOptions,
    },
  ],

  services: ['electron'],
  framework: 'mocha',
  reporters: ['spec'],
  mochaOpts: { ui: 'bdd', timeout: 90_000 },
  logLevel: 'warn',
  waitforTimeout: 15_000,

  async onPrepare() {
    // Always refresh the main/preload bundle so the suite tests current code.
    execSync('node scripts/build-electron.mjs', { cwd: projectRoot, stdio: 'inherit' })
    if (!existsSync(rendererIndex)) {
      execSync('npm run build', { cwd: projectRoot, stdio: 'inherit' })
    }
    const { startStubBackend } = await import('./tests/electron/support/stub-backend.ts')
    stub = await startStubBackend(STUB_PORT)
  },

  async onComplete() {
    await stub?.close()
  },
}
