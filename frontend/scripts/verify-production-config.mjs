import { existsSync, readFileSync } from 'node:fs'
import path from 'node:path'

const allowedKeys = new Set([
  'VITE_API_BASE_URL',
  'VITE_ENABLE_MSW',
  'VITE_REQUEST_TIMEOUT_MS',
])
const forbiddenKeyPattern = /(SECRET|TOKEN|PASSWORD|API_KEY|PRIVATE|CREDENTIAL)/iu

function parseEnvFile(file) {
  if (!existsSync(file)) return {}
  const entries = {}
  for (const rawLine of readFileSync(file, 'utf8').split(/\r?\n/u)) {
    const line = rawLine.trim()
    if (!line || line.startsWith('#')) continue
    const separator = line.indexOf('=')
    if (separator < 1) throw new Error(`Invalid environment line in ${file}: ${rawLine}`)
    entries[line.slice(0, separator).trim()] = line.slice(separator + 1).trim()
  }
  return entries
}

const source = {
  ...parseEnvFile(path.resolve('.env.production')),
  ...parseEnvFile(path.resolve('.env.production.local')),
  ...Object.fromEntries(Object.entries(process.env).filter(([key]) => key.startsWith('VITE_'))),
}
for (const key of Object.keys(source).filter((item) => item.startsWith('VITE_'))) {
  if (!allowedKeys.has(key)) throw new Error(`Unexpected client-exposed environment variable: ${key}`)
  if (forbiddenKeyPattern.test(key)) throw new Error(`Sensitive key must never use the VITE_ prefix: ${key}`)
}
if ((source.VITE_ENABLE_MSW ?? 'false').toLowerCase() !== 'false') {
  throw new Error('VITE_ENABLE_MSW must be false for production.')
}
const baseUrl = source.VITE_API_BASE_URL ?? ''
if (baseUrl) {
  const parsed = new URL(baseUrl)
  if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('VITE_API_BASE_URL must use HTTP(S).')
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error('VITE_API_BASE_URL must not contain credentials, query parameters, or fragments.')
  }
  if (parsed.pathname !== '/' && parsed.pathname !== '') {
    throw new Error('VITE_API_BASE_URL must be an origin without a path; same-origin empty value is recommended.')
  }
}
const timeout = Number(source.VITE_REQUEST_TIMEOUT_MS ?? '10000')
if (!Number.isInteger(timeout) || timeout <= 0 || timeout > 120000) {
  throw new Error('VITE_REQUEST_TIMEOUT_MS must be an integer between 1 and 120000.')
}
console.log('Production environment check passed.')
