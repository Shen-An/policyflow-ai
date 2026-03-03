import { existsSync, readdirSync, readFileSync } from 'node:fs'
import path from 'node:path'

const distDirectory = path.resolve('dist')
if (!existsSync(distDirectory)) {
  throw new Error('Production build verification requires dist/.')
}

const files = readdirSync(distDirectory, { recursive: true }).filter(
  (entry) => typeof entry === 'string',
)
if (files.some((entry) => entry.endsWith('mockServiceWorker.js'))) {
  throw new Error('Production build must not contain the MSW service worker.')
}
for (const file of files.filter((entry) => entry.endsWith('.js'))) {
  const source = readFileSync(path.join(distDirectory, file), 'utf8')
  if (source.includes('Mocking enabled.')) {
    throw new Error(`Production bundle contains development mock startup code: ${file}`)
  }
}
console.log('Production build check passed: MSW worker and mock startup are absent.')
