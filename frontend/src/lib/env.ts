import { z } from 'zod'

const booleanString = z.enum(['true', 'false']).default('false').transform((value) => value === 'true')
const envSchema = z.object({
  VITE_API_BASE_URL: z.string().trim().default('').refine(
    (value) => value === '' || value.startsWith('http://') || value.startsWith('https://'),
    'VITE_API_BASE_URL must be empty or an absolute HTTP(S) URL.',
  ),
  VITE_ENABLE_MSW: booleanString,
  VITE_REQUEST_TIMEOUT_MS: z.coerce.number().int().positive().max(120_000).default(10_000),
})

export type AppEnv = { apiBaseUrl: string; enableMsw: boolean; requestTimeoutMs: number }

function removeTrailingSlash(value: string): string {
  return value.endsWith('/') ? value.slice(0, -1) : value
}

export function parseEnv(source: Record<string, unknown>, production = false): AppEnv {
  const parsed = envSchema.parse(source)
  if (production && parsed.VITE_ENABLE_MSW) {
    throw new Error('MSW cannot be enabled in a production build.')
  }
  return {
    apiBaseUrl: removeTrailingSlash(parsed.VITE_API_BASE_URL),
    enableMsw: parsed.VITE_ENABLE_MSW,
    requestTimeoutMs: parsed.VITE_REQUEST_TIMEOUT_MS,
  }
}

export const env = parseEnv(import.meta.env, import.meta.env.PROD)
