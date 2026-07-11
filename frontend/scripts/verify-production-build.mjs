import { existsSync, readdirSync, readFileSync } from 'node:fs'
import path from 'node:path'

const distDirectory = path.resolve('dist')
if (!existsSync(distDirectory)) throw new Error('Production build verification requires dist/.')

const files = readdirSync(distDirectory, { recursive: true }).filter(
  (entry) => typeof entry === 'string',
)
const normalized = files.map((entry) => entry.replaceAll('\\', '/'))
if (!normalized.includes('.vite/manifest.json')) throw new Error('Production build manifest is missing.')
if (normalized.some((entry) => entry.endsWith('.map'))) throw new Error('Production source maps must not be published.')
if (normalized.some((entry) => entry.endsWith('mockServiceWorker.js'))) {
  throw new Error('Production build must not contain the MSW service worker.')
}

const sensitiveMarkers = [
  'frontend-e2e-only',
  'employee-password',
  'replace-on-first-run',
  'Mocking enabled.',
]
for (const file of normalized.filter((entry) => /\.(?:html|js|css|json)$/u.test(entry))) {
  const source = readFileSync(path.join(distDirectory, file), 'utf8')
  for (const marker of sensitiveMarkers) {
    if (source.includes(marker)) throw new Error(`Production artifact contains forbidden marker in ${file}: ${marker}`)
  }
  if (source.includes('sourceMappingURL=')) throw new Error(`Production artifact references a source map: ${file}`)
  if (/https?:\/\/(?:127\.0\.0\.1|localhost):\d+/u.test(source)) {
    throw new Error(`Production artifact contains a loopback URL: ${file}`)
  }
}

const index = readFileSync(path.join(distDirectory, 'index.html'), 'utf8')
const assetReferences = [...index.matchAll(/(?:src|href)="([^"]+\.(?:js|css))"/gu)].map((match) => match[1])
if (assetReferences.length === 0 || assetReferences.some((entry) => !/-[A-Za-z0-9_-]{8,}\.(?:js|css)$/u.test(entry))) {
  throw new Error('Production JS/CSS assets must use content-hashed filenames.')
}
console.log('Production build check passed: hashed assets, no source maps, mocks, loopback URLs, or test credentials.')
