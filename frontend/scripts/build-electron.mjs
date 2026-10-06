import { rmSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import esbuild from 'esbuild'

// Bundles the Electron main + sandboxed preload with esbuild. Output is CommonJS
// (`.cjs`): the sandboxed preload must be CommonJS, and a `.cjs` extension keeps
// both files CommonJS regardless of package.json "type": "module".

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const watch = process.argv.includes('--watch')

const common = {
  bundle: true,
  platform: 'node',
  target: 'node22',
  format: 'cjs',
  sourcemap: false,
  logLevel: 'info',
  // Electron is provided by the runtime; everything else (incl. zod) is bundled.
  external: ['electron'],
}

const targets = [
  { entry: 'electron/main/index.ts', outfile: 'dist-electron/main/index.cjs' },
  { entry: 'electron/preload/index.ts', outfile: 'dist-electron/preload/index.cjs' },
]

rmSync(path.join(projectRoot, 'dist-electron'), { recursive: true, force: true })

const configs = targets.map((target) => ({
  ...common,
  entryPoints: [path.join(projectRoot, target.entry)],
  outfile: path.join(projectRoot, target.outfile),
}))

if (watch) {
  const contexts = await Promise.all(configs.map((config) => esbuild.context(config)))
  await Promise.all(contexts.map((context) => context.watch()))
  console.log('Watching Electron main/preload for changes…')
} else {
  await Promise.all(configs.map((config) => esbuild.build(config)))
  console.log('Electron main/preload bundled → dist-electron/')
}
